import itertools
import os
from enum import Enum
from pathlib import Path
from typing import List, Tuple, Optional, Any, Callable, Sequence, Iterable, BinaryIO
import numpy as np
from diplomat.processing import *
from multiprocessing import get_context, Queue
from multiprocessing.context import BaseContext
import time
import diplomat.utils.frame_store_api as frame_store_api


try:
    from ..fpe.frame_pass import FramePass, ProgressBar
    from ..fpe.frame_pass_loader import FramePassBuilder
    from ..fpe.sparse_storage import ForwardBackwardData, SparseTrackingData, ForwardBackwardFrame, AttributeDict
    from .growable_numpy_array import GrowableNumpyArray
    from ..fpe.frame_passes.fix_frame import FixFrame
except ImportError:
    __package__ = "diplomat.predictors.sfpe"
    from ..fpe.frame_pass import FramePass, ProgressBar
    from ..fpe.frame_pass_loader import FramePassBuilder
    from ..fpe.sparse_storage import ForwardBackwardData, SparseTrackingData, ForwardBackwardFrame, AttributeDict
    from .growable_numpy_array import GrowableNumpyArray
    from ..fpe.frame_passes.fix_frame import FixFrame


class NestedProgressIndicator(ProgressBar):

    DEFAULT_TICKS = 1000

    def __init__(self, progress_bar: ProgressBar, total: Optional[int] = None, ticks: int = DEFAULT_TICKS):
        self._runs = 0
        self._total = total
        self._sub_total = 0
        self._sub_current = 0

        self._current_prog_val = 0

        self._prog_bar = progress_bar
        self._ticks = ticks

        self._prog_bar.reset(self._total * self._ticks)

    def reset_runs_counter(self, total: int = 0):
        self._total = total
        self._prog_bar.reset(self._total * self._ticks)
        self._current_prog_val = 0
        self._runs = 0

    def update(self, amt: int = 1):
        self._sub_current += amt
        self._update_pbar()

    def _update_pbar(self):
        if(self._sub_current > self._sub_total):
            self._sub_current = self._sub_total
        val = self._runs * self._ticks + int((self._sub_current / (self._sub_total if(self._sub_total > 0) else 1)) * self._ticks)

        if(val > self._current_prog_val):
            self._prog_bar.update(val - self._current_prog_val)
            self._current_prog_val = val

    def inc_rerun_counter(self):
        self._runs += 1
        self.reset()

    def reset(self, total: Optional[int] = None):
        if(total is None):
            total = 0
        self._sub_total = total
        self._sub_current = 0
        self._update_pbar()

    def close(self):
        self._prog_bar.close()

    def message(self, message: str):
        self._prog_bar.message(message)


class InternalProgressIndicator(ProgressBar):
    # Max update rate in seconds...
    DEF_MAX_UPDATE_RATE = 0.25

    RERUNS = 0
    PROGRESS = 1
    TOTAL = 2
    IN_USE = 3

    def __init__(self, ctx: BaseContext, total: Optional[int] = None, max_refresh_rate: float = DEF_MAX_UPDATE_RATE):
        self._refresh_rate = max_refresh_rate
        self._last_update = time.monotonic() - self._refresh_rate
        self._prog_data = ctx.Array("Q", [0] * 4)
        self._internal_prog_data = [0] * 4
        self.reset(total)

    def inc_rerun_counter(self):
        self._internal_prog_data[self.RERUNS] += 1
        self._internal_prog_data[self.IN_USE] = False
        self.update_shared_mem(self._internal_prog_data)

    def mark_as_using(self):
        self._internal_prog_data[:] = self._prog_data[:]
        self._internal_prog_data[self.IN_USE] = True
        self.update_shared_mem(self._internal_prog_data)

    def reset(self, total: Optional[int] = None):
        self._internal_prog_data[self.TOTAL] = total if(total is not None) else 0
        self._internal_prog_data[self.PROGRESS] = 0
        self.rate_limit_update()

    def update(self, amt: int = 1):
        self._internal_prog_data[self.PROGRESS] = min(
            self._internal_prog_data[self.PROGRESS] + amt,
            self._internal_prog_data[self.TOTAL]
        )
        self.rate_limit_update()

    def rate_limit_update(self):
        new_time = time.monotonic()
        if(new_time - self._last_update > self._refresh_rate):
            self._last_update = new_time
            self.update_shared_mem(self._internal_prog_data)

    def update_shared_mem(self, data: list):
        self._prog_data[:] = data

    def get_prog_info(self) -> Tuple[int, int, int]:
        # (Number of reruns, current progress, total progress).
        self._internal_prog_data[:] = self._prog_data[:]
        return tuple(self._internal_prog_data)

    def close(self):
        # Does nothing....
        pass

    def message(self, message: str):
        # Does nothing...
        pass


def simple_pool_worker(queue: Queue, out_queue: Queue, max_iters: Optional[int], func: Callable, extra_args: Iterable[Any]):
    iterator = range(max_iters) if(max_iters is not None) else itertools.repeat(None)

    for __ in iterator:
        index, data = queue.get(True)

        if(index < 0): # Main process has asked that we kill ourselves....
            return

        result = func(*data, *extra_args)
        out_queue.put((index, result), True)


