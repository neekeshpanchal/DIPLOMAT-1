"""
Package includes the frame exporter plugin. This plugin exports DeepLabCut probability maps to a binary format that can
be passed back into DeepLabCut again to perform frame predictions later. This allows for a video to be run through
the neural network (expensive) on a headless server or supercomputer, and then run through a predictor with gui
feedback on a laptop or somewhere else.
"""
from pathlib import Path
from typing import List, BinaryIO, Optional
from diplomat.processing import *
from diplomat.utils.frame_store_fmt import DLFSWriter, DLFSHeader
from diplomat.utils.h5_frame_store_fmt import DLH5FSWriter


class FrameExporter(Predictor):
    """
    Exports DeepLabCut probability maps to a binary format that can be passed back into DeepLabCut again to perform
    frame predictions later. This allows for a video to be run through the neural network (expensive) on a headless
    server or supercomputer, and then run through a predictor with gui feedback on a laptop or somewhere else.
    """
    def __init__(
        self,
        bodyparts: List[str],
        num_outputs: int,
        num_frames: int,
        settings: Config,
        video_metadata: Config,
    ):
        super().__init__(bodyparts, num_outputs, num_frames, settings, video_metadata)

        bp_to_k = settings.bodyparts_to_keep
        if(bp_to_k is not None):
            self._bp_to_idx = {bp: index for index, bp in enumerate(bodyparts) if(bp in bp_to_k)}

            if(len(self._bp_to_idx) == 0):
                raise ValueError("0 body parts specified to be saved!")
        else:
            self._bp_to_idx = None

        self._crop_off = video_metadata["cropping-offset"]
        self._crop_off = (None, None) if (self._crop_off is None) else self._crop_off

        self._frame_writer = None
        # Making the output file...
        orig_h5_path = Path(video_metadata["h5-file-name"])
        vid_path = Path(video_metadata["orig-video-path"])
        self._out_file: BinaryIO = (
            orig_h5_path.parent / (vid_path.name + "~" + settings.filename_suffix + (".h5" if(settings.export_as_h5) else ".dlfs"))
        ).open("w+b")
        # Initialize the frame counter...
        self._current_frame = 0

    def on_frames(self, scmap: TrackingData) -> Optional[Pose]:
        # If we are just starting, write the header, body part names chunk, and magic for frame data chunk...
        s = self.settings

        if self._current_frame == 0:
            header = DLFSHeader(
                self.num_frames,
                scmap.get_frame_height(),
                scmap.get_frame_width(),
                self.video_metadata["fps"],
                scmap.get_down_scaling(),
                *self.video_metadata["size"],
                *self._crop_off,
                self.bodyparts if(self._bp_to_idx is None) else list(self._bp_to_idx.keys())
            )

            if(s.export_as_h5):
                self._frame_writer = DLH5FSWriter(
                    self._out_file,
                    header,
                    s.threshold if (s.sparsify) else None,
                )
            else:
                self._frame_writer = DLFSWriter(
                    self._out_file,
                    header,
                    s.threshold if (s.sparsify) else None,
                    s.compression_level
                )

        # Writing all of the frames in this batch...
        if(self._bp_to_idx is None):
            self._frame_writer.write_data(scmap)
        else:
            bp_idx = list(self._bp_to_idx.values())
            locref = scmap.get_offset_map()

            new_scmap = TrackingData(
                scmap.get_source_map()[:, :, :, bp_idx],
                None if (locref is None) else locref[:, :, :, bp_idx],
                scmap.get_down_scaling()
            )

            self._frame_writer.write_data(new_scmap)

        self._current_frame += scmap.get_frame_count()

        return scmap.get_poses_for(
            scmap.get_max_scmap_points(num_max=self.num_outputs)
        )

    def on_end(self, progress_bar: ProgressBar) -> Optional[Pose]:
        self._frame_writer.close()
        self._out_file.flush()
        self._out_file.close()
        return None

    @classmethod
    def get_settings(cls) -> ConfigSpec:
        return {
            "sparsify": (
                True,
                bool,
                "Specify whether to optimize and store the data in a sparse format when dumping frames."
            ),
            "threshold": (
                1e-7,
                type_casters.RangedFloat(0, 1),
                "The threshold used if sparsify is true. Any values which land below "
                "this threshold probability won't be included in the frame.",
            ),
            "compression_level": (
                6,
                type_casters.RangedInteger(0, 9),
                "Determines the z-lib compression level. Higher compression level "
                "means it takes longer to compress the data, while 0 is no compression. Note this "
                "only applies if the dlfs format is being used, the hdf5 format ignores this value."
            ),
            "export_as_h5": (
                False,
                bool,
                "Determines if the alternate hdf5 format is used instead of the default DLFS binary format."
            ),
            "filename_suffix": (
                "DATA",
                str,
                "A string, The suffix to place onto the end of the file name."
            ),
            "bodyparts_to_keep": (
                None,
                type_casters.Union(type_casters.Literal(None), type_casters.Sequence(str)),
                "A list of body parts to store. None means keep all the body parts."
            )
        }

    @classmethod
    def supports_multi_output(cls) -> bool:
        return True