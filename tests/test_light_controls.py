import copy
import tempfile
import unittest
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import read
import sim
from app.context import AppContext
from app.io import scenario
from house_state import HouseState
from lighting import NORMAL_LIGHT, SMART_LIGHT, automatic_light_states, light_switch_position
from models import Device, Door, Room, Sensor, Wall


class Canvas:
    def __init__(self):
        self.options = {}

    def canvasx(self, value):
        return value

    def canvasy(self, value):
        return value

    def delete(self, *_args):
        pass

    def create_image(self, *_args, **_kwargs):
        return 1

    def itemconfig(self, tag, **options):
        self.options.setdefault(tag, {}).update(options)


class LightControlTests(unittest.TestCase):
    def setUp(self):
        self.house = HouseState()
        self.timer = SimpleNamespace(is_running=True, elapsed_time=timedelta(), current_date="2026-10-19",
                                     get_simulated_time=lambda: "12:00")
        self.left = Room("left", [(0, 0), (100, 0), (100, 100), (0, 100)])
        self.right = Room("right", [(100, 0), (200, 0), (200, 100), (100, 100)])

    def light(self, name, x, *, kind=SMART_LIGHT, control="automatic"):
        return Device(name, x, 50, kind, 7.5, 0, 7.5, 7.5, light_control=control)

    def runtime(self, devices, sensors=()):
        return {"devices": devices, "sensors": list(sensors), "rooms": [self.left, self.right],
                "points": [], "walls": [], "doors": []}

    def tick(self, runtime):
        sim.update_sensors(None, self.timer, None, self.house, runtime, schedule_next=False,
                           force=True, fast=True, delta_override=3600)

    def test_room_presence_updates_both_light_types_and_meters_in_same_tick(self):
        left_light = self.light("left light", 50)
        right_light = self.light("right light", 150, kind=NORMAL_LIGHT)
        manual = self.light("manual", 75, control="manual")
        meter = Sensor("normal meter", 150, 50, "Smart Meter", 0, 100, 1, 0,
                       associated_device="right light")
        runtime = self.runtime([left_light, right_light, manual], [meter])
        for position, expected in (((25, 25), (1, 0)), ((175, 25), (0, 1)), ((250, 25), (0, 0))):
            self.house.sim_state()["avatar_position"] = position
            self.tick(runtime)
            self.assertEqual((left_light.state, right_light.state), expected)
            self.assertEqual(manual.state, 0)
            self.assertEqual((self.left.total_power_w, self.right.total_power_w),
                             (7.5 * expected[0], 7.5 * expected[1]))
            self.assertEqual(self.house.sensor_states()["smartmeter_left light"]["consumption"][-1], 7.5 * expected[0])
            self.assertEqual(meter.consumption, 7.5 * expected[1])

    def test_pir_presence_is_used_only_until_avatar_position_is_known(self):
        light = self.light("light", 50)
        pir = Sensor("motion", 25, 25, "PIR", 0, 1, 1, 1)
        runtime = self.runtime([light], [pir])
        self.tick(runtime)
        self.assertEqual(light.state, 1)
        self.house.sim_state()["avatar_position"] = (150, 50)
        self.tick(runtime)
        self.assertEqual(light.state, 0)
        light.state = 1
        sim.toggle_device_state(None, None, self.house, self.timer, runtime, x=50, y=50, render=False)
        self.assertEqual(light.state, 1, "direct clicks must not override automatic control")

    def test_open_plan_presence_respects_range_walls_and_closed_doors(self):
        light = self.light("light", 50)

        def desired(position, walls=(), doors=()):
            return list(automatic_light_states([light], [], [], position, walls, doors))[0][1]

        self.assertEqual(desired((150, 50)), 1)
        self.assertEqual(desired((500, 50)), 0)
        self.assertEqual(desired((150, 50), [Wall(100, 0, 100, 100)]), 0)
        self.assertEqual(desired((150, 50), doors=[Door(100, 0, 100, 100, "close")]), 0)
        self.assertEqual(desired((150, 50), doors=[Door(100, 0, 100, 100, "open")]), 1)
        self.assertEqual(desired(None), 0)

    def test_manual_switch_toggles_light_and_meter_immediately_and_stays_set(self):
        light = self.light("manual", 50, control="manual")
        runtime = self.runtime([light])
        canvas = Canvas()

        def click(x, y):
            sim.interaction(canvas, self.timer, SimpleNamespace(x=x, y=y), None, self.house, runtime)

        with patch("sim.initialize_avatar_image", return_value=object()):
            click(light.x, light.y)
            self.assertEqual(light.state, 0)
            overlapping = Device("oven", light.x, light.y, "Oven", 2000, 0, 500, 2000)
            runtime["devices"].insert(0, overlapping)
            click(*light_switch_position(light))
            self.assertEqual(light.state, 1)
            self.assertEqual(overlapping.state, 0, "a switch must control its own light even at shared coordinates")
            self.assertEqual(self.left.total_power_w, 7.5)
            self.assertEqual(self.house.sensor_states()["smartmeter_manual"]["consumption"][-1], 7.5)
            click(175, 25)
            self.tick(runtime)
            self.assertEqual(light.state, 1, "leaving a room must not turn off a manual light")
            click(*light_switch_position(light))
            self.assertEqual((light.state, light.current_consumption, self.left.total_power_w), (0, 0, 0))
            self.assertEqual(canvas.options["light_switch_label:manual"]["text"], "Switch: OFF")
            self.timer.is_running = False
            click(*light_switch_position(light))
            self.assertEqual(light.state, 0)

    def test_control_mode_survives_copy_and_save_reload_with_legacy_compatibility(self):
        lights = [self.light(f"{kind} {mode}", 50, kind=kind, control=mode)
                  for kind in (NORMAL_LIGHT, SMART_LIGHT) for mode in ("manual", "automatic")]
        duplicate = copy.deepcopy(lights[-1])
        self.assertEqual(duplicate.light_control, "automatic")
        ctx = AppContext(window=None, load_active=True, read_devices=lights)
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            stack.enter_context(patch.object(scenario, "ask_confirmation", return_value=True))
            stack.enter_context(patch.object(scenario.messagebox, "showinfo"))
            for name in ("coordinates", "read_walls", "read_walls_coordinates", "read_sensors", "read_devices", "read_doors"):
                stack.enter_context(patch.object(read, name, []))
            stack.enter_context(patch.object(read, "read_room_overrides", {}))
            path = Path(folder) / "lights.csv"
            self.assertTrue(scenario._write_scenario(ctx, path))
            _, _, _, loaded, _ = read.read_coordinates_from_file(path)
            self.assertEqual([(light.type, light.light_control) for light in loaded],
                             [(light.type, light.light_control) for light in lights])
            path.write_text("Devices\nlegacy,50,50,Lighting,10,0,10,10,0,1\n", encoding="utf-8")
            _, _, _, loaded, _ = read.read_coordinates_from_file(path)
            self.assertEqual(loaded[0].light_control, "manual")


if __name__ == "__main__":
    unittest.main()
