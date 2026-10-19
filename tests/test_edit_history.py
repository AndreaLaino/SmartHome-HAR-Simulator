import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from app.context import AppContext
from app.ui import device_inspector as inspector
from lighting import SMART_LIGHT, ensure_light_switches, with_integrated_light_meters
from models import Device, Door, Point, Sensor, Wall


class Canvas:
    def canvasx(self, value):
        return value

    def canvasy(self, value):
        return value

    def focus_set(self):
        pass

    def delete(self, *_args):
        pass


class EditHistoryTests(unittest.TestCase):
    def setUp(self):
        self.light = Device("lamp", 100, 100, SMART_LIGHT, 10, 0, 10, 10)
        self.switch = ensure_light_switches(self.light)[0]
        self.ctx = AppContext(window=None, canvas=Canvas(), load_active=True, read_devices=[self.light])
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(inspector, "_redraw_editable_objects"))
        self.stack.enter_context(patch.object(inspector, "_draw_selections"))
        self.geometry = self.stack.enter_context(patch("read.read_walls_coordinates", []))

    def drag(self, kind, value, x, y):
        selection = inspector.MapSelection(kind, value)
        inspector._set_selected_objects(self.ctx, [selection])
        with patch.object(inspector, "_selection_at_event", return_value=selection):
            inspector.begin_selection_drag(self.ctx, SimpleNamespace(x=value.x, y=value.y))
        inspector.finish_selection_drag(self.ctx, SimpleNamespace(x=x, y=y))

    def test_multiple_undo_redo_steps_preserve_independent_light_and_switch_positions(self):
        self.drag("device", self.light, 200, 200)
        self.drag("light_switch", self.switch, 25, 150)
        for _ in range(2):
            inspector.undo_last_movement(self.ctx)
        self.assertEqual((self.light.x, self.light.y, self.switch.x, self.switch.y), (100, 100, 75, 125))
        self.assertIsNone(inspector.undo_last_movement(self.ctx))
        inspector.redo_last_movement(self.ctx)
        self.assertEqual((self.light.x, self.light.y, self.switch.x, self.switch.y), (200, 200, 75, 125))
        inspector.redo_last_movement(self.ctx)
        self.assertEqual((self.light.x, self.light.y, self.switch.x, self.switch.y), (200, 200, 25, 150))
        self.assertIsNone(inspector.redo_last_movement(self.ctx))
        inspector.undo_last_movement(self.ctx)
        inspector.redo_last_movement(self.ctx)
        self.assertEqual((self.switch.x, self.switch.y), (25, 150))

    def test_redo_wall_creation_restores_both_wall_stores_without_duplicates(self):
        wall = Wall(0, 0, 100, 0)
        self.ctx.r_points[:] = [Point("a", 0, 0), Point("b", 100, 0)]
        self.ctx.read_walls.append(wall)
        self.geometry.append(wall)
        inspector.record_wall_addition(self.ctx, wall)
        for _ in range(3):
            inspector.undo_last_movement(self.ctx)
            self.assertEqual((self.ctx.read_walls, self.geometry), ([], []))
            inspector.redo_last_movement(self.ctx)
            self.assertEqual((self.ctx.read_walls, self.geometry), ([wall], [wall]))
            self.assertIs(self.ctx.read_walls[0], wall)

    def test_redo_point_deletion_removes_connected_segments_and_restores_list_order_on_undo(self):
        corner, other = Point("a", 0, 0), Point("b", 100, 0)
        wall, door = Wall(0, 0, 100, 0), Door(0, 0, 0, 100, "close")
        self.ctx.r_points[:] = [corner, other]
        self.ctx.read_walls.append(wall)
        self.ctx.read_doors.append(door)
        self.geometry.append(wall)
        inspector.delete_map_selection(self.ctx, inspector.MapSelection("point", corner), confirm=False)
        for _ in range(2):
            inspector.undo_last_movement(self.ctx)
            self.assertEqual(self.ctx.r_points, [corner, other])
            self.assertEqual((self.ctx.read_walls, self.ctx.read_doors, self.geometry), ([wall], [door], [wall]))
            inspector.redo_last_movement(self.ctx)
            self.assertEqual(self.ctx.r_points, [other])
            self.assertEqual((self.ctx.read_walls, self.ctx.read_doors, self.geometry), ([], [], []))

    def test_redo_deletion_repeats_cleanup_and_next_undo_restores_updated_meter_history(self):
        sensor = Sensor("meter", 50, 50, "Smart Meter", 0, 100, 1, 0, associated_device="lamp")
        self.ctx.read_sensors.append(sensor)
        meter = with_integrated_light_meters([], [self.light])[0]
        self.ctx.house_state.sensor_states()[meter.name] = {"consumption": [10]}
        self.ctx.house_state.sensor_states()[sensor.name] = {"consumption": [7]}
        self.ctx.house_state.active_cycles()[self.light.name] = {"start_time": "original"}
        inspector.delete_device(self.ctx, self.light, confirm=False)
        inspector.undo_last_movement(self.ctx)
        self.ctx.house_state.sensor_states()[meter.name]["consumption"].append(12)
        self.ctx.house_state.active_cycles()[self.light.name]["start_time"] = "updated"
        inspector.redo_last_movement(self.ctx)
        self.assertEqual(self.ctx.read_devices, [])
        self.assertIsNone(sensor.associated_device)
        self.assertNotIn(meter.name, self.ctx.house_state.sensor_states())
        self.assertNotIn(self.light.name, self.ctx.house_state.active_cycles())
        inspector.undo_last_movement(self.ctx)
        self.assertIs(self.ctx.read_devices[0], self.light)
        self.assertIs(self.light.light_switches[0], self.switch)
        self.assertEqual(sensor.associated_device, self.light.name)
        self.assertEqual(self.ctx.house_state.sensor_states()[meter.name]["consumption"], [10, 12])
        self.assertEqual(self.ctx.house_state.sensor_states()[sensor.name]["consumption"], [7])
        self.assertEqual(self.ctx.house_state.active_cycles()[self.light.name]["start_time"], "updated")

    def test_redo_point_deletion_updates_legacy_wall_names_in_a_new_scenario(self):
        corner, other = Point("a", 0, 0), Point("b", 100, 0)
        wall = Wall(0, 0, 100, 0)
        self.ctx.load_active = False
        with patch("point.points", [corner, other]) as points, \
                patch("wall.walls", ["a", "b"]) as names, \
                patch("wall.walls_coordinates", [wall]) as walls, \
                patch("device.devices", []), patch("sensor.sensors", []), patch("door.doors", []):
            inspector.delete_map_selection(self.ctx, inspector.MapSelection("point", corner), confirm=False)
            self.assertEqual((points, names, walls), ([other], [], []))
            for _ in range(2):
                inspector.undo_last_movement(self.ctx)
                self.assertEqual((points, names, walls), ([corner, other], ["a", "b"], [wall]))
                inspector.redo_last_movement(self.ctx)
                self.assertEqual((points, names, walls), ([other], [], []))

    def test_sensor_deletion_redo_keeps_latest_history_for_the_next_undo(self):
        sensor = Sensor("motion", 50, 50, "PIR", 0, 1, 1, 0)
        self.ctx.read_sensors.append(sensor)
        self.ctx.house_state.sensor_states()[sensor.name] = {"state": [0, 1]}
        inspector.delete_map_selection(self.ctx, inspector.MapSelection("sensor", sensor), confirm=False)
        inspector.undo_last_movement(self.ctx)
        self.ctx.house_state.sensor_states()[sensor.name]["state"].append(0)
        inspector.redo_last_movement(self.ctx)
        self.assertEqual(self.ctx.read_sensors, [])
        self.assertNotIn(sensor.name, self.ctx.house_state.sensor_states())
        inspector.undo_last_movement(self.ctx)
        self.assertIs(self.ctx.read_sensors[0], sensor)
        self.assertEqual(self.ctx.house_state.sensor_states()[sensor.name]["state"], [0, 1, 0])

    def test_switch_addition_and_removal_can_be_repeated_without_removing_the_light(self):
        added = inspector.add_switch_to_light(self.ctx, self.light)
        inspector.delete_map_selection(self.ctx, inspector.MapSelection("light_switch", added), confirm=False)
        for _ in range(2):
            inspector.undo_last_movement(self.ctx)
            self.assertIs(self.light.light_switches[1], added)
            inspector.undo_last_movement(self.ctx)
            self.assertEqual(self.light.light_switches, [self.switch])
            inspector.redo_last_movement(self.ctx)
            self.assertIs(self.light.light_switches[1], added)
            inspector.redo_last_movement(self.ctx)
            self.assertEqual(self.light.light_switches, [self.switch])
            self.assertEqual(self.ctx.read_devices, [self.light])

    def test_new_move_discards_only_the_redo_branch(self):
        self.drag("device", self.light, 200, 200)
        self.drag("device", self.light, 300, 300)
        inspector.undo_last_movement(self.ctx)
        self.drag("device", self.light, 150, 150)
        self.assertIsNone(inspector.redo_last_movement(self.ctx))
        self.assertEqual((self.light.x, self.light.y), (150, 150))
        inspector.undo_last_movement(self.ctx)
        self.assertEqual((self.light.x, self.light.y), (200, 200))
        inspector.undo_last_movement(self.ctx)
        self.assertEqual((self.light.x, self.light.y), (100, 100))

    def test_paste_clears_redo_and_can_itself_be_undone_and_redone(self):
        self.drag("device", self.light, 200, 200)
        inspector.undo_last_movement(self.ctx)
        inspector._set_selected_objects(self.ctx, [inspector.MapSelection("device", self.light)])
        inspector.copy_selected_object(self.ctx)
        inspector.paste_map_object(self.ctx)
        duplicate = self.ctx.read_devices[-1]
        self.assertIsNone(inspector.redo_last_movement(self.ctx))
        inspector.undo_last_movement(self.ctx)
        self.assertEqual(self.ctx.read_devices, [self.light])
        inspector.redo_last_movement(self.ctx)
        self.assertIs(self.ctx.read_devices[-1], duplicate)
        self.assertEqual(duplicate.light_switches[0].associated_device, duplicate.name)
        self.assertEqual((self.light.x, self.light.y), (100, 100))


if __name__ == "__main__":
    unittest.main()
