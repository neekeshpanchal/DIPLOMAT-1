from abc import ABC, abstractmethod
from typing import Optional, List, Callable, Tuple, TypeVar
from diplomat.processing.containers import Config, ConfigSpec
from diplomat.processing.track_data import TrackingData
from diplomat.processing.pose import Pose
from diplomat.processing.progress_bar import ProgressBar


TestFunction = Callable[[], Tuple[bool, str, str]]


class Predictor(ABC):
    """
    Base plugin class for all predictor plugins.

    Predictors accept TrackingData objects as they are generated by the network and are expected to return a single or
    several Pose objects providing the predicted locations of body parts in the original video...
    """

    def __init__(
        self,
        bodyparts: List[str],
        num_outputs: int,
        num_frames: int,
        settings: Config,
        video_metadata: Config,
    ):
        """
        Constructor for the predictor. Should be used by plugins to initialize key data structures and settings.

        :param bodyparts: The body parts for the dataset, a list of the string friendly names in order. Note that if in
                          multi-output mode, this will be a list of only the unique original body parts.
        :param num_outputs: The number of expected outputs for each body part model. Note that if this plugin doesn't
                            support multi output mode, this will always be 1. When returning poses, all the
                            outputs for a single body part should be side-by-side.
                                Ex: If the bodyparts=[Nose, Tail] and num_outputs=2, pose arrangement should be:
                                    [Nose1, Nose2, Tail1, Tail2]
        :param num_frames: The number of total frames this predictor will be processing.
        :param settings: The settings for this predictor plugin. Dictionary is a map of strings, or setting names
                         to values. The actual data within the dictionary depends on return provided by get_settings
                         and what settings the user has set in deeplabcut's config.yaml.
                         If `get_settings` for this predictor returns None, this method will pass None...
        :param video_metadata: The metadata information for this dlc instance. Most of these settings are primarily
                               useful to interactive plugins. Includes the keys:
                                    "fps": Original Video's frames per second
                                    "h5-file-name": The name of the original h5 file, and it's path, as a string.
                                    "orig-video-path": The file path and name of the video being analyzed, as a string.
                                                       this value may be None, meaning the video could not be found, and
                                                       user is processing frames via a .dlfs file.
                                    "duration": The duration of the video in seconds
                                    "size": The x and y dimensions of the original video.
                                    "cropping-offset": The (y, x) offset of the cropped box in the video. If there is
                                                       no cropping, this value is set to None. Width/Height of cropping
                                                       box can be inferred using tracking data width and height and
                                                       multiplying it by the stride.
                                    "dotsize": The radius of dots used when outputting predictions to a video, an
                                               integer.
                                    "colormap": The colormap used when plotting points to a video, a string representing
                                                a matplotlib colormap.
                                    "shape_list": A shape iterator, yields shape names in order of
                                                  (part 1 body 1, part 1 body 2, ..., part 2 body 1, ...). This specifies the shapes to draw each dot
                                                  as if displaying results in a UI.
                                    "alphavalue": The alpha value of the points when outputting predictions, a float
                                                  between 0 and 1.
                                    "pcutoff": The probability at which to display no point in the final plotted video
                                               if the point in the data falls below this threshold. A float between 0
                                               and 1.
                                    "line_thickness": The thickness of the outline to plot occluded predictions with, an integer.
                                    "skeleton": None or a list of tuples, specifying body parts to connect in a skeleton.
        """
        self.__bodyparts = [str(b) for b in bodyparts]
        self.__num_outputs = int(num_outputs)
        self.__num_frames = int(num_frames)
        self.__settings = settings
        self.__video_meta = video_metadata

    @property
    def bodyparts(self) -> List[str]:
        """
        Get the body part names for this predictor instance, a list of strings.

        :returns: A list of strings.
        """
        return self.__bodyparts

    @property
    def num_outputs(self) -> int:
        """
        Get the number of outputs for each body part for this predictor instance.

        :returns: An integer, the number of outputs for each body part type.
        """
        return self.__num_outputs

    @property
    def num_frames(self) -> int:
        """
        Get the number of frames this predictor will be run on.

        :returns: An integer, the total frame count.
        """
        return self.__num_frames

    @property
    def settings(self) -> Config:
        """
        Get the settings for this predictor plugin.

        :returns: A Config object, being the configuration settings for this
                  predictor plugin...
        """
        return self.__settings

    @property
    def video_metadata(self) -> Config:
        """
        Get the video metadata passed to this predictor plugin instance.

        :returns: A Config object, being the video metadata passed to this
                  predictor instance...
        """
        return self.__video_meta

    @abstractmethod
    def on_frames(self, scmap: TrackingData) -> Optional[Pose]:
        """
        Executed on every batch of frames in the video, plugins should process or store the probability map data and
        return the guessed max locations, or return None if it is storing the probability maps for post-processing.

        :param scmap: A TrackingData object, containing probability maps, offset maps, and all data and methods needed
                      to generate poses.

        :return: A Pose object representing a collection of predicted poses for frames and body parts, or None if
                 TrackingData objects need to be stored since this plugin requires post-processing.
        """
        pass

    @abstractmethod
    def on_end(self, progress_bar: ProgressBar) -> Optional[Pose]:
        """
        Executed once all frames have been run through. Should be used for post-processing. Useful if a plugin needs to
        store all the frames in order to make predictions.

        :param progress_bar: A progress bar, should be used to display post-processing progress, the default max value
                             of the progress bar is set to the number of frames left.
                             (Number of total frames minus the number of frames returned in 'on_frames')...
                             See ProgressBar class for API details...

        :return: A Pose object representing a collection of poses for frames and body parts, or None if all the
                 predictions were made and returned as Pose object in 'on_frames'.
        """
        pass

    @classmethod
    def get_name(cls) -> str:
        """
        Get the name of this predictor plugin, the name is used when selecting a predictor in the
        deeplabcut.analyze_videos method. Does not have to be overridden, and defaults to
        returning the name of the class.

        :return: The name of this plugin to be used to select it, as a string.
        """
        return cls.__name__.split(".")[-1]

    @classmethod
    def get_description(cls) -> str:
        """
        Get the description of this plugin, the equivalent of a doc-string for this plugin, it is displayed when
        user lists available plugins. Does not have to be overridden, and defaults to returning the sanitized docstring
        of this class.

        :return: The description/summary of this plugin as a string.
        """
        if cls.__doc__ is None:
            return "None"
        else:
            return " ".join([s.strip() for s in cls.__doc__.split("\n") if(s.strip() != "")])

    T = TypeVar("T")

    @classmethod
    def get_settings(cls) -> Optional[ConfigSpec]:
        """
        Get the configurable or available settings for this predictor plugin.

        :return: The settings that can be set for this plugin, in the form of a dictionary of tuples.
                 The dictionary key is the name of the setting as stored internally and also
                 specified by the user, and each tuple will contain the following 3 items in order:

                    - Setting Default Value: Any type, the default value to be assigned to this setting if it is not
                                             set explicitly in the DeepLabCut config by the user...
                    - Setting Type Caster: A function which accepts and returns a single value, converting the passed
                                           value into the desired type of the setting. Can also throw an exception to
                                           indicate rogue input.
                    - Setting Description: A String, A user-friendly description of the setting. Should include info
                                           on its default value, what it does, and what it should be set to.


                  If this predictor plugin has no configurable settings, this method should return None.
        """
        return None

    @classmethod
    def get_tests(cls) -> Optional[List[TestFunction]]:
        """
        Get the test methods for this plugin.

        :return: A list of callable objects(aka. methods) or None if no test methods exist. The callables in the list
                 should accept no arguments and return a tuple of 3 items, containing the below values in order:

                    - Test Success: A Boolean, True if test was successful, otherwise False.

                    - Test Expected Results: A string, a human-readable string representing the expected results of this
                                             test.
                    - Test Actual Results: A string, a human-readable string representing the actual results that
                                           the test method received. If test was successful, this should match the
                                           expected results value.
                 Another valid response from the test methods is to throw an exception, in which case the test is
                 considered a failure and the stack trace of the exception is printed instead of the expected/actual
                 results.

        """
        return None

    @classmethod
    def supports_multi_output(cls) -> bool:
        """
        Get whether this plugin supports outputting multiple of the same body part (num_outputs > 1). Returning
        false here will keep the plugin from being allowed to be used when num_outputs is greater than 1.

        :return: A boolean, True if multiple outputs per body part is supported, otherwise False...
        """
        return False
