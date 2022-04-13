from typing import Tuple, List, Optional, Union, Any
import wx
from diplomat.processing import *
from .labeler_lib import PoseLabeler, SettingCollectionWidget
from .video_player import VideoPlayer
import cv2
import matplotlib.pyplot as plt
from matplotlib.colors import Colormap
from wx.lib.newevent import NewCommandEvent
from wx.lib.scrolledpanel import ScrolledPanel

# Represents a cropping box, using x, y, width, height....
Box = Optional[Tuple[int, int, int, int]]
ofloat = Optional[float]

def _bounded_float(low: float, high: float):
    """
    PRIVATE: Returns a function which will check that a float lands between the provided low and high value, inclusive,
    and throws an error if they don't.
    """
    def convert(value: float):
        value = float(value)
        if(not (low <= value <= high)):
            raise ValueError(f"{value} is not between {low} and {high}!")
        return value

    return convert


class Initialisable:
    """
    Defines a class that needs to be initialized as soon as it is read in by python.
    """
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()
        cls.init_class()

    @classmethod
    def init_class(cls):
        raise NotImplementedError("Abstract method that must be initialized...")


class BasicDataFields(Initialisable):
    """
    PRIVATE: Allows for defining a list of basic data fields in your subclass with basic checker methods, and will
    automatically construct getters and setters for those values.

    METHOD_LIST = [(public name, private name, checker method), ...]
    """
    METHOD_LIST = []

    @classmethod
    def init_class(cls):
        for name, private_name, cast_method in cls.METHOD_LIST:
            cls._add_methods(name, private_name, cast_method)

    @classmethod
    def _add_methods(cls, name, private_name, cast_method):
        setattr(cls, f"get_{name}", lambda self: getattr(self, private_name))
        setattr(cls, f"set_{name}", lambda self, value: setattr(self, private_name, cast_method(value)))

    def __init__(self, *args, **kwargs):
        names = {public_name for public_name, __, __ in self.METHOD_LIST}

        for i in range(min(len(args), len(self.METHOD_LIST))):
            getattr(self, f"set_{self.METHOD_LIST[i][0]}")(args[i])

        # Keyword arguments will override positional arguments...
        for name, value in kwargs.items():
            if(name in names):
                getattr(self, f"set_{name}")(value)

# Represents a x, y coordinate.
Coord = Tuple[Optional[int], Optional[int]]

