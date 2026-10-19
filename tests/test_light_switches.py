import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import read
from app.context import AppContext
from app.io import scenario
from app.ui import device_inspector as inspector
from house_state import HouseState
from lighting import SMART_LIGHT, add_light_switch, ensure_light_switches, manual_light_at_switch
from models import Device


class Canvas:
    _snap_to_grid = True

    def canvasx(self, value):
        return value

    def canvasy(self, value):
        return value

    def delete(self, *_args):
        pass

    def focus_set(self):
        pass

    def create_oval(self, *_args, **_kwargs):
        return 1


class LightSwitchTests(unittest.TestCase):
    def setUp(self):
        self.light = Device("lamp", 100, 100, SMART_LIGHT, 7.5, 0, 7.5, 7.5)
        self.switch = ensure_light_switches(self.light)[0]
        self.ctx = AppContext(window=None, canvas=Canvas(), load_active=True, read_devices=[self.light])
        self.ctx.house_state = HouseState()
        self.ctx._movement_undo_stack = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(inspector, "_redraw_editable_objects"))
        self.stack.enter_context(patch.object(inspector, "_draw_selections"))

    def drag(self, kind, value, target):
        selection = inspector.MapSelection(kind, value)
        inspector._set_selected_objects(self.ctx, [selection])
        with patch.object(inspector, "_selection_at_event", return_value=selection):
            inspector.begin_selection_drag(self.ctx, SimpleNamespace(x=value.x, y=value.y))
        inspector.finish_selection_drag(self.ctx, SimpleNamespace(x=target[0], y=target[1]))

    def test_switch_and_bulb_drag_independently_and_undo_preserves_both_positions(self):
        self.drag("light_switch", self.switch, (25, 150))
        self.assertEqual((self.switch.x, self.switch.y), (25, 150))
        self.assertEqual((self.light.x, self.light.y), (100, 100))
        self.assertIs(manual_light_at_switch([self.light], 25, 150), self.light)
        self.assertIsNone(manual_light_at_switch([self.light], 75, 125))
        self.drag("device", self.light, (200, 200))
        self.assertEqual((self.switch.x, self.switch.y), (25, 150))
        inspector.undo_last_movement(self.ctx)
        self.assertEqual((self.light.x, self.light.y), (100, 100))
        self.assertEqual((self.switch.x, self.switch.y), (25, 150))
        inspector.undo_last_movement(self.ctx)
        self.assertEqual((self.switch.x, self.switch.y), (75, 125))

    def test_add_remove_switches_and_undo_never_delete_the_light(self):
        second = inspector.add_switch_to_light(self.ctx, self.light)
        self.assertEqual(len(self.light.light_switches), 2)
        self.assertIs(manual_light_at_switch([self.light], second.x, second.y), self.light)
        inspector.delete_map_selection(self.ctx, inspector.MapSelection("light_switch", second), confirm=False)
        self.assertEqual(self.light.light_switches, [self.switch])
        inspector.undo_last_movement(self.ctx)
        self.assertIs(self.light.light_switches[1], second)
        inspector.undo_last_movement(self.ctx)
        self.assertEqual(self.light.light_switches, [self.switch])
        inspector.delete_map_selection(self.ctx, inspector.MapSelection("light_switch", self.switch), confirm=False)
        self.assertEqual(ensure_light_switches(self.light), [])
        self.assertIsNone(manual_light_at_switch([self.light], 75, 125))
        self.assertEqual(self.ctx.read_devices, [self.light])
        inspector.undo_last_movement(self.ctx)
        self.assertIs(self.light.light_switches[0], self.switch)

    def test_copying_light_and_switch_preserves_positions_and_correct_associations(self):
        self.switch.x, self.switch.y = 25, 150
        inspector._set_selected_objects(self.ctx, [inspector.MapSelection("device", self.light)])
        inspector.copy_selected_object(self.ctx)
        inspector.paste_map_object(self.ctx)
        duplicate = self.ctx.read_devices[-1]
        copied_switch = ensure_light_switches(duplicate)[0]
        self.assertIsNot(copied_switch, self.switch)
        self.assertEqual((duplicate.x, duplicate.y), (125, 125))
        self.assertEqual((copied_switch.x, copied_switch.y), (50, 175))
        self.assertEqual(copied_switch.associated_device, duplicate.name)
        inspector._set_selected_objects(self.ctx, [inspector.MapSelection("light_switch", self.switch)])
        inspector.copy_selected_object(self.ctx)
        inspector.paste_map_object(self.ctx)
        extra = self.light.light_switches[-1]
        self.assertEqual(extra.associated_device, self.light.name)
        self.assertEqual((extra.x, extra.y), (50, 175))
        self.assertIsNot(extra, self.switch)
        self.assertEqual(len(duplicate.light_switches), 1)

    def test_deleted_light_restores_all_of_its_switches_on_undo(self):
        second = add_light_switch(self.light, 225, 75)
        inspector.delete_device(self.ctx, self.light, confirm=False)
        self.assertEqual(inspector.current_light_switches(self.ctx), [])
        inspector.undo_last_movement(self.ctx)
        self.assertEqual(inspector.current_light_switches(self.ctx), [self.switch, second])
        self.assertIs(self.ctx.read_devices[0], self.light)

    def test_positions_multiple_switches_and_empty_list_survive_save_reload(self):
        empty = Device("no switches", 200, 200, SMART_LIGHT, 10, 0, 10, 10, light_switches=[])
        self.ctx.read_devices.append(empty)
        self.switch.x, self.switch.y = 0, 250
        second = add_light_switch(self.light, 225, 75)
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            stack.enter_context(patch.object(scenario, "ask_confirmation", return_value=True))
            stack.enter_context(patch.object(scenario.messagebox, "showinfo"))
            for name in ("coordinates", "read_walls", "read_walls_coordinates", "read_sensors", "read_devices", "read_doors"):
                stack.enter_context(patch.object(read, name, []))
            stack.enter_context(patch.object(read, "read_room_overrides", {}))
            path = Path(folder) / "switches.csv"
            self.assertTrue(scenario._write_scenario(self.ctx, path))
            _, _, _, devices, _ = read.read_coordinates_from_file(path)
            loaded = ensure_light_switches(devices[0])
            self.assertEqual([(switch.name, switch.x, switch.y) for switch in loaded],
                             [(self.switch.name, 0, 250), (second.name, 225, 75)])
            self.assertEqual(ensure_light_switches(devices[1]), [])
            path.write_text("Devices\nlegacy,100,100,Smart Light,7.5,0,7.5,7.5,0,1,manual\n", encoding="utf-8")
            _, _, _, devices, _ = read.read_coordinates_from_file(path)
            self.assertEqual(len(ensure_light_switches(devices[0])), 1)


if __name__ == "__main__":
    unittest.main()
