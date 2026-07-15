import unittest

from canvas_zoom import (
    bind_canvas_pan,
    canvas_to_logical,
    event_to_logical,
    get_zoom,
    queue_trackpad_zoom,
    set_zoom,
    to_canvas,
)
from models import Device, Door, Sensor, Wall
from utils import (
    find_closest_sensor_within_fov,
    find_switch_sensors_by_doors,
    get_nearby_device_states,
    intersect,
    is_path_blocked_by_walls,
    is_within_fov,
)


def make_sensor(name, sensor_type, x, y, direction=None):
    return Sensor(name, x, y, sensor_type, 0, 1, 1, 0, direction=direction)


class GeometryTests(unittest.TestCase):
    def test_segment_intersection(self):
        self.assertTrue(intersect(0, 0, 10, 10, 0, 10, 10, 0))
        self.assertFalse(intersect(0, 0, 5, 0, 0, 5, 5, 5))
        self.assertTrue(intersect(0, 0, 10, 0, 5, 0, 15, 0))

    def test_fov_respects_angle_and_distance(self):
        pir = make_sensor("pir", "PIR", 0, 0, direction=0)
        self.assertTrue(is_within_fov(pir, 10, 0, 20, 60))
        self.assertFalse(is_within_fov(pir, 0, 10, 20, 60))
        self.assertFalse(is_within_fov(pir, 30, 0, 20, 60))

    def test_closed_door_and_wall_block_visibility(self):
        wall = Wall(5, -5, 5, 5)
        closed_door = Door(5, -5, 5, 5, "close")
        open_door = Door(5, -5, 5, 5, "open")

        self.assertTrue(is_path_blocked_by_walls(0, 0, 10, 0, [wall], []))
        self.assertTrue(is_path_blocked_by_walls(0, 0, 10, 0, [], [closed_door]))
        self.assertFalse(is_path_blocked_by_walls(0, 0, 10, 0, [], [open_door]))

    def test_closest_visible_sensor_skips_blocked_sensor(self):
        near = make_sensor("near", "PIR", 0, 0, direction=0)
        far = make_sensor("far", "PIR", -5, 0, direction=0)
        wall = Wall(2, -2, 2, 2)

        selected = find_closest_sensor_within_fov(
            (10, 0),
            [near, far],
            [wall],
            [],
            20,
            60,
        )
        self.assertIsNone(selected)

    def test_switch_association_and_nearby_devices(self):
        door = Door(0, 0, 10, 0, "close")
        switch = make_sensor("switch", "Switch", 5, 5)
        far_switch = make_sensor("far", "Switch", 100, 100)
        associations = find_switch_sensors_by_doors([door], [switch, far_switch])

        self.assertEqual(associations[0][1], [switch])

        sensor = make_sensor("temp", "Temperature", 0, 0)
        near_device = Device("near", 10, 0, "Oven", 100, 1, 0, 100)
        far_device = Device("far", 200, 0, "Oven", 100, 1, 0, 100)
        self.assertEqual(
            get_nearby_device_states(sensor, [near_device, far_device], [], [], 50),
            [1],
        )


class CanvasZoomTests(unittest.TestCase):
    class FakeCanvas:
        def __init__(self):
            self._zoom_factor = 1.0
            self.scale_calls = []
            self.config = {}
            self.xview_calls = []
            self.yview_calls = []
            self.bindings = {}
            self.scan_marks = []
            self.scan_drags = []
            self.after_calls = []

        def canvasx(self, value):
            return value + 40

        def canvasy(self, value):
            return value + 20

        def scale(self, *args):
            self.scale_calls.append(args)

        def bbox(self, _tag):
            return (0, 0, 100, 100)

        def configure(self, **kwargs):
            self.config.update(kwargs)

        def winfo_width(self):
            return 50

        def winfo_height(self):
            return 50

        def xview_moveto(self, fraction):
            self.xview_calls.append(fraction)

        def yview_moveto(self, fraction):
            self.yview_calls.append(fraction)

        def bind(self, sequence, callback):
            self.bindings[sequence] = callback

        def scan_mark(self, x, y):
            self.scan_marks.append((x, y))

        def scan_dragto(self, x, y, gain=10):
            self.scan_drags.append((x, y, gain))

        def after(self, delay, callback):
            self.after_calls.append((delay, callback))
            return len(self.after_calls)

    class Event:
        x = 60
        y = 30

    def test_coordinate_conversion_uses_zoom_without_changing_model_space(self):
        canvas = self.FakeCanvas()
        canvas._zoom_factor = 2.0

        self.assertEqual(to_canvas(canvas, 25, 10), (50.0, 20.0))
        self.assertEqual(canvas_to_logical(canvas, 50, 20), (25.0, 10.0))
        self.assertEqual(event_to_logical(canvas, self.Event()), (50.0, 25.0))

    def test_zoom_is_clamped_and_scales_from_origin(self):
        canvas = self.FakeCanvas()

        self.assertEqual(set_zoom(canvas, 10), 2.5)
        self.assertEqual(get_zoom(canvas), 2.5)
        self.assertEqual(canvas.scale_calls, [("all", 0, 0, 2.5, 2.5)])
        self.assertEqual(canvas.config["scrollregion"], (0, 0, 100, 100))
        self.assertEqual(canvas.xview_calls, [0.5])
        self.assertEqual(canvas.yview_calls, [0.5])

    def test_pan_uses_secondary_buttons_without_overriding_avatar_click(self):
        canvas = self.FakeCanvas()
        bind_canvas_pan(canvas)

        self.assertNotIn("<ButtonPress-1>", canvas.bindings)
        self.assertIn("<ButtonPress-2>", canvas.bindings)
        self.assertIn("<ButtonPress-3>", canvas.bindings)
        canvas.bindings["<ButtonPress-2>"](self.Event())
        canvas.bindings["<B2-Motion>"](self.Event())

        self.assertEqual(canvas.scan_marks, [(60, 30)])
        self.assertEqual(canvas.scan_drags, [(60, 30, 1)])

    def test_trackpad_zoom_coalesces_input_into_one_synchronized_update(self):
        canvas = self.FakeCanvas()
        background_zooms = []
        canvas._redraw_zoom_background = background_zooms.append
        queue_trackpad_zoom(canvas, 1, screen_x=20, screen_y=30)
        queue_trackpad_zoom(canvas, 1, screen_x=25, screen_y=35)

        self.assertEqual(canvas._zoom_pending_steps, 2)
        self.assertEqual(len(canvas.after_calls), 1)
        self.assertEqual(canvas._zoom_pointer, (25.0, 35.0))
        canvas.after_calls[0][1]()

        self.assertGreater(get_zoom(canvas), 1.0)
        self.assertEqual(canvas._zoom_pending_steps, 0)
        self.assertEqual(background_zooms, [get_zoom(canvas)])


if __name__ == "__main__":
    unittest.main()