class PointViewNEdit(VideoPlayer, BasicDataFields):
    """
    An extension of the VideoPlayer widget, which is also capable of display body part locations and allowing the user
    to edit them. This is one of the two components which makes up the Point Editor....
    """

    DEF_MAP = None
    FAST_MODE_KEY = wx.WXK_CONTROL
    JUMP_BACK_KEY = wx.WXK_SHIFT
    JUMP_BACK_AMT = 10
    DEF_FAST_MODE_SPEED_FRACTION = 3
    JUMP_BACK_DELAY = 200
    METHOD_LIST = [
        ("colormap", "_colormap", plt.get_cmap),
        ("plot_threshold", "_plot_threshold", _bounded_float(0, 1)),
        ("point_radius", "_point_radius", int),
        ("point_alpha", "_point_alpha", _bounded_float(0, 1))
    ]

    # All events emitted by this class.
    PointChangeEvent, EVT_POINT_CHANGE = NewCommandEvent()  # The location of a point has been changed by the user.
    # Two below are used mostly for enable/disabling widgets while a user is changing points:
    PointInitEvent, EVT_POINT_INIT = NewCommandEvent()  # The user has begun changing points.
    PointEndEvent, EVT_POINT_END = NewCommandEvent()  # The user has finished changing points.

    def __init__(
        self,
        parent,
        video_hdl: cv2.VideoCapture,
        crop_box: Box,
        poses: Pose,
        colormap: Union[str, Colormap] = DEF_MAP,
        plot_threshold: float = 0.1,
        point_radius: int = 5,
        point_alpha: float = 0.7,
        ctrl_speed_divider = DEF_FAST_MODE_SPEED_FRACTION,
        w_id=wx.ID_ANY,
        pos=wx.DefaultPosition,
        size=wx.DefaultSize,
        style=wx.BORDER_DEFAULT,
        validator=wx.DefaultValidator,
        name="VideoPlayer"
    ):
        """
        Construct a new PointNViewEdit

        :param parent: The wx Control Parent.
        :param video_hdl: The cv2 VideoCapture to play video from. One should avoid never manipulate the video capture
                  once passed to this constructor, as the handle will be passed to another thread for fast
                  video loading.
        :param crop_box: A tuple of 4 integers being the x, y, width, and height of the cropped area of the video.
        :param poses: The Pose object for the above video, used as initial point data.
        :param colormap: The matplotlib colormap to use when coloring the points.
        :param plot_threshold: The probability threshold at which to not plot a points. Defaults to 0.1.
        :param point_radius: Determines the size of the points. Defaults to 5.
        :param point_alpha: Determines the alpha level of the points. Defaults to 0.7.
        :param ctrl_speed_divider: The initial slow down multiplier while labeling in fast mode. Defaults to 8.
        :param w_id: The wx ID.
        :param pos: The position of the widget.
        :param size: The size of the widget.
        :param style: The style of the widget.
        :param validator: The widgets validator.
        :param name: The name of the widget.
        """
        VideoPlayer.__init__(self, parent, w_id, video_hdl, crop_box, pos, size, style, validator, name)

        self._poses = poses
        self._colormap = None
        self._plot_threshold = None
        self._point_radius = None
        self._point_alpha = None
        self._step_counter = 0
        self._shift_delay = 0
        self._fast_m_speed = ctrl_speed_divider
        self._pose_label_modes = {}
        self._current_pose_labeling_mode = ""

        BasicDataFields.__init__(self, colormap, plot_threshold, point_radius, point_alpha)

        self._edit_point = None
        self._new_location = None
        self._old_location = None
        self._ctrl_mode = False
        self._pressed = False
        # Handle point changing events....
        self.Bind(wx.EVT_LEFT_DOWN, self.on_press)
        self.Bind(wx.EVT_MOTION, self.on_move)
        self.Bind(wx.EVT_LEFT_UP, self.on_release)
        self.Bind(wx.EVT_RIGHT_UP, self.on_right_click)

    def register_labeling_mode(self, pose_labeler: PoseLabeler):
        self._pose_label_modes[pose_labeler.get_display_name()] = pose_labeler

    def unregister_labeling_mode(self, labeler_name: str):
        del self._pose_label_modes[labeler_name]

    def set_labeling_mode(self, labeler_name: str):
        self._current_pose_labeling_mode = labeler_name

    def get_labeling_mode(self) -> str:
        return self._current_pose_labeling_mode

    def get_labeling_class(self, name: str) -> PoseLabeler:
        return self._pose_label_modes[name]

    def set_keyboard_listener(self, window: wx.Window):
        """
        Set the wx Window which will handle keyboard events for this Player.

        :param window: A wx.Window to attach a listener to.
        """
        window.Bind(wx.EVT_CHAR_HOOK, self.on_key_down)

    def _on_timer(self, event: wx.TimerEvent, __ = True):
        """
        PRIVATE: Executes per timer event.
        """
        if(self._ctrl_mode):
            self.fast_mode_move_frame()
        else:
            super()._on_timer(event)

    def fast_mode_move_frame(self):
        """
        PRIVATE: Triggered when moving from frame-to-frame in fast labeling mode, increments the frame and finalizes
                 the point location.
        """
        if((not self.is_frozen()) or (self._edit_point is None)):
            self._ctrl_mode = False
            return

        self._step_counter += 1

        if(wx.GetKeyState(self.FAST_MODE_KEY)):
            if (wx.GetKeyState(self.JUMP_BACK_KEY) and (self._shift_delay <= 0)):
                x, y = self._get_mouse_loc_video(self._get_mouse_no_evt())
                self._point_relocation(x, y)
                self._shift_delay = self.JUMP_BACK_DELAY
                self.unfreeze()
                self.move_back(min(self.get_offset_count(), self.JUMP_BACK_AMT))
                self.freeze()
                self._old_location = self._get_selected_bodypart()
                self._point_prediction(x, y)
            elif(self._step_counter >= self._fast_m_speed):
                self._step_counter = 0
                # Finalize the points location, and move to the next frame...
                x, y = self._get_mouse_loc_video(self._get_mouse_no_evt())
                self._point_relocation(x, y)
                self.unfreeze()
                if (self.get_offset_count() < (self.get_total_frames() - 1)):
                    self.move_forward()
                self.freeze()
                self._old_location = self._get_selected_bodypart()
                self._point_prediction(x, y)

            self._shift_delay = max(0, self._shift_delay - (1000 / self._fps))
            self.Refresh()
            self._core_timer.StartOnce(1000 / self._fps)
        else:
            self._on_key_up()

    def on_key_down(self, event: wx.KeyEvent):
        """
        PRIVATE: Triggered on key press event. Determines if we should enter fast labeling mode.
        """
        if(event.GetKeyCode() == self.FAST_MODE_KEY):
            if ((not self.is_playing()) and (self._edit_point is not None) and (not self._pressed)):
                if(not self._ctrl_mode):
                    self.freeze()
                    self._ctrl_mode = True
                    self._step_counter = 0
                    self._old_location = self._get_selected_bodypart()
                    self._push_point_init_event(self._old_location)
                    self._core_timer.StartOnce(1000 / self._fps)

        event.Skip()

    def set_ctrl_speed_divider(self, val: int):
        """
        Set the fast mode speed multiplier. The larger the slower.

        :param val: An integer between 0 and 1000, which is multiplied with the video frame rate to determine the
                    frame changing speed.
        """
        if(not (0 < val <= 1000)):
            raise ValueError("Speed Divider must be greater than 0 and less than or equal to 1000!")
        self._fast_m_speed = int(val)

    def get_ctrl_speed_divider(self) -> int:
        """
        Get the fast labeling mode speed multiplier. The higher the value the slower the playback.

        :returns: An integer between 0 and 1000, which is multiplied with the video frame rate to determine the
                    frame changing speed.
        """
        return self._fast_m_speed

    def _on_key_up(self):
        """
        PRIVATE: Triggered when the fast mode key "CTRL" is released.
        """
        x, y = self._get_mouse_loc_video(self._get_mouse_no_evt())
        self._point_relocation(x, y)
        self._push_point_end_event()
        self._ctrl_mode = False
        self.unfreeze()

    def _get_mouse_no_evt(self) -> Coord:
        """
        PRIVATE: Get the location of the mouse in video coordinates given no event.
        """
        width, height = self.GetClientSize().Get()
        if((not width) or (not height)):
            return (None, None)
        x, y = self.ScreenToClient(wx.GetMousePosition().Get())
        if((x is None) or (y is None) or (not (0 <= x < width)) or (not (0 <= y < height))):
            return (None, None)
        else:
            return (x, y)

    def on_draw(self, dc: wx.DC):
        """
        Called when redrawing the PointViewNEdit Control.

        :param dc: The wx.DC to draw to.
        """
        # Call superclass draw to draw the video...
        super().on_draw(dc)

        width, height = self.GetClientSize()
        if((not width) or (not height)):
            return

        frame = self._current_frame
        if(self._crop_box is not None):
            x, y, w, h = self._crop_box
            frame = self._current_frame[y:y+h, x:x+w]

        ov_h, ov_w = frame.shape[:2]
        x_off, y_off, nv_w, nv_h = self._get_video_bbox(frame, width, height)

        num_out = self._poses.get_bodypart_count()
        colormap = plt.get_cmap(self._colormap)
        frame = self.get_offset_count()

        for bp_idx in range(num_out):
            x = self._poses.get_x_at(frame, bp_idx)
            y = self._poses.get_y_at(frame, bp_idx)
            prob = self._poses.get_prob_at(frame, bp_idx)

            if(prob < self._plot_threshold):
                continue

            color = colormap(bp_idx / num_out, bytes=True)
            wx_color = wx.Colour(*color)
            dc.SetPen(wx.Pen(wx_color, 2, wx.PENSTYLE_SOLID))
            dc.SetBrush(wx.Brush(wx_color, wx.BRUSHSTYLE_SOLID))
            
            dc.DrawCircle(
                (x * (nv_w / ov_w)) + x_off,
                (y * (nv_h / ov_h)) + y_off,
                self._point_radius * (nv_h / ov_h)
            )

    def _get_selected_bodypart(self) -> Tuple[float, float, float]:
        """
        PRIVATE: Get the currently selected body part in the video.
        """
        x = self._poses.get_x_at(self.get_offset_count(), self._edit_point)
        y = self._poses.get_y_at(self.get_offset_count(), self._edit_point)
        prob = self._poses.get_prob_at(self.get_offset_count(), self._edit_point)
        return float(x), float(y), float(prob)

    def _point_prediction(self, x: ofloat, y: ofloat) -> Tuple[Any, Tuple[float, float, float]]:
        """
        PRIVATE: Makes a location prediction based on the user submitted point,
        and returns the submission data needed to force a full relocation for
        this labeler, and the predicted point location...
        """
        probability = 1 if(x is not None) else None

        if(len(self._pose_label_modes) < 1):
            raise ValueError("No labeling modes registered!")
        elif(self._current_pose_labeling_mode not in self._pose_label_modes):
            raise ValueError(f"Selected labeling mode '{self._current_pose_labeling_mode}' not a valid labeling mode.")

        labeler = self._pose_label_modes[self._current_pose_labeling_mode]
        submit_data, (x, y, probability) = labeler.predict_location(
            self.get_offset_count(), self._edit_point, x, y, probability
        )

        self._set_selected_bodypart(x, y, probability)

        return submit_data, (x, y, probability)

    def _point_relocation(self, x: ofloat, y: ofloat) -> Tuple[Any, Tuple[float, float, float]]:
        """
        PRIVATE: Makes a location prediction using _point_prediction, and
        then submits a point relocation to the point labeler, updating the
        labelers internal state. Returns the history data required to
        undo this point relocation event.
        """
        submit_data, new_p = self._point_prediction(x, y)
        labeler = self._pose_label_modes[self._current_pose_labeling_mode]
        hist_data = labeler.pose_change(submit_data)
        self._push_point_change_event(new_p, self._old_location, labeler, hist_data)
        self._old_location = None

        return (submit_data, new_p)

    def _set_selected_bodypart(self, x: float, y: float, probability: float):
        """
        PRIVATE: Set the currently selected body part in the video to a new location.
        """
        self._poses.set_x_at(self.get_offset_count(), self._edit_point, x)
        self._poses.set_y_at(self.get_offset_count(), self._edit_point, y)
        self._poses.set_prob_at(self.get_offset_count(), self._edit_point, probability)

    def get_all_poses(self) -> Pose:
        """
        Get the Poses of this PointViewNEdit.

        :returns: A Pose object, being the Pose object of this PointViewNEdit.
        """
        return self._poses

    def set_all_poses(self, poses: Pose):
        """
        Set all of the poses(points) of this control to the new pose object.

        :param poses: A Pose object. Not copied, so data can be manipulated...
        """
        if(self._poses.get_frame_count() == poses.get_frame_count()):
            if(self._poses.get_bodypart_count() == poses.get_bodypart_count()):
                self._poses = poses
                return

        raise ValueError("Pose dimensions don't match those of the current pose object!!!")

    def _get_mouse_loc_video(self, evt: Union[wx.MouseEvent, Coord]) -> Coord:
        """
        PRIVATE: Get the mouse location in video coordinates given a mouse event.
        """
        total_w, total_h = self.GetClientSize()
        if((not total_w) or (not total_h) or (self._current_frame is None)):
            return (None, None)

        frame = self._current_frame
        if(self._crop_box is not None):
            x, y, w, h = self._crop_box
            frame = self._current_frame[y:y+h, x:x+w]

        if(isinstance(evt, wx.MouseEvent)):
            x = evt.GetX()
            y = evt.GetY()
        else:
            x, y = evt
            if(x is None):
                return (None, None)
        # Now we need to translate into video coordinates...
        x_off, y_off, w, h = self._get_video_bbox(frame, total_w, total_h)
        v_h, v_w = frame.shape[:2]

        final_x, final_y = (x - x_off) * (v_w / w), (y - y_off) * (v_h / h)
        final_x, final_y = max(0, min(final_x, v_w)), max(0, min(final_y, v_h))

        return (final_x, final_y)

    def _push_point_change_event(
        self,
        new_point: Tuple[float, float, float],
        old_point: Tuple[float, float, float],
        labeler: PoseLabeler,
        labeler_data: Any
    ):
        """
        PRIVATE: Emits a PointChangeEvent from this widget with the provided values above.
        """
        new_evt = self.PointChangeEvent(
            id=self.Id,
            frame=self.get_offset_count(),
            part=self._edit_point,
            new_location=new_point,
            old_location=old_point,
            labeler=labeler,
            labeler_data=labeler_data
        )
        wx.PostEvent(self, new_evt)

    def _push_point_init_event(self, old_point: Tuple[float, float, float]):
        """
        PRIVATE: Emits a PointInitEvent from this widget with the provided values above.
        """
        new_evt = self.PointInitEvent(
            id=self.Id,
            frame=self.get_offset_count(),
            part=self._edit_point,
            current_location = old_point
        )
        wx.PostEvent(self, new_evt)

    def _push_point_end_event(self):
        """
        PRIVATE: Emits a PointEndEvent from this widget.
        """
        new_evt = self.PointEndEvent(
            id=self.Id,
            frame=self.get_offset_count(),
            part=self._edit_point
        )
        wx.PostEvent(self, new_evt)

    def on_press(self, event: wx.MouseEvent):
        """
        PRIVATE: Executed whenever the mouse is pressed down, triggering a PointInitEvent.
        """
        if(not self.is_playing() and (self._edit_point is not None) and (not self._ctrl_mode)):
            self.freeze()
            self._old_location = self._get_selected_bodypart()
            self._pressed = True
            self._push_point_init_event(self._old_location)
            self.on_move(event)

    def on_move(self, event: wx.MouseEvent):
        """
        PRIVATE: Executed whenever the mouse is moved, simply displaying the new point location on screen.
        """
        if((self._edit_point is None) or (not self.is_frozen())):
            self._pressed = False
            return

        if(self._ctrl_mode):
            self._pressed = False
            x, y = self._get_mouse_loc_video(event)
            self._point_prediction(x, y)
            self.Refresh()
        elif(self._pressed and event.LeftIsDown()):
            x, y = self._get_mouse_loc_video(event)
            if(x is None):
                return
            self._point_prediction(x, y)
            self.Refresh()

    def on_release(self, event: wx.MouseEvent):
        """
        PRIVATE: Executed whenever the mouse is released, triggering a PointChangeEvent followed by a PointEndEvent.
        """
        if((self._edit_point is None) or self._ctrl_mode or (not self.is_frozen())):
            self._pressed = False
            return

        if(self._pressed and event.LeftUp()):
            x, y = self._get_mouse_loc_video(event)
            if(x is not None):
                self._point_relocation(x, y)
            self._push_point_end_event()
            self._pressed = False
            self.unfreeze()
            self.Refresh()

    def on_right_click(self, event: wx.MouseEvent):
        """
        PRIVATE: Executed on right click, makes the point disappear as if it isn't in this frame.
        """
        if(self.is_playing() or (self._edit_point is None) or self._ctrl_mode):
            return
        self.freeze()

        self._old_location = self._get_selected_bodypart()
        x, y = self._get_mouse_loc_video(event)

        if(x is None):
            return

        self._point_relocation(None, None)
        self.unfreeze()
        self.Refresh()

    def set_pose(self, frame: int, bodypart: int, value: Tuple[float, float, float]):
        """
        Set the pose, or point data at the given frame and body part.

        :param frame: An integer, the frame index to change.
        :param bodypart: An integer, the body part index to change.
        :param value: A Tuple of floats, being the x video coordinate, y video coordinate, and probability to set the
                      point data to.
        """
        x, y, prob = value
        self._poses.set_x_at(frame, bodypart, x)
        self._poses.set_y_at(frame, bodypart, y)
        self._poses.set_prob_at(frame, bodypart, prob)

    def get_pose(self, frame: int, bodypart: int) -> Tuple[float, float, float]:
        """
        Get the pose, or point data at the given frame and body part.

        :param frame: An integer, the frame index to change.
        :param bodypart: An integer, the body part index to change.

        :return: A Tuple of floats, being the x video coordinate, y video coordinate, and probability at the given
                 location.
        """
        x = float(self._poses.get_x_at(frame, bodypart))
        y = float(self._poses.get_y_at(frame, bodypart))
        prob = float(self._poses.get_prob_at(frame, bodypart))
        return (x, y, prob)

    def get_selected_body_part(self) -> Optional[int]:
        """
        Get the selected body part. The selected body part can be modified in the point editor.

        :return: An integer index, being the index of the selected body part.
        """
        return self._edit_point

    def set_selected_bodypart(self, value: Optional[int]):
        """
        Set the selected body part. The selected body part can be modified in the point editor.

        :param value: An integer index, being the index to set the selected body part to.
        """
        if(value is None):
            self._edit_point = None
            return
        if(not (0 <= value <= self._poses.get_bodypart_count())):
            raise ValueError("Selected Body part not within range!")
        self._edit_point = value


