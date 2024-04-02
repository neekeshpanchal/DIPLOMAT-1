from typing import Tuple, Any, Optional, MutableMapping
from typing_extensions import Protocol

from diplomat.predictors.fpe import fpe_math
from diplomat.predictors.fpe.arr_utils import find_peaks
from diplomat.predictors.fpe.sparse_storage import SparseTrackingData, ForwardBackwardFrame, ForwardBackwardData
from diplomat.wx_gui import labeler_lib

import numpy as np


class EditableFramePassEngine(Protocol):
    @property
    def changed_frames(self) -> MutableMapping[Tuple[int, int], ForwardBackwardFrame]:
        raise NotImplementedError

    @property
    def frame_data(self) -> ForwardBackwardData:
        raise NotImplementedError

    def video_to_scmap_coord(self, coord: Tuple[float, float, float]) -> Tuple[int, int, float, float, float]:
        pass

    def scmap_to_video_coord(
        self,
        x_scmap: float,
        y_scmap: float,
        prob: float,
        x_off: float,
        y_off: float,
        down_scaling: float
    ) -> Tuple[float, float, float]:
        pass

    def get_maximum_with_defaults(self, frame: ForwardBackwardFrame) -> Tuple[int, int, float, float, float]:
        pass


class Point(labeler_lib.PoseLabeler):
    """
    The manual labeling mode, sets probability map to exact location of the
    user click always.
    """
    def __init__(self, frame_engine: EditableFramePassEngine):
        super().__init__()
        self._frame_engine = frame_engine

    def predict_location(
        self,
        frame_idx: int,
        bp_idx: int,
        x: float,
        y: float,
        probability: float
    ) -> Tuple[Any, Tuple[float, float, float]]:
        meta = self._frame_engine.frame_data.metadata
        frame = self._frame_engine.frame_data.frames[frame_idx][bp_idx]

        if(x is None):
            #should we be returning this prob value or the probability value?
            x, y, prob = self._frame_engine.scmap_to_video_coord(
                *self._frame_engine.get_maximum_with_defaults(frame),
                meta.down_scaling
            )
            return ((frame_idx, bp_idx, x, y, 0), (x, y, 0))

        return ((frame_idx, bp_idx, x, y, probability), (x, y, probability))

    def pose_change(self, new_state: Any) -> Any:
        frm, bp, x, y, p = new_state
        changed_frames = self._frame_engine.changed_frames
        frames = self._frame_engine.frame_data.frames

        x, y, off_x, off_y, prob = self._frame_engine.video_to_scmap_coord((x, y, p))
        old_frame_data = frames[frm][bp]
        is_orig = False

        idx = (frm, bp)
        if (idx not in changed_frames):
            changed_frames[idx] = old_frame_data
            is_orig = True

        new_data = SparseTrackingData()
        if (prob > 0):
            new_data.pack(*[np.array([item]) for item in [y, x, prob, off_x, off_y]])

        new_frame = ForwardBackwardFrame()
        new_frame.orig_data = new_data
        new_frame.src_data = new_data
        new_frame.disable_occluded = True
        new_frame.ignore_clustering = True

        frames[frm][bp] = new_frame

        return (frm, bp, is_orig, old_frame_data)

    def undo(self, data: Any) -> Any:
        frames = self._frame_engine.frame_data.frames
        changed_frames = self._frame_engine.changed_frames
        frm, bp, is_orig, frame_data = data

        idx = (frm, bp)
        new_is_orig = False
        new_old_frame_data = frames[frm][bp]

        if (idx not in changed_frames):
            changed_frames[idx] = new_old_frame_data
            new_is_orig = True
        elif (is_orig):
            del changed_frames[idx]

        frames[frm][bp] = frame_data

        return (frm, bp, new_is_orig, new_old_frame_data)

    def redo(self, data: Any) -> Any:
        return self.undo(data)