class SimplePool:
    def __init__(
        self,
        ctx: BaseContext,
        process_func: Callable,
        process_args: Sequence[Iterable[Any]],
        max_worker_reuse: Optional[int] = None,
        max_queue_size: int = 0,
    ):
        self._max_queue_size = min(max_queue_size, len(process_args)) if(max_queue_size > 0) else len(process_args)
        self._ctx = ctx

        self._input_queue = self._ctx.Queue(self._max_queue_size)
        self._output_queue = self._ctx.Queue(self._max_queue_size)
        self._args = process_args
        self._proc_func = process_func
        self._max_reuse = max_worker_reuse

        self._processes = [
            self._ctx.Process(
                target=simple_pool_worker,
                args=(self._input_queue, self._output_queue, max_worker_reuse, process_func, arg),
                daemon=True
            ) for arg in self._args
        ]

    def __enter__(self):
        for p in self._processes:
            p.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Kill the workers...
        self._input_queue.close()
        self._output_queue.close()

        for p in self._processes:
            if(p.is_alive()):
                p.terminate()
                p.join()
            p.close()


    def fast_map(self, getter: Callable[[int], Iterable[Any]], setter: Callable[[int, Any], None], length: int, update: Callable):
        next_idx = 0
        waiting_for = set()

        while((len(waiting_for) > 0) or (next_idx < length)):
            for i, p in enumerate(self._processes):
                if(not p.is_alive()):
                    p.close()
                    del p
                    self._processes[i] = self._ctx.Process(
                        target=simple_pool_worker,
                        args=(self._input_queue, self._output_queue, self._max_reuse, self._proc_func, self._args[i]),
                        daemon=True
                    )
                    self._processes[i].start()

            while((next_idx < length) and (not self._input_queue.full())):
                self._input_queue.put_nowait((next_idx, getter(next_idx)))
                waiting_for.add(next_idx)
                next_idx += 1
            update()
            while(not self._output_queue.empty()):
                finished_idx, res = self._output_queue.get_nowait()
                setter(finished_idx, res)
                waiting_for.remove(finished_idx)


def pool_with_progress_thread(func: Callable, args: List[Any], prog_bar: InternalProgressIndicator):
    # Mark this thread as being used, and then run the code...
    prog_bar.mark_as_using()
    result = func(*args, prog_bar)

    # Once a progress step is done, increment the number of runs counter, and mark this thread as unused...
    prog_bar.inc_rerun_counter()
    return result