class ColoredCircle(wx.Control):
    """
    Represents a static, colored circle. Used by the ColoredRadioButton for displaying colors.
    """
    def __init__(self, parent, color: wx.Colour, w_id = wx.ID_ANY, pos = wx.DefaultPosition,
                 size = wx.DefaultSize, style=wx.BORDER_NONE, validator=wx.DefaultValidator, name = "ColoredCircle"):
        """
        Construct a new ColoredCircle.

        :param parent: The parent wx.Window.
        :param color: A wx.Colour, the color of the circle.
        :param w_id: The wx ID.
        :param pos: The position of the widget.
        :param size: The size of the widget.
        :param style: The style of the widget.
        :param validator: The widgets validator.
        :param name: The name of the widget.
        """
        super().__init__(parent, w_id, pos, size, style, validator, name)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)

        self._color = color
        self.SetInitialSize(size)
        self.SetSize(size)
        self.Enable(False) # Disable tab traversal on this widget.

        self.Bind(wx.EVT_ERASE_BACKGROUND, lambda evt: None)
        self.Bind(wx.EVT_PAINT, self.on_paint)

    def on_paint(self, event):
        """
        Executed on a paint event, redrawing the circle.
        """
        self.on_draw(wx.GCDC(wx.PaintDC(self) if(self.IsDoubleBuffered()) else wx.BufferedPaintDC(self)))

    def on_draw(self, dc: wx.DC):
        """
        Draws the circle.

        :param dc: The wx.DC to draw with.
        """
        width, height = self.GetClientSize()

        if((not width) or (not height)):
            return

        dc.SetBackground(wx.Brush(self.GetBackgroundColour(), wx.BRUSHSTYLE_SOLID))
        dc.Clear()

        dc.SetBrush(wx.Brush(self._color, wx.BRUSHSTYLE_SOLID))
        dc.SetPen(wx.Pen(self._color, 1, wx.PENSTYLE_TRANSPARENT))

        circle_radius = min(width, height) // 2
        dc.DrawCircle(width // 2, height // 2, circle_radius)

    def get_circle_color(self) -> wx.Colour:
        """
        Get the color of the circle.

        :returns: A wx.Colour, being the color of the circle.
        """
        return self._color

    def set_circle_color(self, value: wx.Colour):
        """
        Set the color of the circle.

        :param value: A wx.Colour, which the color of the circle will be set to.
        """
        self._color = wx.Colour(value)


class ColoredRadioButton(wx.Panel):
    """
    A colored radio button, used for displaying colors of each body part.
    """

    ColoredRadioEvent, EVT_COLORED_RADIO = NewCommandEvent()
    PADDING = 10

    def __init__(self, parent, button_idx: int, color: wx.Colour, label: str, w_id = wx.ID_ANY,
                 pos = wx.DefaultPosition, size = wx.DefaultSize, style = 0, name = "ColoredRadioButton"):
        """
        Create a ColoredRadioButton.

        :param parent: The parent wx.Window.
        :param button_idx: The internal integer index of the button this index is emitted during a ColoredRadioEvent.
        :param color: A wx.Colour, the color of the radio button.
        :param label: A string, the text to set as the radio button's label.
        :param w_id: The wx ID.
        :param pos: The position of the widget.
        :param size: The size of the widget.
        :param style: The style of the widget.
        :param name: The name of the widget.
        """
        super().__init__(parent, w_id, pos, size, style, name)

        self._index = button_idx

        self._sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.radio_button = wx.CheckBox(self, label=label, style=wx.CHK_2STATE)
        height = self.radio_button.GetBestSize().GetHeight()
        self.circle = ColoredCircle(self, color, size=wx.Size(height, height))

        self.radio_button.SetValue(False)

        self._sizer.Add(self.circle, 0, wx.EXPAND, self.PADDING)
        self._sizer.Add(self.radio_button, 1, wx.EXPAND, self.PADDING)

        self.SetSizerAndFit(self._sizer)

        self.SetInitialSize(size)

        self.radio_button.Bind(wx.EVT_CHECKBOX, self._send_event)

    def _send_event(self, event):
        """
        PRIVATE: Emits a colored radio event.
        """
        evt = self.ColoredRadioEvent(id=self.Id, button_id=self._index, label=self.radio_button.GetLabelText())
        wx.PostEvent(self, evt)


class ColoredRadioBox(wx.Panel):
    """
    A colored radio box. Contains a list of ColoredRadioButtons, and implements selection such that only one radio
    button can be selected at a time.
    """

    ColoredRadioEvent, EVT_COLORED_RADIO = ColoredRadioButton.ColoredRadioEvent, ColoredRadioButton.EVT_COLORED_RADIO
    PADDING = 20

    def __init__(self, parent, colormap: Union[str, Colormap], labels: List[str], w_id = wx.ID_ANY,
                 pos = wx.DefaultPosition, size = wx.DefaultSize, style = wx.TAB_TRAVERSAL | wx.BORDER_DEFAULT,
                 name = "ColoredRadioBox"):
        """
        Create a ColoredRadioBox.

        :param parent: The parent wx.Window.
        :param colormap: A matplotlib colormap or string referencing a matplotlib colormap, used to assign each radio
                         a color.
        :param labels: A list of strings, being the labels for all of the radio buttons.
        :param w_id: The wx ID.
        :param pos: The position of the widget.
        :param size: The size of the widget.
        :param style: The style of the widget.
        :param name: The name of the widget.
        """
        super().__init__(parent, w_id, pos, size, style, name)

        self._scroller = ScrolledPanel(self, style=wx.VSCROLL)
        self._main_sizer = wx.BoxSizer(wx.VERTICAL)

        self._inner_sizer = wx.BoxSizer(wx.VERTICAL)
        self._buttons = []
        self._selected = None

        self._colormap = plt.get_cmap(colormap)

        for i, label in enumerate(labels):
            color = self._colormap(i / len(labels), bytes=True)
            wx_color = wx.Colour(*color)

            radio_button =  ColoredRadioButton(self._scroller, i, wx_color, label)
            radio_button.Bind(ColoredRadioButton.EVT_COLORED_RADIO, self._enforce_single_select)
            self._inner_sizer.Add(radio_button, 0, wx.EXPAND, self.PADDING)
            self._buttons.append(radio_button)

        self._scroller.SetSizerAndFit(self._inner_sizer)
        self._scroller.SetMinSize(wx.Size(self._scroller.GetMinSize().GetWidth() + self.PADDING, -1))
        self._scroller.SetAutoLayout(True)
        self._scroller.SetupScrolling()
        self._scroller.SendSizeEvent()

        self._main_sizer.Add(self._scroller, 1, wx.EXPAND)
        self.SetSizerAndFit(self._main_sizer)

    def _correct_sidebar_size(self, forward_now = True):
        """
        PRIVATE: Fixes the size of the radio box to account for the scrollbar...
        """
        self._scroller.Fit()
        self._scroller.SetMinSize(wx.Size(self._scroller.GetMinSize().GetWidth() + self.PADDING, -1))
        if(forward_now):
            self.SendSizeEvent()

    def _enforce_single_select(self, event: ColoredRadioButton.ColoredRadioEvent, user_set = True, post: bool = True):
        """
        PRIVATE: Enforces single select, only allowing for one radio button to be selected at a time.
        """
        # If we clicked on the already selected widget, toggle it off...
        if(user_set):
            if(self._selected == event.button_id):
                event.button_id = None

        # Disable all radio buttons except for the one that was just toggled on.
        for i, radio_button in enumerate(self._buttons):
            radio_button.radio_button.SetValue(i == event.button_id)

        self._selected = event.button_id

        # Repost the event on this widget...
        if(post):
            wx.PostEvent(self, event)

    def get_selected(self) -> Optional[int]:
        """
        Get the currently selected entry.

        :returns: An integer index, the index of the selected radio button, or None if no radio button is selected.
        """
        return self._selected

    def set_selected(self, value: int):
        """
        Get the currently selected entry.

        :param value: An integer index, the radio button to select, or None to deselect all the radio buttons.
        """
        if(not (0 <= value < len(self._buttons)) and (value is not None)):
            raise ValueError("Not a valid selection!!!!")
        if(value is not None):
            value = int(value)

        new_evt = ColoredRadioButton.ColoredRadioEvent(id=self.GetId(), button_id=value)
        self._enforce_single_select(new_evt, False, False)

    def get_labels(self) -> List[str]:
        """
        Get the radio button labels.

        :returns: A list of strings.
        """
        return [button.radio_button.GetLabel() for button in self._buttons]

    def set_labels(self, value: List[str]):
        """
        Set the radio button labels.

        :param value: A list of strings.
        """
        if(len(self._buttons) != len(value)):
            raise ValueError("Length of labels does not match the number of radio buttons!")

        for button, label in zip(self._buttons, value):
            button.SetLabel(label)

        self._correct_sidebar_size()

    def get_colormap(self) -> Colormap:
        """
        Get the colormap.

        :returns: A matplotlib colormap.
        """
        return self._colormap

    def set_colormap(self, value: Union[str, Colormap]):
        """
        Set the colormap.

        :param value: A matplotlib colormap, or string which refers to a valid matplotlib colormap.
        """
        self._colormap = plt.get_cmap(value)

        for i, button in enumerate(self._buttons):
            color = self._colormap(i / len(self._buttons), bytes=True)
            wx_color = wx.Colour(*color)
            button.circle.set_circle_color(wx_color)



class PointEditor(wx.Panel):
    """
    The Point Editor. Combines a PointViewNEdit and A ColoredRadio box to allow the user to edit any body parts on any
    frame.
    """
    def __init__(self,
        parent,
        video_hdl: cv2.VideoCapture,
        crop_box: Box,
        poses: Pose,
        bp_names: List[str],
        labeling_modes: List[PoseLabeler],
        colormap: str = PointViewNEdit.DEF_MAP,
        plot_threshold: float = 0.1,
        point_radius: int = 5,
        point_alpha: float = 0.7,
        w_id = wx.ID_ANY,
        pos = wx.DefaultPosition,
        size = wx.DefaultSize,
        style = wx.TAB_TRAVERSAL,
        name = "PointEditor"
    ):
        """
        Construct a new PointEdit

        :param parent: The wx Control Parent.
        :param video_hdl: The cv2 VideoCapture to play video from. One should avoid never manipulate the video capture
                  once passed to this constructor, as the handle will be passed to another thread for fast
                  video loading.
        :param crop_box: A tuple of 4 integers being the x, y, width, and height of the cropped area of the video.
        :param poses: The Pose object for the above video, used as initial point data.
        :param bp_names: A list of strings, being the names of the body parts.
        :param colormap: The matplotlib colormap to use when coloring the points.
        :param plot_threshold: The probability threshold at which to not plot a points. Defaults to 0.1.
        :param point_radius: Determines the size of the points. Defaults to 5.
        :param point_alpha: Determines the alpha level of the points. Defaults to 0.7.
        :param w_id: The wx ID.
        :param pos: The position of the widget.
        :param size: The size of the widget.
        :param style: The style of the widget.
        :param name: The name of the widget.
        """
        super().__init__(parent, w_id, pos, size, style, name)

        if(poses.get_bodypart_count() != len(bp_names)):
            raise ValueError("Length of the body part names provided does not match body part count of poses object!")
        if(len(labeling_modes) < 1):
            raise ValueError("Must pass at least 1 labeling mode!")

        self._main_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self._side_bar_sizer = wx.BoxSizer(wx.VERTICAL)

        self.video_viewer = PointViewNEdit(self, video_hdl, crop_box, poses, colormap, plot_threshold, point_radius,
                                           point_alpha)

        for p in labeling_modes:
            self.video_viewer.register_labeling_mode(p)

        self._labeling_label = wx.StaticText(self, label="Labeling Mode:")
        self._labeling_mode_selector = wx.Choice(self, choices=[p.get_display_name() for p in labeling_modes])
        self._labeling_settings = SettingCollectionWidget(self)
        self.select_box = ColoredRadioBox(self, colormap, bp_names)

        self._main_sizer.Add(self.video_viewer, 1, wx.EXPAND)

        self._side_bar_sizer.Add(self._labeling_label)
        self._side_bar_sizer.Add(self._labeling_mode_selector)
        self._side_bar_sizer.Add(self._labeling_settings)
        self._side_bar_sizer.Add(self.select_box, 1, wx.EXPAND)

        self._main_sizer.Add(self._side_bar_sizer, 0, wx.EXPAND)

        self.SetSizerAndFit(self._main_sizer)

        self.select_box.Bind(ColoredRadioBox.EVT_COLORED_RADIO, self.on_radio_change)
        self.Bind(wx.EVT_CHOICE, self._set_mode_from_dropdown)
        self.Bind(PointViewNEdit.EVT_POINT_INIT, lambda evt: self.toggle_select_box(evt, False))
        self.Bind(PointViewNEdit.EVT_POINT_END, lambda evt: self.toggle_select_box(evt, True))
        # Initialize labeling mode...
        self._labeling_mode_selector.SetSelection(0)
        self._set_mode_from_dropdown(None)

    def toggle_select_box(self, event, value: bool):
        """
        PRIVATE: Triggered when editing a point in the PointViewNEdit, disables to ColoredRadioBox to keep the body
        part from being changed mid-frame.
        """
        self.select_box.Enable(value)
        self._labeling_mode_selector.Enable(value)
        event.Skip()

    def _set_mode_from_dropdown(self, evt):
        idx = self._labeling_mode_selector.GetSelection()
        value = self._labeling_mode_selector.GetString(idx)
        self._labeling_settings.set_setting_collection(
            self.video_viewer.get_labeling_class(value).get_settings()
        )
        self.video_viewer.set_labeling_mode(value)

    def set_body_part(self, part: int):
        """
        Set the selected body part.

        :param part: An integer, the index of the body part to select.
        """
        self.select_box.set_selected(part)

    def get_body_part(self) -> int:
        """
        Get the selected body part.

        :returns: An integer, the index of the selected body part.
        """
        return self.select_box.get_selected()

    def on_radio_change(self, event):
        """
        PRIVATE: Triggered when the selected body part is changed.
        """
        idx = event.button_id
        self.video_viewer.set_selected_bodypart(idx)