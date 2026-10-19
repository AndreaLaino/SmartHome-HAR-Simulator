import copy
import tempfile
import unittest
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import device
import read
import sim
from app.context import AppContext
from app.io import scenario
from house_state import HouseState
from lighting import NORMAL_LIGHT, SMART_LIGHT, with_integrated_light_meters
from models import Device, Room, Sensor
from room_recognition import update_room_power


class LightingTests(unittest.TestCase):
    def test_normal_and_smart_lights_have_different_metering(self):
        normal = Device("normal", 25, 25, NORMAL_LIGHT, 10, 1, 10, 10)
        smart = Device("smart", 50, 50, SMART_LIGHT, 7.5, 1, 7.5, 7.5)
        room = Room("room", [(0, 0), (100, 0), (100, 100), (0, 100)])
        update_room_power([room], [], [normal, smart])
        self.assertEqual(room.total_power_w, 7.5)
        self.assertEqual(room.smartmeter_names, ["smartmeter_smart"])
        external = Sensor("normal meter", 25, 25, "Smart Meter", 0, 5000, 1, 0,
                          consumption=10, associated_device="normal")
        duplicate = Sensor("smart external meter", 50, 50, "Smart Meter", 0, 5000, 1, 0,
                           consumption=7.5, associated_device="smart")
        update_room_power([room], [external, duplicate], [normal, smart])
        self.assertEqual(room.total_power_w, 17.5)
        self.assertEqual(room.smartmeter_names, ["normal meter", "smartmeter_smart"])

    def test_smart_light_records_its_builtin_meter_without_a_separate_sensor(self):
        light = Device("smart", 50, 50, SMART_LIGHT, 7.5, 0, 7.5, 7.5)
        room = Room("room", [(0, 0), (100, 0), (100, 100), (0, 100)])
        runtime = {"devices": [light], "sensors": [], "rooms": [room]}
        house = HouseState()
        timer = SimpleNamespace(is_running=True, elapsed_time=timedelta(), current_date="2026-10-19",
                                get_simulated_time=lambda: "12:00")
        for expected in (7.5, 0.0):
            sim.toggle_device_state(None, None, house, timer, runtime, x=50, y=50, render=False)
            sim.update_sensors(None, timer, None, house, runtime, schedule_next=False, force=True,
                               fast=True, delta_override=48 * 3600)
            self.assertEqual(light.current_consumption, expected)
            self.assertEqual(room.total_power_w, expected)
            history = house.sensor_states()["smartmeter_smart"]
            self.assertEqual(history["type"], "Smart Meter")
            self.assertEqual(history["associated_device"], "smart")
            self.assertEqual(history["consumption"][-1], expected)
        self.assertEqual(runtime["sensors"], [])
        self.assertEqual(house.active_cycles(), {})

    def test_builtin_meter_follows_movement_copy_and_device_removal(self):
        light = Device("smart", 50, 50, SMART_LIGHT, 7.5, 1, 7.5, 7.5)
        original_meter = with_integrated_light_meters([], [light])[0]
        duplicate = copy.deepcopy(light)
        duplicate.name = "smart_copy"
        duplicate.x = 150
        meters = with_integrated_light_meters([], [light, duplicate])
        self.assertIs(meters[0], original_meter)
        self.assertIsNot(meters[1], original_meter)
        self.assertEqual(meters[1].associated_device, "smart_copy")
        self.assertEqual((meters[1].x, meters[1].y), (150, 50))
        left = Room("left", [(0, 0), (100, 0), (100, 100), (0, 100)])
        right = Room("right", [(100, 0), (200, 0), (200, 100), (100, 100)])
        light.x = 125
        update_room_power([left, right], meters, [light, duplicate])
        self.assertEqual((left.total_power_w, right.total_power_w), (0, 15))
        update_room_power([left, right], meters, [duplicate])
        self.assertEqual(right.total_power_w, 7.5)
        self.assertEqual(right.smartmeter_names, ["smartmeter_smart_copy"])

    def test_builtin_meter_avoids_name_collisions_and_stays_single_on_refresh(self):
        light = Device("smart", 50, 50, SMART_LIGHT, 7.5, 1, 7.5, 7.5)
        external = Sensor("smartmeter_smart", 0, 0, "PIR", 0, 1, 1, 0)
        original = [external]
        meters = with_integrated_light_meters(original, [light])
        self.assertEqual([meter.name for meter in meters], ["smartmeter_smart", "smartmeter_smart_2"])
        refreshed = with_integrated_light_meters(meters, [light])
        self.assertEqual(len(refreshed), 2)
        self.assertIs(refreshed[-1], meters[-1])
        self.assertEqual(original, [external])

    def test_deleting_smart_light_removes_builtin_meter_history(self):
        from app.ui.device_inspector import associated_sensor_names, delete_device

        ctx = AppContext(window=Mock(), load_active=True)
        light = Device("smart", 50, 50, SMART_LIGHT, 7.5, 1, 7.5, 7.5)
        ctx.read_devices = [light]
        self.assertEqual(associated_sensor_names(ctx, light), ["smartmeter_smart"])
        ctx.house_state.sensor_states()["smartmeter_smart"] = {"consumption": [7.5]}
        with patch("app.ui.device_inspector._redraw_editable_objects"):
            self.assertTrue(delete_device(ctx, light, confirm=False))
        self.assertEqual(ctx.read_devices, [])
        self.assertNotIn("smartmeter_smart", ctx.house_state.sensor_states())

    def test_smart_and_normal_light_types_survive_save_reload(self):
        ctx = AppContext(window=Mock(), load_active=True)
        ctx.read_devices = [Device("normal", 25, 25, NORMAL_LIGHT, 10, 1, 10, 10),
                            Device("smart", 50, 50, SMART_LIGHT, 7.5, 1, 7.5, 7.5)]
        # Even if a runtime view is passed to persistence, built-ins are derived again on load.
        ctx.read_sensors = with_integrated_light_meters([], ctx.read_devices)
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            stack.enter_context(patch.object(scenario, "ask_confirmation", return_value=True))
            stack.enter_context(patch.object(scenario.messagebox, "showinfo"))
            for name in ("coordinates", "read_walls", "read_walls_coordinates", "read_sensors", "read_devices", "read_doors"):
                stack.enter_context(patch.object(read, name, []))
            stack.enter_context(patch.object(read, "read_room_overrides", {}))
            path = Path(folder) / "lights.csv"
            self.assertTrue(scenario._write_scenario(ctx, path))
            _, _, sensors, lights, _ = read.read_coordinates_from_file(path)
            self.assertEqual([light.type for light in lights], [NORMAL_LIGHT, SMART_LIGHT])
            self.assertEqual([light.power for light in lights], [10, 7.5])
            self.assertEqual(sensors, [])
            meters = with_integrated_light_meters(sensors, lights)
            self.assertEqual(len(meters), 1)
            self.assertEqual(meters[0].associated_device, "smart")
            self.assertEqual(meters[0].consumption, 0)

    def test_lighting_stays_on_through_fast_forward_and_meter_and_room_follow_toggle(self):
        light = Device("lamp", 50, 50, "Lighting", 7.5, 0, 7.5, 7.5)
        meter = Sensor("meter", 50, 50, "Smart Meter", 0, 5000, 1, 0, associated_device="lamp")
        room = Room("room", [(0, 0), (100, 0), (100, 100), (0, 100)])
        runtime = {"devices": [light], "sensors": [meter], "rooms": [room]}
        house = HouseState()
        timer = SimpleNamespace(is_running=True, elapsed_time=timedelta(), current_date="2026-10-19",
                                get_simulated_time=lambda: "12:00")

        sim.toggle_device_state(None, None, house, timer, runtime, x=50, y=50, render=False)
        timer.current_date = "2026-10-21"
        sim.update_sensors(None, timer, None, house, runtime, schedule_next=False, force=True,
                           fast=True, delta_override=48 * 3600)
        self.assertEqual((light.state, light.current_consumption, meter.consumption, room.total_power_w),
                         (1, 7.5, 7.5, 7.5))
        self.assertEqual(house.sensor_states()["meter"]["consumption"][-1], 7.5)

        sim.toggle_device_state(None, None, house, timer, runtime, x=50, y=50, render=False)
        sim.update_sensors(None, timer, None, house, runtime, schedule_next=False, force=True, fast=True)
        self.assertEqual((light.state, light.current_consumption, meter.consumption, room.total_power_w),
                         (0, 0, 0, 0))

    def test_lighting_wattage_and_meter_association_survive_save_reload(self):
        ctx = AppContext(window=Mock(), load_active=True)
        ctx.read_devices = [Device("lamp", 50, 50, "Lighting", 7.5, 1, 7.5, 7.5, 7.5)]
        ctx.read_sensors = [Sensor("meter", 50, 50, "Smart Meter", 0, 5000, 1, 0, associated_device="lamp")]
        stores = ("coordinates", "read_walls", "read_walls_coordinates", "read_sensors", "read_devices", "read_doors")
        with tempfile.TemporaryDirectory() as folder, patch.object(scenario, "ask_confirmation", return_value=True), \
                patch.object(scenario.messagebox, "showinfo"):
            with ExitStack() as stack:
                for name in stores:
                    stack.enter_context(patch.object(read, name, []))
                stack.enter_context(patch.object(read, "read_room_overrides", {}))
                path = Path(folder) / "lighting.csv"
                self.assertTrue(scenario._write_scenario(ctx, path))
                _, _, sensors, lights, _ = read.read_coordinates_from_file(path)
                light = lights[0]
                self.assertEqual((light.type, light.power, light.min_consumption, light.max_consumption),
                                 ("Lighting", 7.5, 7.5, 7.5))
                self.assertEqual(light.state, 0)
                self.assertEqual(sensors[0].associated_device, "lamp")

    def test_lighting_dialog_accepts_fractional_watts_and_rejects_invalid_values(self):
        dialog = device.DeviceDialog.__new__(device.DeviceDialog)
        dialog.device_name = Mock(get=lambda: " new lamp ")
        dialog.device_type = Mock(get=lambda: "Lighting")
        dialog.lighting_power = Mock()
        dialog.lighting_control = Mock(get=lambda: "Manual")
        dialog.property_weights = Mock(get=lambda: "lighting=1")
        with patch.object(device, "devices", []), patch.object(device, "device_file", []), \
                patch.object(device.messagebox, "showwarning"):
            for value in ("", "bad", "0", "-1", "nan", "inf"):
                with self.subTest(value=value):
                    dialog.lighting_power.get.return_value = value
                    self.assertFalse(dialog.validate())
            dialog.lighting_power.get.return_value = "7.5"
            self.assertTrue(dialog.validate())
            dialog.apply()
            self.assertEqual(dialog.result, ("new lamp", "Lighting", 7.5, 7.5, 7.5, "manual"))


if __name__ == "__main__":
    unittest.main()