class Approximate(labeler_lib.PoseLabeler):
    """
    Approximate labeling mode, adds a Gaussian centered around the user
    predicted location to generate a new frame. This makes results 'snap' to
    already existing probs when the user input is close enough the predictions.
    """

    # We set a hard limit on the number of allowed probabilities generated by this method in a frame for
    # performance reasons...
    CELL_LIMIT = 75

    def __init__(self, frame_engine: EditableFramePassEngine):
        super().__init__()
        self._frame_engine = frame_engine
        self._settings = labeler_lib.SettingCollection(
            user_input_strength = labeler_lib.Slider(500, 1000, 667),
            user_input_spread = labeler_lib.FloatSpin(0.5, None, 20, 1, 4)
        )
        self._cached_gaussian_std = None
        self._cached_gaussian = None

    def _make_gaussian(self, new_std: float):
        self._cached_gaussian_std = new_std
        meta = self._frame_engine.frame_data.metadata

        d_scale = meta.down_scaling
        std = self._cached_gaussian_std / d_scale
        two_std = min(
            np.ceil(self._cached_gaussian_std * 2),
            max(meta.width, meta.height)
        )

        eval_vals = np.arange(-two_std, two_std + 1)
        x, y = np.meshgrid(eval_vals, eval_vals)
        g = fpe_math.gaussian_formula(0, x, 0, y, std, 1, 0)

        # Filter to improve memory usage, and performance....
        good_loc = g > meta.threshold
        g = g[good_loc]
        x = x[good_loc]
        y = y[good_loc]

        self._cached_gaussian = (
            g.reshape(-1),
            np.asarray([x.reshape(-1), y.reshape(-1)], dtype=int)
        )

    @staticmethod
    def _absorb_frame_data(p1, c1, off1, p2, c2, off2):
        comb_c = np.concatenate([c1.T, c2.T])
        comb_p = np.concatenate([p1, p2])
        comb_off = np.concatenate([off1.T, off2.T])
        from_dlc = np.repeat([True, False], [len(p1), len(p2)])

        sort_idx = np.lexsort([comb_c[:, 1], comb_c[:, 0]])
        comb_c = comb_c[sort_idx]
        comb_p = comb_p[sort_idx]
        comb_off = comb_off[sort_idx]
        from_dlc = from_dlc[sort_idx]

        match_idx, = np.nonzero(np.all(comb_c[1:] == comb_c[:-1], axis=1))
        match_idx_after = match_idx + 1

        comb_p[match_idx_after] = comb_p[match_idx] + comb_p[match_idx_after]
        comb_off[match_idx_after] = comb_off[match_idx]

        return (
            np.delete(comb_p, from_dlc, axis=0),
            np.delete(comb_c, from_dlc, axis=0).T,
            np.delete(comb_off, from_dlc, axis=0).T
        )

    @staticmethod
    def _filter_cell_count(
        x: np.ndarray,
        y: np.ndarray,
        probs: np.ndarray,
        x_off: np.ndarray,
        y_off: np.ndarray,
        max_cell_count: int
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        top_k = np.argpartition(probs, -max_cell_count)[-max_cell_count:]
        return (x[top_k], y[top_k], probs[top_k], x_off[top_k], y_off[top_k])

    def predict_location(
        self,
        frame_idx: int,
        bp_idx: int,
        x: float,
        y: float,
        probability: float,
    ) -> Tuple[Any, Tuple[float, float, float]]:
        info = self._settings.get_values()
        user_amp = info.user_input_strength / 1000
        if((self._cached_gaussian is None) or (info.user_input_spread != self._cached_gaussian_std)):
            self._make_gaussian(info.user_input_spread)

        meta = self._frame_engine.frame_data.metadata
        modified_frames = self._frame_engine.changed_frames
        if((frame_idx, bp_idx) in modified_frames):
            frame = modified_frames[(frame_idx, bp_idx)]
        else:
            frame = self._frame_engine.frame_data.frames[frame_idx][bp_idx]

        if(x is None):
            x, y, prob = self._frame_engine.scmap_to_video_coord(
                *self._frame_engine.get_maximum_with_defaults(frame),
                meta.down_scaling
            )
            return ((frame_idx, bp_idx, None, (x, y)), (x, y, 0))

        xvid, yvid = x, y
        x, y, x_off, y_off, prob = self._frame_engine.video_to_scmap_coord((x, y, probability))
        gp, gc = self._cached_gaussian
        gc = gc + np.array([[x], [y]], dtype=int)

        good_locs = ((0 <= gc[0]) & (gc[0] < meta.width)) & ((0 <= gc[1]) & (gc[1] < meta.height))
        gc = gc[:, good_locs]
        gp = gp[good_locs]

        fy, fx, fp, foffx, foffy = [a if(a is not None) else np.array([]) for a in frame.orig_data.unpack()]
        final_p, (final_x, final_y), (final_off_x, final_off_y) = self._absorb_frame_data(
            fp * ((1 - user_amp) / np.max(fp)) if(len(fp) > 0) else fp,
            np.asarray([fx, fy]),
            np.asarray([foffx, foffy]),
            gp * user_amp,
            gc,
            np.asarray([
                xvid - (gc[0] * meta.down_scaling + meta.down_scaling * 0.5),
                yvid - (gc[1] * meta.down_scaling + meta.down_scaling * 0.5)
            ])
        )

        final_x, final_y, final_p, final_off_x, final_off_y = self._filter_cell_count(
            final_x,
            final_y,
            final_p,
            final_off_x,
            final_off_y,
            self.CELL_LIMIT
        )

        final_x = final_x.astype(np.int32)
        final_y = final_y.astype(np.int32)
        final_p /= np.max(final_p)

        sp = SparseTrackingData()
        sp.pack(final_y, final_x, final_p, final_off_x, final_off_y)
        temp_f = ForwardBackwardFrame(src_data=sp, frame_probs=final_p)

        x, y, prob = self._frame_engine.scmap_to_video_coord(
            *self._frame_engine.get_maximum_with_defaults(temp_f),
            meta.down_scaling
        )

        return ((frame_idx, bp_idx, temp_f, (x, y)), (x, y, prob))

    def pose_change(self, new_state: Any) -> Any:
        """
        Handles the change in pose by updating the frame data with the new state.

        This method is responsible for updating the frame data when there is a change in the pose. It checks if the
        frame and body part index combination (frm, bp) has already been modified. If not, it marks the original frame
        data for this combination as changed. Depending on whether a suggested frame is provided or not, it either
        creates new tracking data from the suggested frame or from the provided coordinates. This new data is then
        used to update the frame data for the given frame and body part index. The method returns a tuple containing
        the frame index, body part index, a flag indicating if the original data was modified, and the old frame data.

        Parameters:
        - new_state (Any): A tuple containing the frame index, body part index, an optional suggested frame, and
          coordinates. The suggested frame is None if not provided.

        Returns:
        - Tuple[Any, Any, bool, Any]: A tuple containing the frame index, body part index, a boolean indicating if the
          original data was modified (is_orig), and the old frame data before the change.
        """
        frm, bp, suggested_frame, coord = new_state
        changed_frames = self._frame_engine.changed_frames
        frames = self._frame_engine.frame_data.frames

        old_frame_data = frames[frm][bp]
        is_orig = False

        idx = (frm, bp)
        if (idx not in changed_frames):
            changed_frames[idx] = old_frame_data
            is_orig = True

        if(suggested_frame is None):
            new_data = SparseTrackingData()
            x, y, off_x, off_y, prob = self._frame_engine.video_to_scmap_coord(
                coord + (0,)
            )
            new_data.pack(*[np.array([item]) for item in [y, x, prob, off_x, off_y]])
        else:
            y, x, prob, x_offset, y_offset = suggested_frame.src_data.unpack()
            max_prob_idx = np.argmax(prob)
            new_data = SparseTrackingData()
            new_data.pack(*[np.array([item]) for item in [y[max_prob_idx], x[max_prob_idx], 1, x_offset[max_prob_idx], y_offset[max_prob_idx]]])

        new_frame = ForwardBackwardFrame()
        new_frame.orig_data = new_data
        new_frame.src_data = new_data
        new_frame.disable_occluded = True
        new_frame.ignore_clustering = True

        frames[frm][bp] = new_frame

        return (frm, bp, is_orig, old_frame_data)

    def undo(self, data: Any) -> Any:
        frames = self._frame_engine.frame_data.frames
        changed_frames = self._frame_engine.changed_frames
        frm, bp, is_orig, frame_data = data

        idx = (frm, bp)
        new_is_orig = False
        new_old_frame_data = frames[frm][bp]

        if (idx not in changed_frames):
            changed_frames[idx] = new_old_frame_data
            new_is_orig = True
        elif (is_orig):
            del changed_frames[idx]

        frames[frm][bp] = frame_data

        return (frm, bp, new_is_orig, new_old_frame_data)

    def redo(self, data: Any) -> Any:
        return self.undo(data)

    def get_settings(self) -> Optional[labeler_lib.SettingCollection]:
        return self._settings

    @classmethod
    def supports_multi_label(cls) -> bool:
        return True


class ApproximateSourceOnly(Approximate):
    @staticmethod
    def _absorb_frame_data(p1, c1, off1, p2, c2, off2):
        comb_c = np.concatenate([c1.T, c2.T])
        comb_p = np.concatenate([p1, p2])
        comb_off = np.concatenate([off1.T, off2.T])
        from_dlc = np.repeat([True, False], [len(p1), len(p2)])

        sort_idx = np.lexsort([comb_c[:, 1], comb_c[:, 0]])
        comb_c = comb_c[sort_idx]
        comb_p = comb_p[sort_idx]
        comb_off = comb_off[sort_idx]
        from_dlc = from_dlc[sort_idx]

        match_idx, = np.nonzero(np.all(comb_c[1:] == comb_c[:-1], axis=1))
        match_idx_after = match_idx + 1

        comb_p[match_idx] = comb_p[match_idx] + comb_p[match_idx_after]

        return (
            np.delete(comb_p, ~from_dlc, axis=0),
            np.delete(comb_c, ~from_dlc, axis=0).T,
            np.delete(comb_off, ~from_dlc, axis=0).T
        )

    @staticmethod
    def _filter_cell_count(
        x: np.ndarray,
        y: np.ndarray,
        probs: np.ndarray,
        x_off: np.ndarray,
        y_off: np.ndarray,
        max_cell_count: int
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return (x, y, probs, x_off, y_off)


class NearestPeakInSource(labeler_lib.PoseLabeler):
    """
    Nearest labeling mode, similar to approximate but always selects the nearest peak location in the source probability
    map, assigning it a fixed value, and all the other locations a lower value...
    """

    def __init__(self, frame_engine: EditableFramePassEngine):
        super().__init__()
        self._frame_engine = frame_engine
        self._settings = labeler_lib.SettingCollection(
            minimum_peak_value=labeler_lib.FloatSpin(0, 1, 0.05, 0.001, 4),
            selected_peak_value=labeler_lib.FloatSpin(0.5, 1, 0.95, 0.001, 4),
            unselected_peak_value=labeler_lib.FloatSpin(0, 0.5, 0.05, 0.001, 4)
        )

    def predict_location(
        self,
        frame_idx: int,
        bp_idx: int,
        x: float,
        y: float,
        probability: float
    ) -> Tuple[Any, Tuple[float, float, float]]:
        meta = self._frame_engine.frame_data.metadata
        config = self._settings.get_values()

        modified_frames = self._frame_engine.changed_frames
        if((frame_idx, bp_idx) in modified_frames):
            frame = modified_frames[(frame_idx, bp_idx)]
        else:
            frame = self._frame_engine.frame_data.frames[frame_idx][bp_idx]

        if(x is None):
            x, y, prob = self._frame_engine.scmap_to_video_coord(
                *self._frame_engine.get_maximum_with_defaults(frame),
                meta.down_scaling
            )
            return ((frame_idx, bp_idx, None, (x, y)), (x, y, 0))

        ys, xs, probs, off_xs, off_ys = frame.orig_data.unpack()

        peak_locs = find_peaks(xs, ys, probs, meta.width)
        peak_locs = peak_locs[probs[peak_locs] >= config.minimum_peak_value]
        print(peak_locs)
        if(len(peak_locs) <= 1):
            # No peaks, or only one peak, perform basically a no-op, return prior frame state...
            x, y, prob = self._frame_engine.scmap_to_video_coord(
                *self._frame_engine.get_maximum_with_defaults(frame),
                meta.down_scaling
            )
            return ((frame_idx, bp_idx, frame, (x, y)), (x, y, prob))

        def to_exact(_x, _y, _x_off, _y_off):
            return _x + 0.5 + (_x_off / meta.down_scaling), _y + 0.5 + (_y_off / meta.down_scaling)

        # Compute nearest location...
        xp, yp, pp, xp_off, yp_off = self._frame_engine.video_to_scmap_coord((x, y, probability))

        xp_ex, yp_ex = to_exact(xp, yp, xp_off, yp_off)
        x_ex, y_ex = to_exact(xs[peak_locs], ys[peak_locs], off_xs[peak_locs], off_ys[peak_locs])

        dists = (xp_ex - x_ex) ** 2 + (yp_ex - y_ex) ** 2
        nearest_idx = np.argmin(dists)

        # Compute belonging of every cell...
        owner_peak = np.argmin(
            ((np.expand_dims(xs, 1) - np.expand_dims(xs[peak_locs], 0)) ** 2)
            + ((np.expand_dims(ys, 1) - np.expand_dims(ys[peak_locs], 0)) ** 2),
            axis=-1
        )

        # Compute how much we need to scale each peak and it's neighbors by to get the configured weighting...
        multipliers = config.unselected_peak_value / probs[peak_locs]
        multipliers[nearest_idx] = config.selected_peak_value / probs[peak_locs[nearest_idx]]

        # Apply scaling to all peaks...
        probs = probs * multipliers[owner_peak]

        temp_f = ForwardBackwardFrame(
            src_data=SparseTrackingData().pack(ys, xs, probs, off_xs, off_ys), frame_probs=probs
        )

        x, y, prob = self._frame_engine.scmap_to_video_coord(
            *self._frame_engine.get_maximum_with_defaults(temp_f),
            meta.down_scaling
        )

        return ((frame_idx, bp_idx, temp_f, (x, y)), (x, y, prob))

    def pose_change(self, new_state: Any) -> Any:
        frm, bp, suggested_frame, coord = new_state
        changed_frames = self._frame_engine.changed_frames
        frames = self._frame_engine.frame_data.frames

        old_frame_data = frames[frm][bp]
        is_orig = False

        idx = (frm, bp)
        if(idx not in changed_frames):
            changed_frames[idx] = old_frame_data
            is_orig = True

        if(suggested_frame is None):
            new_data = SparseTrackingData()
            x, y, off_x, off_y, prob = self._frame_engine.video_to_scmap_coord(
                coord + (0,)
            )
            new_data.pack(*[np.array([item]) for item in [y, x, prob, off_x, off_y]])
        else:
            y, x, prob, x_offset, y_offset = suggested_frame.src_data.unpack()
            max_prob_idx = np.argmax(prob)
            new_data = SparseTrackingData()
            new_data.pack(*[np.array([item]) for item in [y[max_prob_idx], x[max_prob_idx], 1, x_offset[max_prob_idx], y_offset[max_prob_idx]]])

        new_frame = ForwardBackwardFrame()
        new_frame.orig_data = new_data
        new_frame.src_data = new_data
        new_frame.disable_occluded = True
        new_frame.ignore_clustering = True

        frames[frm][bp] = new_frame

        return (frm, bp, is_orig, old_frame_data)

    def undo(self, data: Any) -> Any:
        frames = self._frame_engine.frame_data.frames
        changed_frames = self._frame_engine.changed_frames
        frm, bp, is_orig, frame_data = data

        idx = (frm, bp)
        new_is_orig = False
        new_old_frame_data = frames[frm][bp]

        if (idx not in changed_frames):
            changed_frames[idx] = new_old_frame_data
            new_is_orig = True
        elif (is_orig):
            del changed_frames[idx]

        frames[frm][bp] = frame_data

        return (frm, bp, new_is_orig, new_old_frame_data)

    def redo(self, data: Any) -> Any:
        return self.undo(data)

    def get_settings(self) -> Optional[labeler_lib.SettingCollection]:
        return self._settings

    @classmethod
    def supports_multi_label(cls) -> bool:
        return True