class PoolWithProgress:
    DEF_MAX_REFRESH_RATE = 0.25
    DEF_SUB_TICKS = 1000
    CHUNK_MULTIPLIER = 2

    def __init__(
        self,
        progress_bar: ProgressBar,
        process_count: int = os.cpu_count(),
        refresh_rate_seconds: float = DEF_MAX_REFRESH_RATE,
        sub_ticks: int = DEF_SUB_TICKS,
        max_worker_reuse: Optional[int] = None,
        max_queue_size: int = 0
    ):
        self._progress_bar = progress_bar
        self._ctx = self.get_optimal_ctx()

        self._sub_progress_bars = [InternalProgressIndicator(self._ctx) for __ in range(process_count)]

        self._process_count = process_count
        self._pool = SimplePool(self._ctx, pool_with_progress_thread, [(p,) for p in self._sub_progress_bars], max_worker_reuse, max_queue_size)
        self._refresh_rate = refresh_rate_seconds
        self._sub_ticks = int(sub_ticks)

    @staticmethod
    def get_optimal_ctx():
        try:
            return get_context("forkserver")
        except ValueError:
            return get_context("spawn")

    def fast_map(self, do_work: Callable, getter: Callable[[int], Iterable[Any]], setter: Callable[[int, Any], None], total: int) -> None:
        self._progress_bar.reset(total * self._sub_ticks)
        current_value = 0
        last_update = time.monotonic()

        def full_getter(i):
            return (do_work, getter(i))

        def update():
            nonlocal current_value
            nonlocal last_update
            time_now = time.monotonic()

            if(time_now - last_update < self._refresh_rate):
                return
            last_update = time_now

            totals = np.sum(np.array([bar.get_prog_info() for bar in self._sub_progress_bars]), axis=0)

            new_progress_val = totals[0] * self._sub_ticks + ((totals[1] * totals[3] * self._sub_ticks) // (totals[2] if (totals[2] > 0) else 1))

            if (current_value < new_progress_val):
                self._progress_bar.update(new_progress_val - current_value)
            else:
                return

            current_value = new_progress_val

        self._pool.fast_map(full_getter, setter, total, update)
        self._progress_bar.update((total * self._sub_ticks) - current_value)
        return

    def __enter__(self) -> "PoolWithProgress":
        self._pool = self._pool.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._pool.__exit__(exc_type, exc_val, exc_tb)


class AntiClosePool:
    """
    Wrap a pool, so it can be used in with statements without closing it...
    """
    def __init__(self, pool):
        self._pool = pool

    def __getattr__(self, item):
        return self._pool.__getattribute__(item)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

class SegmentedFramePassEngine(Predictor):
    """
    A predictor that applies a collection of frame passes to the frames
    dumped by deeplabcut, and then predicts poses by selecting maximums.
    Contains a collection of useful prediction algorithms which can be listed
    by calling "get_predictor_settings" on this Predictor. This version
    applies passes in segments, and then stitches those segments together.
    """

    def __init__(
        self,
        bodyparts: List[str],
        num_outputs: int,
        num_frames: int,
        settings: Config,
        video_metadata: Config,
    ):
        super().__init__(
            bodyparts, num_outputs, num_frames, settings, video_metadata
        )

        self._num_bp = len(bodyparts)
        self._num_total_bp = self._num_bp * num_outputs

        self._width, self._height = None, None

        self.FULL_PASSES = FramePassBuilder.sanitize_pass_config_list(settings.full_passes)
        self.SEGMENTED_PASSES = FramePassBuilder.sanitize_pass_config_list(settings.segmented_passes)
        self.THRESHOLD = settings.threshold

        p = settings.export_frame_path
        self.EXPORT_LOC = Path(p).resolve() if(p is not None) else None

        self._frame_holder = ForwardBackwardData(num_frames, self._num_total_bp)

        self._frame_holder.metadata.threshold = self.THRESHOLD
        self._frame_holder.metadata.bodyparts = bodyparts
        self._frame_holder.metadata.num_outputs = num_outputs

        self._segments = None
        self._segment_scores = None
        self._segment_bp_order = None

        self._current_frame = 0

    def _sparcify_and_store(self, fb_frame: ForwardBackwardFrame, scmap: TrackingData, frame_idx: int, bp_idx: int):
        fb_frame.orig_data = SparseTrackingData.sparsify(scmap, frame_idx, bp_idx, self.THRESHOLD)
        fb_frame.src_data = fb_frame.orig_data

    def on_frames(self, scmap: TrackingData) -> Optional[Pose]:
        if(self._width is None):
            self._width = scmap.get_frame_width()
            self._height = scmap.get_frame_height()
            self._frame_holder.metadata.down_scaling = scmap.get_down_scaling()
            self._frame_holder.metadata.width = scmap.get_frame_width()
            self._frame_holder.metadata.height = scmap.get_frame_height()

        # Store sparsified frames for passes done later.
        for f_idx in range(scmap.get_frame_count()):
            for bp_idx in range(self._num_total_bp):
                self._sparcify_and_store(
                    self._frame_holder.frames[self._current_frame][bp_idx],
                    scmap,
                    f_idx,
                    bp_idx // self.num_outputs
                )

            self._current_frame += 1

        # No frames to return yet!
        return None

    @classmethod
    def get_maximum(
        cls,
        frame: ForwardBackwardFrame,
        relaxed_radius: float = 0
    ) -> Tuple[float, float, float, float, float]:
        """
        PRIVATE: Get the maximum location of a single forward backward frame.
        Returns a tuple containing the values x, y, probability, x offset,
        and y offset in order.
        """
        if (frame.frame_probs is None or frame.src_data.unpack()[0] is None):
            # No frame data, return 3 for no probability and 0 probability...
            return (-1, -1, 0, 0, 0)
        else:
            # Get the max location in the frame....
            y_coords, x_coords, orig_probs, x_offsets, y_offsets = frame.src_data.unpack()

            max_loc = np.argmax(frame.frame_probs)
            m_y, m_x, m_p = y_coords[max_loc], x_coords[max_loc], frame.frame_probs[max_loc]
            m_offx, m_offy = x_offsets[max_loc], y_offsets[max_loc]

            # Get the max location on the occluded state...
            try:
                max_occluded_loc = np.argmax(frame.occluded_probs)
                m_occluded_prob = frame.occluded_probs[max_occluded_loc]
                m_occ_x, m_occ_y = frame.occluded_coords[max_occluded_loc]
            except (ValueError, TypeError):
                m_occluded_prob = -np.inf
                m_occ_x, m_occ_y = 0, 0

            max_select = np.array([m_p, m_occluded_prob])
            max_of_max = np.argmax(max_select)

            if (max_of_max > 0):
                # Return correct location for occluded, but return a
                # probability of 0.
                return (m_occ_x, m_occ_y, 0, 0, 0)
            else:
                if (relaxed_radius <= 0):
                    # If no relaxed radius, just set pose...
                    return (m_x, m_y, m_p, m_offx, m_offy)
                else:
                    # Now find locations within the radius...
                    dists = np.sqrt(
                        (x_coords - m_x) ** 2 + (y_coords - m_y) ** 2)
                    res = np.flatnonzero(dists <= relaxed_radius)

                    # No other neighbors, return initially suggested value...
                    if (len(res) <= 0):
                        return (m_x, m_y, m_p, m_offx, m_offy)
                    else:
                        best_idx = res[np.argmax(orig_probs[res])]
                        return (
                            x_coords[best_idx], y_coords[best_idx], m_p,
                            x_offsets[best_idx], y_offsets[best_idx]
                        )

    @classmethod
    def get_maximums(
        cls,
        frame_list: ForwardBackwardData,
        segments: np.ndarray,
        segment_alignments: np.ndarray,
        progress_bar: ProgressBar,
        relaxed_radius: float = 0
    ) -> Pose:
        # Our final pose object:
        poses = Pose.empty_pose(frame_list.num_frames, frame_list.num_bodyparts)

        if(progress_bar is not None):
            progress_bar.reset(frame_list.num_frames)

        for seg, alignment in zip(segments, segment_alignments):
            start, end, fix = [int(elm) for elm in seg]

            for f_idx in range(start, end):
                for bp_idx in range(frame_list.num_bodyparts):
                    x, y, p, x_off, y_off = cls.get_maximum(
                        frame_list.frames[f_idx][bp_idx], relaxed_radius
                    )

                    poses.set_at(
                        f_idx, alignment[bp_idx], (x, y), (x_off, y_off), p,
                        frame_list.metadata.down_scaling
                    )

                if (progress_bar is not None):
                    progress_bar.update(1)

        return poses

    def _run_full_passes(self, progress_bar: Optional[ProgressBar]):
        for (i, frame_pass_builder) in enumerate(self.FULL_PASSES):
            frame_pass = frame_pass_builder(self._width, self._height, True)

            if(progress_bar is not None):
                progress_bar.message(
                    f"Running Full Frame Pass {i + 1}/{len(self.FULL_PASSES)}: '{frame_pass.get_name()}'"
                )

            self._frame_holder = frame_pass.run_pass(
                self._frame_holder,
                progress_bar,
                True
            )

        # In order to restore correctly, we need to restore to the state
        # right before segmentation.
        if(progress_bar is not None):
            progress_bar.message("Storing copy of source data...")
            progress_bar.reset(self._frame_holder.num_frames)

        for frm in self._frame_holder.frames:
            for bp in frm:
                bp.orig_data = bp.src_data.shallow_duplicate()

            if(progress_bar is not None):
                progress_bar.update()

    def _get_thread_count(self) -> int:
        return os.cpu_count() if(self.settings.thread_count is None) else self.settings.thread_count

    def _build_segments(self, progress_bar: Optional[ProgressBar], reset_bar: bool = True):
        # Compute the scores...
        if(reset_bar and progress_bar is not None):
            progress_bar.message("Breaking video into segments...")
            progress_bar.reset(self.num_frames)

        segment_size = self.settings.segment_size

        scores = FixFrame.compute_scores(
            self._frame_holder,
            progress_bar,
            thread_count=self._get_thread_count()
        )

        visited = np.zeros(len(scores), bool)
        ordered_scores = np.argsort(scores)[::-1]

        self._segments = GrowableNumpyArray(3, np.uint64)

        # We now iterate through the scores in sorted order, marking off segments...
        for frame_idx in ordered_scores:
            if(visited[frame_idx]):
                continue

            search_start = max(0, int(frame_idx - segment_size / 2))
            search_end = min(len(ordered_scores), int(frame_idx + segment_size / 2))
            section = slice(search_start, search_end)
            rev_section = slice(search_end - 1, search_start - 1 if(search_start > 0) else None, -1)

            start_idx = search_start + np.argmin(visited[section])
            end_idx = search_end - np.argmin(visited[rev_section])
            visited[section] = True

            # If the fix frame is missing body parts, resolve the segment by extending the nearest segment instead...
            if(np.isneginf(scores[frame_idx])):
                # We expect this to be very rare, so we iterate all the segments...
                print("Warning: Found bad segment, appending to another segment...")
                if(len(self._segments) == 0):
                    raise ValueError("This video has no frames where all body parts exist, can't run segmentation!")

                closest_idx = np.argmin(np.abs(self._segments.view[:, 2] - frame_idx))
                start, end, fixed = self._segments.view[closest_idx]
                self._segments.view[closest_idx] = [min(start, start_idx), max(end, end_idx), fixed]
                continue

            # Start of the segment, end of the segment, the fix frame index...
            self._segments.add([start_idx, end_idx, frame_idx])

            if(progress_bar is not None):
                progress_bar.update()

        # Sort the segments by the start of the segment...
        self._segments = self._segments.finalize()
        self._segment_scores = scores[self._segments[:, 2]]
        sort_order = self._segments[:, 2].argsort()
        self._segments = self._segments[sort_order]
        self._segment_scores = self._segment_scores[sort_order]

    @classmethod
    def _run_segment(
        cls,
        sub_frame: ForwardBackwardData,
        frame_pass_builders: List[FramePassBuilder],
        width: int,
        height: int,
        allow_multi_threading: bool,
        fix_frame_idx: int,
        progress_bar: Optional[ProgressBar] = None,
    ) -> ForwardBackwardData:
        if(progress_bar is not None):
            progress_bar.reset(sub_frame.num_frames)

        for f in range(sub_frame.num_frames):
            for bp in range(sub_frame.num_bodyparts):
                frame = sub_frame.frames[f][bp]
                frame.frame_probs = None
                frame.occluded_probs = None
                frame.occluded_coords = None

            if(progress_bar is not None):
                progress_bar.update()

        # Compute the fix frame....
        fix_frame = FixFrame.create_fix_frame(
            sub_frame,
            fix_frame_idx,
            sub_frame.metadata.skeleton if("skeleton" in sub_frame.metadata) else None
        )

        sub_frame = FixFrame.restore_all_except_fix_frame(
            sub_frame,
            fix_frame_idx,
            fix_frame,
            progress_bar,
            True
        )

        if (isinstance(progress_bar, NestedProgressIndicator)):
            progress_bar.inc_rerun_counter()

        for (i, frame_pass_builder) in enumerate(frame_pass_builders):
            frame_pass = frame_pass_builder(width, height, allow_multi_threading)

            sub_frame = frame_pass.run_pass(
                sub_frame,
                progress_bar,
                True
            )

            if(isinstance(progress_bar, NestedProgressIndicator)):
                progress_bar.inc_rerun_counter()

        return sub_frame

    def _get_segment(self, index: int):
        start, end, fix_frame = self._segments[index]

        sub_frame = ForwardBackwardData(0, 0)
        sub_frame.frames = self._frame_holder.frames[start:end]
        sub_frame.metadata = self._frame_holder.metadata

        return (sub_frame, self.SEGMENTED_PASSES, self._width, self._height, False, fix_frame - start)

    def _set_segment(self, index: int, frame_data: ForwardBackwardData):
        start, end, fix_frame = self._segments[index]
        self._frame_holder.frames[start:end] = frame_data.frames

    def _run_segmented_passes(
        self,
        progress_bar: ProgressBar,
        segment_idxs: Sequence[int],
    ):
        cls = type(self)
        thread_count = self._get_thread_count()
        total_segments = len(segment_idxs)

        self._frame_holder.allow_pickle = False

        if(thread_count <= 0):
            pass_count = (len(self.SEGMENTED_PASSES) + 1) * total_segments

            passes_can_use_pool = any(b.clazz.UTILIZE_GLOBAL_POOL for b in self.SEGMENTED_PASSES)
            allow_multithread = self.settings.allow_pass_multithreading

            wrapper_bar = NestedProgressIndicator(
                progress_bar,
                total=pass_count,
                ticks=int(self._frame_holder.num_frames / pass_count)
            )
            progress_bar.message("Running on Segments...")

            if(passes_can_use_pool and allow_multithread):
                with PoolWithProgress.get_optimal_ctx().Pool(processes=os.cpu_count()) as pool:
                    FramePass.GLOBAL_POOL = AntiClosePool(pool)
                    for idx in segment_idxs:
                        frm, segs, width, height, __, fix_frame_idx = self._get_segment(idx)
                        self._run_segment(frm, segs, width, height, self.settings.allow_pass_multithreading, fix_frame_idx, wrapper_bar)
            else:
                for idx in segment_idxs:
                    frm, segs, width, height, __, fix_frame_idx = self._get_segment(idx)
                    self._run_segment(frm, segs, width, height, self.settings.allow_pass_multithreading, fix_frame_idx, wrapper_bar)

            FramePass.GLOBAL_POOL = None
        else:
            with PoolWithProgress(progress_bar, thread_count, sub_ticks=int(self._frame_holder.num_frames / len(self._segments))) as pool:
                progress_bar.message("Running on Segments...")
                pool.fast_map(
                    cls._run_segment,
                    lambda i: self._get_segment(segment_idxs[i]),
                    lambda i, r: self._set_segment(segment_idxs[i], r),
                    total_segments
                )


    def _run_all_segmented_passes(
        self,
        progress_bar: ProgressBar,
        reset_bar: bool = True
    ):
        if(reset_bar and progress_bar is not None):
            progress_bar.message("Running Segmented Passes...")
            progress_bar.reset(len(self._segments))

        self._run_segmented_passes(progress_bar, range(len(self._segments)))

        if(progress_bar is not None):
            progress_bar.update()

    def _get_frame_links(
        self,
        current_frame: List[ForwardBackwardFrame],
        prior_frame: List[ForwardBackwardFrame],
        prior_frame_indexes: np.ndarray
    ) -> np.ndarray:
        num_groups = self._frame_holder.num_bodyparts // self.num_outputs
        num_in_group = self.num_outputs

        out_list = np.zeros(len(current_frame), np.uint16)

        for bp_group in range(num_groups):
            score_matrix = np.zeros((num_in_group, num_in_group), np.float32)

            for coff in range(num_in_group):
                for poff in range(num_in_group):
                    pidx = bp_group * num_in_group + poff
                    cidx = bp_group * num_in_group + coff

                    cx, cy, cp, cx_off, cy_off = self.get_maximum(
                        current_frame[cidx],
                        self.settings.relaxed_maximum_radius
                    )
                    px, py, pp, px_off, py_off = self.get_maximum(
                        prior_frame[pidx],
                        self.settings.relaxed_maximum_radius
                    )

                    d_scale = self._frame_holder.metadata.down_scaling

                    score_matrix[poff, coff] = FixFrame.dist(
                        (
                            px + px_off / d_scale,
                            py + py_off / d_scale
                        ),
                        (
                            cx + cx_off / d_scale,
                            cy + cy_off / d_scale
                        )
                    )

            for i in range(num_in_group):
                off = bp_group * num_in_group
                p_max_i, c_max_i = np.unravel_index(np.argmin(score_matrix), score_matrix.shape)
                score_matrix[p_max_i, :] = np.inf
                score_matrix[:, c_max_i] = np.inf

                out_list[off + c_max_i] = prior_frame_indexes[off + p_max_i]

        return out_list

    def _resolve_frame_orderings(
        self,
        progress_bar: ProgressBar,
        reset_bar: bool = True
    ):
        if(reset_bar and progress_bar is not None):
            progress_bar.message("Resolving Orderings...")
            progress_bar.reset(len(self._segments))

        self._segment_bp_order = np.zeros((len(self._segments), self._frame_holder.num_bodyparts), np.uint16)

        # Use the best scoring frame as the ground truth ordering...
        best_segment_idx = np.argmax(self._segment_scores)
        self._segment_bp_order[best_segment_idx] = np.arange(self._frame_holder.num_bodyparts)

        # Now we align orderings to the 'ground truth' ordering...
        for i in range(best_segment_idx - 1, -1, -1):
            # The end is exclusive...
            start, end, fix_frame = [int(elm) for elm in self._segments[i]]
            self._segment_bp_order[i, :] = self._get_frame_links(
                self._frame_holder.frames[end - 1],
                self._frame_holder.frames[end],
                self._segment_bp_order[i + 1]
            )

            if(progress_bar is not None):
                progress_bar.update()

        for i in range(best_segment_idx + 1, len(self._segments)):
            start, end, fix_frame = [int(elm) for elm in self._segments[i]]
            self._segment_bp_order[i, :] = self._get_frame_links(
                self._frame_holder.frames[start],
                self._frame_holder.frames[start - 1],
                self._segment_bp_order[i - 1]
            )

            if(progress_bar is not None):
                progress_bar.update()


    def _run_frame_passes(self, progress_bar: Optional[ProgressBar]):
        self._run_full_passes(progress_bar)
        self._build_segments(progress_bar)
        self._run_all_segmented_passes(progress_bar)
        self._resolve_frame_orderings(progress_bar)


    class ExportableFields(Enum):
        SOURCE = "Source"
        FRAME = "Frame"
        OCCLUDED_AND_EDGES = "Occluded/Edge"

    @classmethod
    def _get_frame_writer(
        cls,
        num_frames: int,
        frame_metadata: AttributeDict,
        video_metadata: Config,
        file_format: str,
        file: BinaryIO,
        export_all: bool = False
    ) -> frame_store_api.FrameWriter:
        """
        Get a frame writer that can export frames from the Forward Backward instance. Used internally to support frame
        export functionality.

        TODO
        """
        from diplomat.utils import frame_store_fmt, h5_frame_store_fmt

        exporters = {
            "DLFS": frame_store_fmt.DLFSWriter,
            "HDF5": h5_frame_store_fmt.DLH5FSWriter
        }

        if(export_all):
            bp_list = [
                f"{bp}_{track}_{map_type.value}"
                for bp in frame_metadata.bodyparts
                for track in range(frame_metadata.num_outputs)
                for map_type in cls.ExportableFields
            ]
        else:
            bp_list = [
                f"{bp}_{track}" for bp in frame_metadata.bodyparts for track in range(frame_metadata.num_outputs)
            ]

        header = frame_store_fmt.DLFSHeader(
            num_frames,
            (frame_metadata.height + 2) if(export_all) else frame_metadata.height,
            (frame_metadata.width + 2) if(export_all) else frame_metadata.width,
            video_metadata["fps"],
            frame_metadata.down_scaling,
            *video_metadata["size"],
            *((None, None) if (video_metadata["cropping-offset"] is None) else video_metadata["cropping-offset"]),
            bp_list
        )

        return exporters.get(file_format, frame_store_fmt.DLFSWriter)(file, header)

    @classmethod
    def _export_frame(
        cls,
        src_frame: ForwardBackwardFrame,
        dest_frame: TrackingData,
        dst_idx: Tuple[int, int],
        header: frame_store_api.DLFSHeader,
        selected_field: ExportableFields,
        padding: int = 0
    ):
        start, end = padding, -padding if(padding > 0) else None
        spc = padding * 2

        if(selected_field == cls.ExportableFields.SOURCE):
            res = src_frame.src_data.desparsify(
                header.frame_width - spc, header.frame_height - spc, header.stride
            )

            dest_frame.get_prob_table(*dst_idx)[start:end, start:end] = res.get_prob_table(0, 0)
        elif(selected_field == cls.ExportableFields.FRAME):
            data = src_frame.src_data.unpack()
            probs = src_frame.frame_probs
            if (probs is None):
                probs = np.zeros(len(data[2]), np.float32)

            res = SparseTrackingData()
            res.pack(*data[:2], probs, *data[3:])
            res = res.desparsify(header.frame_width - spc, header.frame_height - spc, header.stride)

            dest_frame.get_prob_table(*dst_idx)[start:end, start:end] = res.get_prob_table(0, 0)
        elif(selected_field == cls.ExportableFields.OCCLUDED_AND_EDGES):
            if(padding < 1):
                raise ValueError("Padding must be included to export edges and the occluded state!")

            probs = src_frame.occluded_probs

            o_x, o_y = tuple(src_frame.occluded_coords.T)
            x, y = o_x + 1, o_y + 1
            off_x = off_y = np.zeros(len(x), dtype=np.float32)

            res = SparseTrackingData()
            res.pack(y, x, probs, off_x, off_y)

            # Add 2 to resolve additional padding as needed for the edges...
            res = res.desparsify(header.frame_width - spc + 2, header.frame_height - spc + 2, header.stride)

            new_start = start - 1
            new_end = end + 1 if(end + 1 < 0) else None

            dest_frame.get_prob_table(*dst_idx)[new_start:new_end, new_start:new_end] = res.get_prob_table(0, 0)

    @classmethod
    def _export_frames(
        cls,
        frames: ForwardBackwardData,
        segments: np.ndarray,
        segment_alignments: np.ndarray,
        video_metadata: Config,
        path: Path,
        file_format: str,
        p_bar: Optional[ProgressBar] = None,
        export_final_probs: bool = True,
        export_all: bool = False
    ):
        """
        Private method, exports frames if the user specifies a frame export path.

        TODO
        """
        if(p_bar is not None):
            p_bar.reset(frames.num_frames)

        with path.open("wb") as f:
            with cls._get_frame_writer(
                    frames.num_frames, frames.metadata, video_metadata, file_format, f, export_all
            ) as fw:
                header = fw.get_header()

                for (s, e, ff), alignment in zip(segments, segment_alignments):
                    for f_idx in range(s, e):
                        frame_data = TrackingData.empty_tracking_data(
                            1,
                            frames.num_bodyparts * (3 if(export_all) else 1),
                            header.frame_width,
                            header.frame_height,
                            frames.metadata.down_scaling,
                            True
                        )

                        for bp_idx in range(frames.num_bodyparts):
                            if(export_final_probs and frames.frames[f_idx][bp_idx].frame_probs is None):
                                continue

                            if(export_all):
                                for exp_t_i, exp_t in enumerate(cls.ExportableFields):
                                    cls._export_frame(
                                        frames.frames[f_idx][bp_idx],
                                        frame_data,
                                        (0, int(alignment[bp_idx]) * len(cls.ExportableFields) + exp_t_i),
                                        header,
                                        exp_t,
                                        padding=1
                                    )
                            else:
                                # No padding...
                                cls._export_frame(
                                    frames.frames[f_idx][bp_idx],
                                    frame_data,
                                    (0, int(alignment[bp_idx])),
                                    header,
                                    cls.ExportableFields.FRAME if(export_final_probs) else cls.ExportableFields.SOURCE
                                )

                        fw.write_data(frame_data)

                        if(p_bar is not None):
                            p_bar.update()


    def on_end(self, progress_bar: ProgressBar) -> Optional[Pose]:
        self._run_frame_passes(progress_bar)

        if(self.EXPORT_LOC is not None):
            progress_bar.message(f"Exporting Frames to: '{str(self.EXPORT_LOC)}'")
            self._export_frames(
                self._frame_holder,
                self._segments,
                self._segment_bp_order,
                self.video_metadata,
                self.EXPORT_LOC,
                "DLFS",
                progress_bar,
                self.settings.export_final_probs,
                self.settings.export_all_info
            )

        progress_bar.message("Selecting Maximums")
        return self.get_maximums(
            self._frame_holder,
            self._segments,
            self._segment_bp_order,
            progress_bar,
            relaxed_radius=self.settings.relaxed_maximum_radius
        )


    @classmethod
    def get_settings(cls) -> ConfigSpec:
        desc_lst = []

        for fp in FramePass.get_subclasses():
            desc_lst.append(f"Pass '{fp.get_name()}' Settings: [[[")
            options = fp.get_config_options()
            if(options is None):
                desc_lst.append("     No settings available...")
            else:
                for name, (def_val, caster, desc) in options.items():
                    desc_lst.append(f"     Setting Name: '{name}':")
                    desc_lst.append(f"     Default Value: {def_val}")
                    desc_lst.append(f"     Value Type: {caster}")
                    desc_lst.append(f"     Description:\n           {desc}\n")

            desc_lst.append("]]]\n")

        desc_str = "\n".join(desc_lst)

        return {
            "threshold": (
                0.001,
                type_casters.RangedFloat(0, 1),
                "The minimum floating point value a pixel within the probability frame must have "
                "in order to be kept and added to the sparse matrix."
            ),
            "full_passes": (
                [
                    "ClusterFrames",
                    "OptimizeStandardDeviation"
                ],
                type_casters.Sequence(
                    type_casters.Union(
                        type_casters.Tuple(str, dict),
                        type_casters.Tuple(str),
                        str
                    )
                ),
                "The passes to be run on the full list of frames, before segmentation occurs."
                "A list of lists containing a string (the pass name) and a dictionary (the configuration for "
                f"the provided plugin). If no configuration is provided, the entry can just be a string. "
                f"the following plugins are currently supported:\n\n{desc_str}"
            ),
            "segmented_passes": (
                [
                    "MITViterbi"
                ],
                type_casters.Sequence(
                    type_casters.Union(
                        type_casters.Tuple(str, dict),
                        type_casters.Tuple(str),
                        str
                    )
                ),
                "The passes to be run on partial lists of frames, after segmentation occurs."
                "A list of lists containing a string (the pass name) and a dictionary (the configuration for "
                f"the provided plugin). If no configuration is provided, the entry can just be a string. "
                f"the following plugins are currently supported:\n\n{desc_str}"
            ),
            "thread_count": (
                None,
                type_casters.Union(type_casters.Literal(None), type_casters.RangedInteger(0, np.inf)),
                "The number of threads to use when running segmented passes. "
                "Defaults to None, which resolves to os.cpu_count() at runtime."
                "If set to 0, disables multithreading..."
            ),
            "allow_pass_multithreading": (
                True,
                bool,
                "Whether or not to allow frame passes to utilize multithreading. Defaults to True."
            ),
            "segment_size": (
                200,
                type_casters.RangedInteger(10, np.inf),
                "The size of the segments in frames to break the video into for tracking."
            ),
            "export_frame_path": (
                None,
                type_casters.Union(type_casters.Literal(None), str),
                "A string or None specifying where to save the post forward backward frames to."
                "If None, does not save the frames to a file. Used for debugging."
            ),
            "export_final_probs": (
                True,
                bool,
                "If true exports the final probabilities as stored in frame_probs. "
                "Otherwise exports the probabilities from src_data. This setting is internal "
                "and for debugging. Defaults to true."
            ),
            "export_all_info": (
                False,
                bool,
                "If true exports all information, both final/pre-fpe probabilities and "
                "the occluded and edge states. This allows for display of several states at once. "
                "Only works if export_frame_path is set, and overrides export_final_probs."
            ),
            "relaxed_maximum_radius": (
                1.8,
                type_casters.RangedFloat(0, np.inf),
                "Determines the radius of relaxed maximum selection."
                "Set to 0 to disable relaxed maximum selection. This value is "
                "measured in cell units, not video units."
            )
        }

    @classmethod
    def get_tests(cls) -> Optional[List[TestFunction]]:
        return [
            cls.test_plotting,
            cls.test_sparsification,
            cls.test_desparsification
        ]

    @classmethod
    def get_test_data(cls) -> TrackingData:
        # Make tracking data...
        track_data = TrackingData.empty_tracking_data(4, 1, 3, 3, 2)

        track_data.set_prob_table(0, 0, np.array([[0, 0, 0],
                                                  [0, 1, 0],
                                                  [0, 0, 0]]))
        track_data.set_prob_table(1, 0, np.array([[0, 1.0, 0],
                                                  [0, 0.5, 0],
                                                  [0, 0.0, 0]]))
        track_data.set_prob_table(2, 0, np.array([[1, 0.5, 0],
                                                  [0, 0.0, 0],
                                                  [0, 0.0, 0]]))
        track_data.set_prob_table(3, 0, np.array([[0.5, 0, 0],
                                                  [1.0, 0, 0],
                                                  [0.0, 0, 0]]))

        return track_data

    @classmethod
    def get_test_instance(cls, track_data: TrackingData, settings: dict = None, num_out: int = 1) -> Predictor:
        return cls(
            [f"part{i + 1}" for i in range(track_data.get_bodypart_count())],
            num_out,
            track_data.get_frame_count(),
            Config(settings, cls.get_settings()),
            Config()
        )

    @classmethod
    def test_plotting(cls) -> Tuple[bool, str, str]:
        # TODO: Need to update for segmentation...
        raise NotImplementedError("TODO: Implement!")

        track_data = cls.get_test_data()
        predictor = cls.get_test_instance(track_data, {"passes": [
            "ClusterFrames",
            "FixFrame",
            ["MITViterbi", {"standard_deviation": 5}]
        ]})

        # Probabilities can change quite easily by even very minute changes to the algorithm, so we don't care about
        # them, just the predicted locations of things...
        expected_result = np.array([[3, 3], [3, 1], [1, 1], [1, 3]])

        # Pass it data...
        predictor.on_frames(track_data)

        # Check output
        poses = predictor.on_end(TQDMProgressBar(total=4)).get_all()

        if (np.allclose(poses[:, :2], expected_result)):
            return (True, "\n" + str(expected_result), "\n" + str(np.array(poses[:, :2])))
        else:
            return (False, "\n" + str(expected_result), "\n" + str(np.array(poses[:, :2])))

    @classmethod
    def test_sparsification(cls) -> Tuple[bool, str, str]:
        # Make tracking data...
        track_data = cls.get_test_data()
        predictor = cls.get_test_instance(track_data)

        # Pass it data...
        predictor.on_frames(track_data)

        # Check output
        predictor.on_end(TQDMProgressBar(total=4))

        fb_frames = [data[0].frame_probs for data in predictor._frame_holder.frames]
        orig_frames = [data[0].orig_data for data in predictor._frame_holder.frames]

        if ((None in orig_frames) or np.any([(f is None) for f in fb_frames])):
            return (False, str((fb_frames, orig_frames)), "No None Entries...")
        else:
            return (True, str((fb_frames, orig_frames)), "No None Entries...")

    @classmethod
    def test_desparsification(cls) -> Tuple[bool, str, str]:
        # Make tracking data...
        track_data = cls.get_test_data()
        orig_frame = track_data.get_prob_table(0, 0)
        result_frame = (
            SparseTrackingData.sparsify(track_data, 0, 0, 0.001)
                .desparsify(orig_frame.shape[1], orig_frame.shape[0], 8).get_prob_table(0, 0)
        )

        return (np.allclose(result_frame, orig_frame), str(orig_frame) + "\n", str(result_frame) + "\n")

    def __reduce__(self, *args, **kwargs):
        raise ValueError("Not allowed to pickle this class!")

    @classmethod
    def supports_multi_output(cls) -> bool:
        return True