import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ui.device_inspector import (
    MapSelection,
    _selection_at_event,
    activate_manual_mode,
    activate_select_mode,
    associated_sensor_names,
    begin_marquee_selection,
    begin_point_segment_mode,
    begin_selection_drag,
    copy_selected_object,
    cut_selected_object,
    delete_device,
    delete_map_selection,
    drag_selected_object,
    duplicate_selected_object,
    finish_selection_drag,
    finish_marquee_selection,
    open_selection_menu_at_event,
    paste_map_object,
    record_wall_addition,
    select_object_at_event,
    update_marquee_selection,
    undo_last_movement,
)
from house_state import HouseState
from models import Device, Door, Point, Sensor, Wall


class FakeCanvas:
    def __init__(self):
        self.deleted = []
        self.tags = {
            1: ("lamp", "device"),
            2: ("lamp", "device", "device_label"),
        }

    def find_withtag(self, tag):
        if tag == "lamp":
            return (1, 2)
        return ()

    def gettags(self, item_id):
        return self.tags.get(item_id, ())

    def delete(self, item_id):
        self.deleted.append(item_id)


class DeviceInspectorTests(unittest.TestCase):
    def make_context(self):
        device = Device("lamp", 10, 20, "Light", 20, 0, 1, 20)
        sensor = Sensor(
            "meter",
            10,
            20,
            "Smart Meter",
            0,
            100,
            1,
            0,
            associated_device="lamp",
        )
        ctx = SimpleNamespace(
            canvas=FakeCanvas(),
            load_active=True,
            r_points=[],
            read_walls=[],
            read_devices=[device],
            read_sensors=[sensor],
            read_doors=[],
            house_state=HouseState(),
            _selected_device=device,
            _selected_object=MapSelection("device", device),
        )
        return ctx, device, sensor

    def test_associated_sensor_names_are_resolved_for_device(self):
        ctx, device, _sensor = self.make_context()

        self.assertEqual(associated_sensor_names(ctx, device), ["meter"])

    def test_manual_mode_removes_left_click_selection(self):
        class Canvas:
            def __init__(self):
                self.bindings = {
                    "<Button-1>": object(),
                    "<Button-3>": object(),
                }
                self.deleted = []
                self.cursor = None

            def unbind(self, sequence):
                self.bindings.pop(sequence, None)

            def delete(self, tag):
                self.deleted.append(tag)

            def configure(self, **kwargs):
                self.cursor = kwargs.get("cursor", self.cursor)

        ctx = SimpleNamespace(
            canvas=Canvas(),
            scenario_menu=None,
            _selected_device=object(),
            _selected_object=object(),
            _tool_palette=None,
            _canvas_mode_var=None,
        )

        with patch("app.controllers.simulation.enable_all_menus"):
            activate_manual_mode(ctx)

        self.assertNotIn("<Button-1>", ctx.canvas.bindings)
        self.assertNotIn("<Button-3>", ctx.canvas.bindings)
        self.assertIsNone(ctx._selected_device)
        self.assertIsNone(ctx._selected_object)
        self.assertEqual(ctx._canvas_mode, "manual")

    def test_segment_tool_picks_two_points_and_shows_a_preview(self):
        class Canvas:
            _zoom_factor = 1.0

            def __init__(self):
                self.bindings = {}
                self.deleted = []
                self.lines = []

            def bind(self, sequence, callback):
                self.bindings[sequence] = callback

            def unbind(self, sequence):
                self.bindings.pop(sequence, None)

            def canvasx(self, value):
                return value

            def canvasy(self, value):
                return value

            def delete(self, tag):
                self.deleted.append(tag)

            def create_line(self, *coordinates, **options):
                self.lines.append((coordinates, options))
                return len(self.lines)

            def create_oval(self, *_coordinates, **_options):
                return 1

            def configure(self, **_options):
                pass

        class Menu:
            def entryconfig(self, *_args, **_kwargs):
                pass

        first = Point("a", 0, 0)
        second = Point("b", 100, 0)
        canvas = Canvas()
        created = []
        finished = []
        ctx = SimpleNamespace(
            canvas=canvas,
            scenario_menu=Menu(),
            load_active=True,
            r_points=[first, second],
            read_walls=[],
            read_sensors=[],
            read_devices=[],
            read_doors=[],
            _canvas_mode="select",
            _tool_palette=None,
            _canvas_mode_var=None,
            _activate_canvas_select=lambda: None,
        )

        with patch("app.controllers.simulation.enable_all_menus"):
            begin_point_segment_mode(
                ctx,
                "Add walls",
                lambda point1, point2: created.append((point1, point2)) or object(),
                on_finished=finished.append,
            )
            canvas.bindings["<Button-1>"](SimpleNamespace(x=0, y=0))
            canvas.bindings["<Motion>"](SimpleNamespace(x=60, y=20))
            canvas.bindings["<Button-1>"](SimpleNamespace(x=100, y=0))

        self.assertEqual(created, [(first, second)])
        self.assertEqual(finished, [True])
        self.assertTrue(canvas.lines)
        self.assertIsNone(ctx._segment_placement_state)

    def test_continuous_segment_tool_stays_active_after_create_and_cancel(self):
        class Canvas:
            _zoom_factor = 1.0

            def __init__(self):
                self.bindings = {}

            def bind(self, sequence, callback):
                self.bindings[sequence] = callback

            def unbind(self, sequence):
                self.bindings.pop(sequence, None)

            def canvasx(self, value):
                return value

            def canvasy(self, value):
                return value

            def delete(self, _tag):
                pass

            def create_line(self, *_coordinates, **_options):
                return 1

            def create_oval(self, *_coordinates, **_options):
                return 1

            def configure(self, **_options):
                pass

        first = Point("a", 0, 0)
        second = Point("b", 100, 0)
        canvas = Canvas()
        created = []
        finished = []
        ctx = SimpleNamespace(
            canvas=canvas,
            scenario_menu=None,
            load_active=True,
            r_points=[first, second],
            read_walls=[],
            read_sensors=[],
            read_devices=[],
            read_doors=[],
            _canvas_mode="select",
            _tool_palette=None,
            _canvas_mode_var=None,
            _activate_canvas_select=lambda: None,
        )

        with patch("app.controllers.simulation.enable_all_menus"):
            begin_point_segment_mode(
                ctx,
                "Add walls",
                lambda point1, point2: created.append((point1, point2)) or object(),
                on_finished=finished.append,
                continuous=True,
            )
            canvas.bindings["<Button-1>"](SimpleNamespace(x=0, y=0))
            canvas.bindings["<Button-1>"](SimpleNamespace(x=100, y=0))

            self.assertEqual(created, [(first, second)])
            self.assertEqual(finished, [])
            self.assertEqual(ctx._canvas_mode, "placement")
            self.assertIsNone(ctx._segment_placement_state["first"])

            canvas.bindings["<Button-1>"](SimpleNamespace(x=0, y=0))
            canvas.bindings["<Button-3>"](SimpleNamespace())

        self.assertEqual(ctx._canvas_mode, "placement")
        self.assertIsNone(ctx._segment_placement_state["first"])
        self.assertEqual(finished, [])

    def test_left_click_selects_without_opening_the_action_menu(self):
        selection = MapSelection("device", object())
        canvas = SimpleNamespace(focus_set=lambda: None)
        ctx = SimpleNamespace(canvas=canvas)

        with (
            patch(
                "app.ui.device_inspector._selection_at_event",
                return_value=selection,
            ),
            patch("app.ui.device_inspector._draw_selection"),
            patch("app.ui.device_inspector._show_selection_actions") as menu,
        ):
            result = select_object_at_event(ctx, object())

        self.assertEqual(result, "break")
        self.assertIs(ctx._selected_object, selection)
        menu.assert_not_called()

    def test_right_click_selects_and_opens_the_action_menu(self):
        selection = MapSelection("sensor", object())
        event = object()
        canvas = SimpleNamespace(focus_set=lambda: None)
        ctx = SimpleNamespace(canvas=canvas)

        with (
            patch(
                "app.ui.device_inspector._selection_at_event",
                return_value=selection,
            ),
            patch("app.ui.device_inspector._draw_selection"),
            patch("app.ui.device_inspector._show_selection_actions") as menu,
        ):
            result = open_selection_menu_at_event(ctx, event)

        self.assertEqual(result, "break")
        self.assertIs(ctx._selected_object, selection)
        menu.assert_called_once_with(ctx, selection, event)

    def test_copy_and_paste_device_uses_a_unique_name_and_offset(self):
        ctx, device, _sensor = self.make_context()
        ctx._selected_object = MapSelection("device", device)

        with (
            patch("app.ui.device_inspector._redraw_editable_objects"),
            patch("app.ui.device_inspector._draw_selection"),
        ):
            self.assertEqual(copy_selected_object(ctx), "break")
            self.assertEqual(paste_map_object(ctx), "break")

        pasted = ctx.read_devices[-1]
        self.assertEqual(len(ctx.read_devices), 2)
        self.assertEqual(pasted.name, "lamp_copy")
        self.assertEqual((pasted.x, pasted.y), (30, 40))
        self.assertIs(ctx._selected_object.value, pasted)

    def test_duplicate_wall_creates_reference_points_for_saving(self):
        ctx, _device, _sensor = self.make_context()
        wall = Wall(0, 0, 100, 0)
        ctx.r_points.extend([Point("a", 0, 0), Point("b", 100, 0)])
        ctx.read_walls.append(wall)
        ctx._selected_object = MapSelection("wall", wall)

        with (
            patch("app.ui.device_inspector._redraw_editable_objects"),
            patch("app.ui.device_inspector._draw_selection"),
            patch("read.read_walls_coordinates", []),
        ):
            self.assertEqual(duplicate_selected_object(ctx), "break")

        pasted = ctx.read_walls[-1]
        self.assertEqual((pasted.x1, pasted.y1), (20, 20))
        self.assertEqual((pasted.x2, pasted.y2), (120, 20))
        self.assertIn((20, 20), {(point.x, point.y) for point in ctx.r_points})
        self.assertIn((120, 20), {(point.x, point.y) for point in ctx.r_points})

    def test_cut_and_paste_preserves_device_name_and_sensor_link(self):
        ctx, device, sensor = self.make_context()
        ctx._selected_object = MapSelection("device", device)

        with (
            patch("app.ui.device_inspector._redraw_editable_objects"),
            patch("app.ui.device_inspector._draw_selection"),
        ):
            self.assertEqual(cut_selected_object(ctx), "break")
            self.assertEqual(ctx.read_devices, [])
            self.assertEqual(sensor.associated_device, "lamp")
            self.assertEqual(paste_map_object(ctx), "break")

        self.assertEqual(ctx.read_devices[0].name, "lamp")
        self.assertEqual((ctx.read_devices[0].x, ctx.read_devices[0].y), (10, 20))
        self.assertEqual(sensor.associated_device, "lamp")

    def test_dragging_wall_endpoint_moves_connected_geometry(self):
        class Canvas:
            _zoom_factor = 1.0

            def canvasx(self, value):
                return value

            def canvasy(self, value):
                return value

            def focus_set(self):
                pass

            def delete(self, _tag):
                pass

        point = Point("corner", 0, 0)
        other = Point("other", 100, 0)
        wall = Wall(0, 0, 100, 0)
        connected = Wall(0, 0, 0, 100)
        door = Door(0, 0, 0, 20, "close")
        ctx = SimpleNamespace(
            canvas=Canvas(),
            load_active=True,
            r_points=[point, other],
            read_sensors=[],
            read_devices=[],
            read_walls=[wall, connected],
            read_doors=[door],
        )
        selection = MapSelection("wall", wall)

        with (
            patch(
                "app.ui.device_inspector._selection_at_event",
                return_value=selection,
            ),
            patch("app.ui.device_inspector._draw_selections"),
            patch("app.ui.device_inspector._draw_drag_preview"),
            patch("app.ui.device_inspector._redraw_editable_objects") as redraw,
        ):
            begin_selection_drag(ctx, SimpleNamespace(x=0, y=0))
            drag_selected_object(ctx, SimpleNamespace(x=15, y=25))
            self.assertEqual((point.x, point.y), (0, 0))
            redraw.assert_not_called()
            finish_selection_drag(ctx)
            redraw.assert_called_once_with(ctx)

        self.assertEqual((point.x, point.y), (15, 25))
        self.assertEqual((wall.x1, wall.y1), (15, 25))
        self.assertEqual((connected.x1, connected.y1), (15, 25))
        self.assertEqual((door.x1, door.y1), (15, 25))
        self.assertEqual((wall.x2, wall.y2), (100, 0))

    def test_dragging_device_moves_its_model(self):
        class Canvas:
            _zoom_factor = 1.0

            def canvasx(self, value):
                return value

            def canvasy(self, value):
                return value

            def focus_set(self):
                pass

            def delete(self, _tag):
                pass

        device = Device("lamp", 10, 20, "Light", 20, 0, 1, 20)
        ctx = SimpleNamespace(
            canvas=Canvas(),
            load_active=True,
            r_points=[],
            read_sensors=[],
            read_devices=[device],
            read_walls=[],
            read_doors=[],
        )
        selection = MapSelection("device", device)

        with (
            patch(
                "app.ui.device_inspector._selection_at_event",
                return_value=selection,
            ),
            patch("app.ui.device_inspector._draw_selections"),
            patch("app.ui.device_inspector._draw_drag_preview") as preview,
            patch("app.ui.device_inspector._redraw_editable_objects") as redraw,
        ):
            begin_selection_drag(ctx, SimpleNamespace(x=10, y=20))
            drag_selected_object(ctx, SimpleNamespace(x=35, y=45))
            self.assertEqual((device.x, device.y), (10, 20))
            preview.assert_called_once()
            redraw.assert_not_called()
            finish_selection_drag(ctx)
            redraw.assert_called_once_with(ctx)

        self.assertEqual((device.x, device.y), (35, 45))

    def test_ctrl_z_restores_previous_device_position(self):
        class Canvas:
            _zoom_factor = 1.0

            def canvasx(self, value):
                return value

            def canvasy(self, value):
                return value

            def focus_set(self):
                pass

            def delete(self, _tag):
                pass

        device = Device("lamp", 10, 20, "Light", 20, 0, 1, 20)
        selection = MapSelection("device", device)
        ctx = SimpleNamespace(
            canvas=Canvas(),
            load_active=True,
            r_points=[],
            read_sensors=[],
            read_devices=[device],
            read_walls=[],
            read_doors=[],
            _selected_object=selection,
            _selected_objects=[selection],
            _movement_undo_stack=[],
        )

        with (
            patch(
                "app.ui.device_inspector._selection_at_event",
                return_value=selection,
            ),
            patch("app.ui.device_inspector._draw_selections"),
            patch("app.ui.device_inspector._draw_drag_preview"),
            patch("app.ui.device_inspector._redraw_editable_objects") as redraw,
        ):
            begin_selection_drag(ctx, SimpleNamespace(x=10, y=20))
            drag_selected_object(ctx, SimpleNamespace(x=35, y=45))
            finish_selection_drag(ctx)
            self.assertEqual((device.x, device.y), (35, 45))

            self.assertEqual(undo_last_movement(ctx), "break")

        self.assertEqual((device.x, device.y), (10, 20))
        self.assertEqual(ctx._movement_undo_stack, [])
        self.assertEqual(redraw.call_count, 2)

    def test_ctrl_z_restores_connected_geometry_after_wall_resize(self):
        point = Point("corner", 0, 0)
        wall = Wall(0, 0, 100, 0)
        connected_door = Door(0, 0, 0, 20, "close")
        snapshot = [
            (point, (0, 0)),
            (wall, (0, 0, 100, 0)),
            (connected_door, (0, 0, 0, 20)),
        ]
        point.x, point.y = 25, 25
        wall.x1, wall.y1 = 25, 25
        connected_door.x1, connected_door.y1 = 25, 25
        ctx = SimpleNamespace(
            canvas=SimpleNamespace(delete=lambda _tag: None),
            load_active=True,
            r_points=[point],
            read_sensors=[],
            read_devices=[],
            read_walls=[wall],
            read_doors=[connected_door],
            _selected_object=None,
            _selected_objects=[],
            _movement_undo_stack=[snapshot],
        )

        with patch("app.ui.device_inspector._redraw_editable_objects"):
            undo_last_movement(ctx)

        self.assertEqual((point.x, point.y), (0, 0))
        self.assertEqual((wall.x1, wall.y1), (0, 0))
        self.assertEqual((connected_door.x1, connected_door.y1), (0, 0))

    def test_ctrl_z_removes_a_just_added_wall(self):
        wall = Wall(0, 0, 100, 0)
        ctx = SimpleNamespace(
            canvas=SimpleNamespace(delete=lambda _tag: None),
            load_active=True,
            r_points=[Point("a", 0, 0), Point("b", 100, 0)],
            read_sensors=[],
            read_devices=[],
            read_walls=[wall],
            read_doors=[],
            _selected_object=None,
            _selected_objects=[],
            _movement_undo_stack=[],
        )

        with (
            patch("read.read_walls_coordinates", [wall]) as pir_walls,
            patch("app.ui.device_inspector._redraw_editable_objects"),
        ):
            record_wall_addition(ctx, wall)
            self.assertEqual(undo_last_movement(ctx), "break")

        self.assertEqual(ctx.read_walls, [])
        self.assertEqual(pir_walls, [])

    def test_ctrl_z_restores_a_just_deleted_wall(self):
        wall = Wall(0, 0, 100, 0)
        ctx = SimpleNamespace(
            canvas=SimpleNamespace(delete=lambda _tag: None),
            load_active=True,
            r_points=[Point("a", 0, 0), Point("b", 100, 0)],
            read_sensors=[],
            read_devices=[],
            read_walls=[wall],
            read_doors=[],
            _selected_object=MapSelection("wall", wall),
            _selected_objects=[MapSelection("wall", wall)],
            _movement_undo_stack=[],
        )

        with (
            patch("read.read_walls_coordinates", [wall]) as pir_walls,
            patch("app.ui.device_inspector._redraw_editable_objects"),
            patch("app.ui.device_inspector._draw_selections"),
        ):
            self.assertTrue(
                delete_map_selection(
                    ctx,
                    MapSelection("wall", wall),
                    confirm=False,
                )
            )
            self.assertEqual(ctx.read_walls, [])
            self.assertEqual(undo_last_movement(ctx), "break")

        self.assertEqual(ctx.read_walls, [wall])
        self.assertEqual(pir_walls, [wall])

    def test_grid_mode_snaps_device_movement(self):
        class Canvas:
            _zoom_factor = 1.0
            _snap_to_grid = True

            def canvasx(self, value):
                return value

            def canvasy(self, value):
                return value

            def focus_set(self):
                pass

            def delete(self, _tag):
                pass

        device = Device("lamp", 10, 12, "Light", 20, 0, 1, 20)
        selection = MapSelection("device", device)
        ctx = SimpleNamespace(
            canvas=Canvas(),
            load_active=True,
            r_points=[],
            read_sensors=[],
            read_devices=[device],
            read_walls=[],
            read_doors=[],
            _selected_object=selection,
            _selected_objects=[selection],
            _movement_undo_stack=[],
        )

        with (
            patch(
                "app.ui.device_inspector._selection_at_event",
                return_value=selection,
            ),
            patch("app.ui.device_inspector._draw_selections"),
            patch("app.ui.device_inspector._draw_drag_preview"),
            patch("app.ui.device_inspector._redraw_editable_objects"),
        ):
            begin_selection_drag(ctx, SimpleNamespace(x=10, y=12))
            drag_selected_object(ctx, SimpleNamespace(x=30, y=32))
            finish_selection_drag(ctx)

        self.assertEqual((device.x, device.y), (25, 25))

    def test_right_drag_selects_multiple_objects_in_rectangle(self):
        class Canvas:
            _zoom_factor = 1.0

            def __init__(self):
                self.deleted = []

            def canvasx(self, value):
                return value

            def canvasy(self, value):
                return value

            def focus_set(self):
                pass

            def delete(self, tag):
                self.deleted.append(tag)

            def create_rectangle(self, *_args, **_kwargs):
                return 1

        point = Point("p", 10, 10)
        sensor = Sensor("s", 40, 40, "PIR", 0, 1, 1, 0)
        device = Device("d", 100, 100, "Light", 10, 0, 1, 10)
        ctx = SimpleNamespace(
            canvas=Canvas(),
            load_active=True,
            r_points=[point],
            read_sensors=[sensor],
            read_devices=[device],
            read_walls=[],
            read_doors=[],
            _selected_object=None,
            _selected_objects=[],
        )

        with patch("app.ui.device_inspector._draw_selections") as draw:
            begin_marquee_selection(ctx, SimpleNamespace(x=0, y=0))
            update_marquee_selection(ctx, SimpleNamespace(x=60, y=60))
            finish_marquee_selection(ctx, SimpleNamespace(x=60, y=60))

        self.assertEqual(
            {selection.kind for selection in ctx._selected_objects},
            {"point", "sensor"},
        )
        draw.assert_called_once_with(ctx, ctx._selected_objects)

    def test_left_drag_moves_all_marquee_selected_objects(self):
        class Canvas:
            _zoom_factor = 1.0
            _snap_to_grid = False

            def canvasx(self, value):
                return value

            def canvasy(self, value):
                return value

            def focus_set(self):
                pass

            def delete(self, _tag):
                pass

        first = Device("one", 10, 20, "Light", 10, 0, 1, 10)
        second = Sensor("two", 30, 40, "PIR", 0, 1, 1, 0)
        selections = [MapSelection("device", first), MapSelection("sensor", second)]
        ctx = SimpleNamespace(
            canvas=Canvas(),
            load_active=True,
            r_points=[],
            read_sensors=[second],
            read_devices=[first],
            read_walls=[],
            read_doors=[],
            _selected_object=selections[0],
            _selected_objects=selections,
        )

        with (
            patch(
                "app.ui.device_inspector._selection_at_event",
                return_value=selections[0],
            ),
            patch("app.ui.device_inspector._draw_selections"),
            patch("app.ui.device_inspector._draw_drag_preview"),
            patch("app.ui.device_inspector._redraw_editable_objects"),
        ):
            begin_selection_drag(ctx, SimpleNamespace(x=10, y=20))
            drag_selected_object(ctx, SimpleNamespace(x=35, y=45))
            finish_selection_drag(ctx)

        self.assertEqual((first.x, first.y), (35, 45))
        self.assertEqual((second.x, second.y), (55, 65))

    def test_point_drag_snaps_to_grid_when_enabled(self):
        class Canvas:
            _zoom_factor = 1.0
            _snap_to_grid = True

            def canvasx(self, value):
                return value

            def canvasy(self, value):
                return value

            def focus_set(self):
                pass

            def delete(self, _tag):
                pass

        point = Point("p", 10, 12)
        selection = MapSelection("point", point)
        ctx = SimpleNamespace(
            canvas=Canvas(),
            load_active=True,
            r_points=[point],
            read_sensors=[],
            read_devices=[],
            read_walls=[],
            read_doors=[],
            _selected_object=selection,
            _selected_objects=[selection],
        )

        with (
            patch(
                "app.ui.device_inspector._selection_at_event",
                return_value=selection,
            ),
            patch("app.ui.device_inspector._draw_selections"),
            patch("app.ui.device_inspector._draw_drag_preview"),
            patch("app.ui.device_inspector._redraw_editable_objects"),
        ):
            begin_selection_drag(ctx, SimpleNamespace(x=10, y=12))
            drag_selected_object(ctx, SimpleNamespace(x=30, y=32))
            finish_selection_drag(ctx)

        self.assertEqual((point.x, point.y), (25, 25))

    def test_delete_removes_device_and_clears_sensor_association(self):
        ctx, device, sensor = self.make_context()

        with patch("app.ui.device_inspector._redraw_editable_objects") as redraw:
            deleted = delete_device(ctx, device, confirm=False)

        self.assertTrue(deleted)
        self.assertEqual(ctx.read_devices, [])
        self.assertIsNone(sensor.associated_device)
        self.assertIsNone(ctx._selected_device)
        self.assertIsNone(ctx._selected_object)
        redraw.assert_called_once_with(ctx)

    def test_sensor_wall_and_door_can_be_deleted(self):
        ctx, _device, sensor = self.make_context()
        wall = Wall(0, 0, 20, 0)
        door = Door(20, 0, 20, 20, "close")
        ctx.read_walls.append(wall)
        ctx.read_doors.append(door)
        ctx.house_state.sensor_states()[sensor.name] = {"state": [0]}

        with (
            patch("app.ui.device_inspector._redraw_editable_objects"),
            patch("read.read_walls_coordinates", [wall]),
        ):
            self.assertTrue(
                delete_map_selection(
                    ctx,
                    MapSelection("sensor", sensor),
                    confirm=False,
                )
            )
            self.assertTrue(
                delete_map_selection(
                    ctx,
                    MapSelection("wall", wall),
                    confirm=False,
                )
            )
            self.assertTrue(
                delete_map_selection(
                    ctx,
                    MapSelection("door", door),
                    confirm=False,
                )
            )

        self.assertNotIn(sensor, ctx.read_sensors)
        self.assertNotIn(sensor.name, ctx.house_state.sensor_states())
        self.assertNotIn(wall, ctx.read_walls)
        self.assertNotIn(door, ctx.read_doors)

    def test_deleting_point_removes_connected_walls_and_doors(self):
        ctx, _device, _sensor = self.make_context()
        point = Point("corner", 0, 0)
        other = Point("other", 20, 0)
        wall = Wall(0, 0, 20, 0)
        door = Door(0, 0, 0, 20, "close")
        ctx.r_points.extend([point, other])
        ctx.read_walls.append(wall)
        ctx.read_doors.append(door)

        with (
            patch("app.ui.device_inspector._redraw_editable_objects"),
            patch("read.read_walls_coordinates", [wall]),
        ):
            deleted = delete_map_selection(
                ctx,
                MapSelection("point", point),
                confirm=False,
            )

        self.assertTrue(deleted)
        self.assertNotIn(point, ctx.r_points)
        self.assertNotIn(wall, ctx.read_walls)
        self.assertNotIn(door, ctx.read_doors)

    def test_points_sensors_devices_walls_and_doors_are_selectable(self):
        class Canvas:
            _zoom_factor = 1.0

            def __init__(self):
                self.active_item = None
                self.tags = {
                    1: ("point",),
                    2: ("sensor",),
                    3: ("device",),
                    4: ("wall",),
                    5: ("door",),
                }

            def canvasx(self, value):
                return value

            def canvasy(self, value):
                return value

            def find_overlapping(self, *_args):
                return (self.active_item,)

            def gettags(self, item_id):
                return self.tags[item_id]

        point = Point("p", 10, 10)
        sensor = Sensor("s", 10, 10, "PIR", 0, 1, 1, 0)
        device = Device("d", 10, 10, "Light", 10, 0, 1, 10)
        wall = Wall(0, 10, 20, 10)
        door = Door(0, 10, 20, 10, "close")
        canvas = Canvas()
        ctx = SimpleNamespace(
            canvas=canvas,
            load_active=True,
            r_points=[point],
            read_sensors=[sensor],
            read_devices=[device],
            read_walls=[wall],
            read_doors=[door],
        )
        event = SimpleNamespace(x=10, y=10)

        for item_id, expected_kind in enumerate(
            ("point", "sensor", "device", "wall", "door"),
            start=1,
        ):
            with self.subTest(kind=expected_kind):
                canvas.active_item = item_id
                selection = _selection_at_event(ctx, event)
                self.assertEqual(selection.kind, expected_kind)

    def test_select_tool_stays_active_while_manual_interaction_is_running(self):
        class Canvas:
            def __init__(self):
                self.bindings = {}
                self.cursor = None

            def bind(self, sequence, callback):
                self.bindings[sequence] = callback

            def unbind(self, sequence):
                self.bindings.pop(sequence, None)

            def configure(self, **kwargs):
                self.cursor = kwargs.get("cursor", self.cursor)

        class Palette:
            def __init__(self):
                self.active = None

            def set_active(self, key):
                self.active = key

        class ModeVar:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        ctx = SimpleNamespace(
            canvas=Canvas(),
            scenario_menu=None,
            timer_app_instance=SimpleNamespace(is_running=True),
            _tool_palette=Palette(),
            _canvas_mode_var=ModeVar(),
        )

        with patch("app.controllers.simulation.enable_all_menus"):
            activate_select_mode(ctx)

        self.assertEqual(ctx._canvas_mode, "select")
        self.assertEqual(ctx._tool_palette.active, "select")
        self.assertIn("<ButtonPress-1>", ctx.canvas.bindings)
        self.assertIn("<B1-Motion>", ctx.canvas.bindings)
        self.assertIn("<ButtonRelease-1>", ctx.canvas.bindings)
        self.assertIn("<ButtonPress-3>", ctx.canvas.bindings)
        self.assertIn("<B3-Motion>", ctx.canvas.bindings)
        self.assertIn("<ButtonRelease-3>", ctx.canvas.bindings)
        self.assertEqual(ctx._canvas_mode_var.value, "Mode: Select / inspect")


if __name__ == "__main__":
    unittest.main()
