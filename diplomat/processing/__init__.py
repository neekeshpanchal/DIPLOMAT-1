"""
Contains Abstract Base Class for all predictor plugins, which when provided probability frames from the neural net,
figure out where the points should be in the image. They are executed when deeplabcut.analyze_videos is run with
"predictor" argument set to a valid plugin name...
"""
# Used for type hints
from typing import Type, Set
# Used by get_predictor for loading plugins
from diplomat.utils import pluginloader
from diplomat import predictors

# Imports for other stuff in this module...
try:
    from .predictor import Predictor, TestFunction
    from .track_data import TrackingData
    from .progress_bar import ProgressBar, TQDMProgressBar
    from .pose import Pose
    from . import type_casters
    from .type_casters import TypeCaster
    from .containers import Config, ConfigSpec
except ImportError:
    __module__ = "deeplabcut.pose_estimation_tensorflow.nnet.predictors"
    from .predictor import Predictor, TestFunction
    from .track_data import TrackingData
    from .progress_bar import ProgressBar, TQDMProgressBar
    from .pose import Pose
    from . import type_casters
    from .type_casters import TypeCaster
    from .containers import Config, ConfigSpec

__all__ = [
    "Predictor",
    "TrackingData",
    "ProgressBar",
    "TQDMProgressBar",
    "Pose",
    "type_casters",
    "TypeCaster",
    "Config",
    "ConfigSpec",
    "TestFunction"
]


def get_predictor(name: str) -> Type[Predictor]:
    """
    Get the predictor plugin by the specified name.

    :param name: The name of this plugin, should be a string
    :return: The plugin class that has a name that matches the specified name
    """
    # Load the plugins from the directory: "deeplabcut/pose_estimation_tensorflow/nnet/predictors"
    plugins = get_predictor_plugins()
    # Iterate the plugins until we find one with a matching name, otherwise throw a ValueError if we don't find one.
    for plugin in plugins:
        if plugin.get_name() == name:
            return plugin
    else:
        raise ValueError(
            f"Predictor plugin {name} does not exist, try another plugin name..."
        )


def get_predictor_plugins() -> Set[Type[Predictor]]:
    """
    Get and retrieve all predictor plugins currently available to the DeepLabCut implementation...

    :return: A Set of Predictors, being the all classes that extend the Predictor class currently loaded visible to
    the python interpreter.
    """
    return pluginloader.load_plugin_classes(predictors, Predictor)