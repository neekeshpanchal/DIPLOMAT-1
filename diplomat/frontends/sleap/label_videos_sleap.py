from pathlib import Path
from typing import TypeVar, Type, Tuple
import cv2
import sleap

import diplomat.processing.type_casters as tc
from diplomat.utils.cli_tools import extra_cli_args
from diplomat.processing import Config, TQDMProgressBar
from diplomat.utils.colormaps import iter_colormap
from diplomat.utils.shapes import shape_iterator, CV2DotShapeDrawer

from .visual_settings import FULL_VISUAL_SETTINGS
from .run_utils import (
    _paths_to_str,
    _to_diplomat_poses
)

def _to_cv2_color(color: Tuple[float, float, float, float]) -> Tuple[int, int, int, int]:
    r, g, b, a = [min(255, max(0, int(val * 256))) for val in color]
    return (b, g, r, a)

class EverythingSet:
    def __contains__(self, item):
        return True

@extra_cli_args(FULL_VISUAL_SETTINGS, auto_cast=False)
@tc.typecaster_function
def label_videos(
    config: tc.PathLike,
    videos: tc.Union[tc.List[tc.PathLike], tc.PathLike],
    body_parts_to_plot: tc.Optional[tc.List[str]] = None,
    video_extension: str = "mp4",
    **kwargs
):
    """
    Label videos created using the SLEAP frontend.

    :param config:
    :param videos:
    :param body_parts_to_plot:
    :param video_extension:
    :param kwargs:
    :return:
    """
    model = sleap.load_model(_paths_to_str(config))

    if(model is None):
        raise ValueError("Model passed was invalid!")

    videos = _paths_to_str(videos)
    videos = [videos] if(isinstance(videos, str)) else videos

    visual_settings = Config(kwargs, FULL_VISUAL_SETTINGS)

    for video in videos:
        _label_video_single(video, visual_settings, body_parts_to_plot, video_extension)


T = TypeVar("T")

def _create_manager(clazz: Type[T]) -> Type[T]:
    class cv2_context_manager(clazz):
        def __enter__(self):
            if(not self.isOpened()):
                self.release()
                raise IOError("Unable to open video capture...")
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.release()

        def read(self):
            if(not self.isOpened()):
                raise IOError("Video capture is not open.")
            return super().read()

        def write(self, frame):
            if (not self.isOpened()):
                raise IOError("Video writer is not open.")
            return super().write(frame)

    return cv2_context_manager

ContextVideoWriter = _create_manager(cv2.VideoWriter)
ContextVideoCapture = _create_manager(cv2.VideoCapture)


def _label_video_single(
    label_path: str,
    visual_settings: Config,
    body_parts_to_plot: tc.Optional[tc.List[str]],
    video_extension: str
):
    print(f"Labeling Video Associated with Labels '{label_path}'...")

    # Grab video and pose info from labels...
    labels = sleap.load_file(label_path)
    label_path = Path(label_path)
    num_outputs, poses, video, skeleton = _to_diplomat_poses(labels)
    video_extension = video_extension if(video_extension.startswith(".")) else f".{video_extension}"

    # Create the output path...
    output_path = label_path.parent / (label_path.stem + "_labeled" + video_extension)

    body_parts_to_plot = EverythingSet() if(body_parts_to_plot is None) else set(body_parts_to_plot)
    bp_names = [name for name in skeleton.node_names for _ in range(num_outputs)]

    print(f"Writing output to: '{output_path}'")

    with ContextVideoWriter(str(output_path), visual_settings.output_codec, getattr(video, "fps", 30), video.shape[1:3][::-1]) as writer:
        with TQDMProgressBar(total=poses.get_frame_count()) as p:
            for f_i in range(poses.get_frame_count()):
                frame = video.get_frame(f_i)[..., ::-1]
                overlay = frame.copy()

                colors = iter_colormap(visual_settings.colormap, poses.get_bodypart_count() // num_outputs)
                shapes = shape_iterator(visual_settings.shape_list, num_outputs)

                part_iter = zip(
                    bp_names,
                    poses.get_x_at(f_i, slice(None)),
                    poses.get_y_at(f_i, slice(None)),
                    poses.get_prob_at(f_i, slice(None)),
                    colors,
                    shapes
                )

                for (name, x, y, prob, color, shape) in part_iter:
                    if(name not in body_parts_to_plot):
                        continue

                    shape_drawer = CV2DotShapeDrawer(
                        overlay,
                        _to_cv2_color(tuple(color[:3]) + (1,)),
                        -1 if (prob > visual_settings.pcutoff) else visual_settings.line_thickness,
                        cv2.LINE_AA if (visual_settings.antialiasing) else None
                    )[shape]

                    if(prob > visual_settings.pcutoff or visual_settings.draw_hidden_tracks):
                        shape_drawer(int(x), int(y), int(visual_settings.dotsize))

                writer.write(cv2.addWeighted(
                    overlay, visual_settings.alphavalue, frame, 1 - visual_settings.alphavalue, 0
                ))
                p.update()




