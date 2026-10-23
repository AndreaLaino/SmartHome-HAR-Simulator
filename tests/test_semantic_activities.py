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
from activity_recognition import recognize_activities, recognition_settings
from app.context import AppContext
from app.io import scenario
from device import get_device_params
from device_properties import DEVICE_PROPERTIES, SENSOR_PROPERTIES, parse_properties
from house_state import HouseState
from models import Device, Room, Sensor


def appliance(name, kind, **kwargs):
    watts = get_device_params(kind)["power"]
    return Device(name, 50, 50, kind, watts, 0, watts, watts, **kwargs)


def meter(name, device):
    return Sensor(name, device.x, device.y, "Smart Meter", 0, 5000, 1, 0, associated_device=device.name)


class PropertyRecognitionTests(unittest.TestCase):
    def setUp(self):
        self.state = {}

    def run_sequence(self, cooking_type, relaxing_type):
        cook, relax = appliance("a", cooking_type), appliance("b", relaxing_type)
        meters = [meter("s1", cook), meter("s2", relax)]
        runtime = {"devices": [cook, relax], "sensors": meters, "avatar_position": (50, 50)}
        observations = {"s1": {"state": [1]}, "s2": {"state": [0]}}
        state = {}
        first = recognize_activities(observations, runtime, state, 0)
        observations["s1"]["state"].append(0)
        observations["s2"]["state"].append(1)
        second = recognize_activities(observations, runtime, state, 5)
        return first, second, state, runtime, observations

    def test_oven_tv_and_microwave_playstation_have_the_same_activity_and_score(self):
        first = self.run_sequence("Oven", "TV")
        other = self.run_sequence("Microwave", "PlayStation")
        self.assertEqual(first[:2], ({"cooking"}, {"eating", "relaxing", "eating while relaxing"}))
        self.assertEqual(first[:2], other[:2])
        self.assertEqual(first[2]["recognition_results"]["eating while relaxing"]["score"],
                         other[2]["recognition_results"]["eating while relaxing"]["score"])

    def test_custom_labels_and_multiple_capabilities_use_properties_not_names(self):
        device = appliance("anything at all", "Unknown model", properties=parse_properties("cooking device=1, working device=0.4"))
        runtime = {"devices": [device], "sensors": [meter("unrelated label", device)]}
        result = recognize_activities({"unrelated label": {"state": [1]}}, runtime, self.state, 0)
        self.assertEqual(result, {"cooking"})
        device.properties = parse_properties("working=0.9, relax device=0.8")
        result = recognize_activities({"unrelated label": {"state": [1]}}, runtime, {}, 1)
        self.assertIn("working", result)
        self.assertIn("relaxing", result)

    def test_computer_can_support_work_or_relaxation_after_cooking(self):
        pc = appliance("pc", "PC")
        runtime = {"devices": [pc], "sensors": [meter("meter", pc)]}
        self.assertEqual(recognize_activities({"meter": {"state": [1]}}, runtime, {}, 0), {"working"})
        # The default work-oriented PC has only weak relaxation evidence. A
        # composite must not fabricate relaxation from the previous cooking.
        after_cooking = self.run_sequence("Microwave", "Computer")[1]
        self.assertIn("working", after_cooking)
        self.assertNotIn("relaxing", after_cooking)
        self.assertNotIn("eating while relaxing", after_cooking)

    def test_reverse_or_simultaneous_use_does_not_infer_cooking_then_relaxing(self):
        cook, relax = appliance("a", "Oven"), appliance("b", "TV")
        runtime = {"devices": [cook, relax], "sensors": [meter("c", cook), meter("r", relax)]}
        states = {"c": {"state": [0]}, "r": {"state": [1]}}
        recognize_activities(states, runtime, self.state, 0)
        states["c"]["state"].append(1)
        self.assertNotIn("eating while relaxing", recognize_activities(states, runtime, self.state, 5))
        self.assertNotIn("eating while relaxing", recognize_activities(states, runtime, {}, 5))

    def test_sequence_expires_and_is_isolated_per_house(self):
        _first, _second, state, runtime, observations = self.run_sequence("Microwave", "TV")
        self.assertNotIn("eating while relaxing", recognize_activities(observations, runtime, state, 61))
        self.assertNotIn("eating while relaxing", recognize_activities(observations, runtime, {}, 6))
        self.assertNotIn("eating while relaxing", recognize_activities(observations, runtime, state, 0))

    def test_multiple_sensors_for_one_capability_do_not_inflate_support(self):
        cook = appliance("a", "Microwave", properties={"cooking": .7})
        one = meter("one", cook)
        runtime = {"devices": [cook], "sensors": [one]}
        states = {"one": {"state": [1]}}
        recognize_activities(states, runtime, self.state, 0)
        baseline = self.state["recognition_results"]["cooking"]["score"]
        runtime["sensors"].append(meter("two", cook))
        states["two"] = {"state": [1]}
        recognize_activities(states, runtime, self.state, 1)
        self.assertEqual(self.state["recognition_results"]["cooking"]["score"], baseline)

    def test_device_sensor_property_and_rule_weights_can_disable_recognition(self):
        cook = appliance("a", "Microwave")
        sm = meter("meter", cook)
        runtime = {"devices": [cook], "sensors": [sm]}
        states = {"meter": {"state": [1]}}
        for obj in (cook, sm):
            obj.recognition_weight = 0
            self.assertNotIn("cooking", recognize_activities(states, runtime, {}, 0))
            obj.recognition_weight = 1
        sm.properties["energy"] = 0
        self.assertNotIn("cooking", recognize_activities(states, runtime, {}, 0))
        sm.properties["energy"] = 1
        runtime["recognition_config"] = {"rules": {"cooking": {"weight": 0}}}
        self.assertNotIn("cooking", recognize_activities(states, runtime, {}, 0))

    def test_background_cleaning_does_not_imply_the_absent_person_is_working(self):
        washer, pc = appliance("a", "Washing_Machine"), appliance("b", "Computer")
        runtime = {"devices": [washer, pc], "sensors": [meter("a", washer), meter("b", pc)]}
        self.assertEqual(recognize_activities({"a": {"state": [1]}, "b": {"state": [1]}}, runtime,
                                             {}, 0, person_inside=False), {"laundry"})

    def test_bathroom_shower_and_handwashing_use_the_observed_fixture(self):
        for kind, expected in (("Shower", "taking a shower"), ("Toilet", "using the bathroom"), ("Sink", "washing hands")):
            fixture = appliance("unlabelled fixture", kind)
            flow = Sensor("flow", 50, 50, "Water Flow", 0, 1, 1, 0, associated_device=fixture.name)
            result = recognize_activities({"flow": {"state": [1]}}, {"devices": [fixture], "sensors": [flow]}, {}, 0)
            self.assertIn(expected, result)

    def test_all_catalogue_objects_have_properties_and_invalid_weights_are_rejected(self):
        for kind in DEVICE_PROPERTIES:
            self.assertTrue(appliance("d", kind).properties)
        for kind in SENSOR_PROPERTIES:
            self.assertTrue(Sensor("s", 0, 0, kind, 0, 1, 1, 0).properties)
        for text in ("", "cooking=-1", "cooking=nan", "cooking=inf", "cooking=2"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_properties(text)


class PropertySleepTests(unittest.TestCase):
    def setUp(self):
        self.bed = appliance("b", "Bed")
        self.light = appliance("l", "Smart Light")
        self.weight = Sensor("w", 50, 50, "Weight", 0, 1, 1, 0, associated_device="b")
        self.pir = Sensor("presence", 50, 50, "PIR", 0, 1, 1, 0)
        self.runtime = {"devices": [self.bed, self.light], "sensors": [self.weight, self.pir],
                        "rooms": [Room("r", [(0, 0), (100, 0), (100, 100), (0, 100)], inferred_type="Bedroom")]}
        self.states = {"w": {"state": [1]}, "presence": {"state": [1]}}
        self.state = {}

    def recognize(self, minute):
        return recognize_activities(self.states, self.runtime, self.state, minute)

    def test_sleep_requires_continuous_bedroom_weight_and_zero_light_power(self):
        self.assertNotIn("sleeping", self.recognize(0))
        self.assertIn("sleeping", self.recognize(10))
        self.light.state = 1
        self.assertNotIn("sleeping", self.recognize(11))
        self.light.state = 0
        self.assertNotIn("sleeping", self.recognize(12))
        self.assertIn("sleeping", self.recognize(22))

    def test_missing_weight_bedroom_or_lights_never_means_sleep(self):
        for missing in ("weight", "presence", "bedroom", "lights"):
            runtime = copy.deepcopy(self.runtime)
            observations = copy.deepcopy(self.states)
            if missing == "weight":
                observations["w"]["state"] = [0]
            elif missing == "presence":
                observations["presence"]["state"] = [0]
            elif missing == "bedroom":
                runtime["rooms"] = []
            else:
                runtime["devices"] = [runtime["devices"][0]]
            state = {}
            recognize_activities(observations, runtime, state, 0)
            self.assertNotIn("sleeping", recognize_activities(observations, runtime, state, 100))

    def test_nonzero_light_meter_vetoes_sleep_but_fridge_power_does_not(self):
        self.states["light meter"] = {"associated_device": "l", "consumption": [1]}
        self.recognize(0)
        self.assertNotIn("sleeping", self.recognize(20))
        self.states["light meter"]["consumption"] = [0]
        self.states["fridge meter"] = {"associated_device": "fridge", "consumption": [100]}
        self.recognize(21)
        self.assertIn("sleeping", self.recognize(31))


class PropertyPersistenceAndSimulationTests(unittest.TestCase):
    def test_real_simulation_sensor_updates_feed_the_same_activity_across_houses(self):
        from activity import process_activities

        for cooking_type, relaxing_type in (("Oven", "TV"), ("Microwave", "PlayStation")):
            cook, relax = appliance("cook", cooking_type), appliance("relax", relaxing_type)
            runtime = {"points": [], "walls": [], "doors": [], "devices": [cook, relax],
                       "sensors": [meter("cook meter", cook), meter("relax meter", relax)]}
            house = HouseState()
            house.sim_state()["avatar_position"] = (50, 50)
            timer = SimpleNamespace(is_running=True, elapsed_time=timedelta(), current_date="2026-10-19")
            timer.get_simulated_time = lambda: f"12:{int(timer.elapsed_time.total_seconds()):02}"
            sim.toggle_device_state(None, None, house, timer, runtime, x=50, y=50, target_device=cook, render=False)
            sim.update_sensors(None, timer, None, house, runtime, schedule_next=False, fast=True)
            process_activities(None, timer, house.sensor_states(), house, runtime)
            self.assertEqual(set(house.activity_state()["current_activities"]), {"cooking"})
            timer.elapsed_time = timedelta(seconds=5)
            sim.toggle_device_state(None, None, house, timer, runtime, x=50, y=50, target_device=cook, render=False)
            sim.toggle_device_state(None, None, house, timer, runtime, x=50, y=50, target_device=relax, render=False)
            sim.update_sensors(None, timer, None, house, runtime, schedule_next=False, fast=True)
            process_activities(None, timer, house.sensor_states(), house, runtime)
            self.assertEqual(set(house.activity_state()["current_activities"]),
                             {"eating", "relaxing", "eating while relaxing"})
            result = house.activity_state()["recognition_results"]["eating while relaxing"]
            self.assertEqual(result["sources"], ["cook meter", "relax meter"])
            self.assertTrue(result["inferred"])

    def test_water_flow_interactions_start_and_stop_fixture_activity(self):
        shower = appliance("fixture", "Shower")
        flow = Sensor("flow", 50, 50, "Water Flow", 0, 1, 1, 0, associated_device=shower.name)
        runtime = {"devices": [shower], "sensors": [flow]}
        house = HouseState()
        sim._handle_weight_interaction(house, [flow], house.sensor_states(), "12:00", (50, 50))
        self.assertIn("taking a shower", recognize_activities(house.sensor_states(), runtime, {}, 0))
        sim._handle_weight_interaction(house, [flow], house.sensor_states(), "12:01", (100, 100))
        self.assertNotIn("taking a shower", recognize_activities(house.sensor_states(), runtime, {}, 1))

    def test_example_houses_load_with_expected_rooms_and_properties(self):
        from room_recognition import recognize_rooms

        folder = Path(__file__).resolve().parents[1] / "examples" / "semantic_activities"
        with ExitStack() as stack:
            for name in ("coordinates", "read_walls", "read_sensors", "read_devices", "read_doors"):
                stack.enter_context(patch.object(read, name, []))
            stack.enter_context(patch.object(read, "read_room_overrides", {}))
            stack.enter_context(patch.object(read, "read_recognition_config", {}))
            for filename, room_types in (("oven_tv.csv", {"Kitchen", "Living room"}),
                                         ("microwave_playstation.csv", {"Kitchen", "Living room"}),
                                         ("bedroom_bathroom.csv", {"Bedroom", "Bathroom"})):
                points, walls, sensors, devices, doors = read.read_coordinates_from_file(folder / filename)
                rooms = recognize_rooms(walls, doors, sensors, devices, points)
                self.assertEqual({room.room_type for room in rooms}, room_types)
                self.assertTrue(all(obj.properties for obj in [*devices, *sensors]))

    def test_custom_rules_and_bad_timing_are_validated(self):
        config = {"rules": {"gaming": {"weights": {"gaming": 1}, "threshold": .7}}}
        obj = appliance("d", "PlayStation")
        result = recognize_activities({"m": {"state": [1]}},
                                      {"devices": [obj], "sensors": [meter("m", obj)], "recognition_config": config}, {}, 0)
        self.assertIn("gaming", result)
        for config in ({"sleep_minutes": 0}, {"meal_window_minutes": float("nan")},
                       {"rules": {"cooking": {"weights": {"cooking": 0}}}}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                recognition_settings(config)

    def test_properties_reliability_and_rules_round_trip_without_changing_legacy_columns(self):
        obj = appliance("custom, oven", "Microwave", properties={"cooking": .9, "working": .2})
        obj.recognition_weight = .8
        sm = meter("sensor ü", obj)
        sm.properties = {"energy": .75, "cooking": .3}
        sm.recognition_weight = .9
        ctx = AppContext(window=None, load_active=True, read_devices=[obj], read_sensors=[sm],
                         recognition_config={"sleep_minutes": 12, "rules": {"cooking": {"threshold": .7}}})
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            for name in ("coordinates", "read_walls", "read_sensors", "read_devices", "read_doors"):
                stack.enter_context(patch.object(read, name, []))
            stack.enter_context(patch.object(read, "read_room_overrides", {}))
            stack.enter_context(patch.object(read, "read_recognition_config", {}))
            path = Path(directory) / "house.csv"
            path.write_text(scenario._serialize_scenario(ctx), encoding="utf-8")
            _, _, sensors, devices, _ = read.read_coordinates_from_file(path)
            self.assertEqual((devices[0].properties, devices[0].recognition_weight), (obj.properties, .8))
            self.assertEqual((sensors[0].properties, sensors[0].recognition_weight), (sm.properties, .9))
            self.assertEqual(read.read_recognition_config, ctx.recognition_config)

    def test_new_electrical_devices_keep_their_wattage_and_meter_it(self):
        for kind in ("Microwave", "TV", "PlayStation", "Vacuum_Cleaner"):
            obj = appliance("appliance", kind)
            sm = meter("meter", obj)
            runtime = {"devices": [obj], "sensors": [sm], "walls": [], "doors": []}
            house = HouseState()
            timer = SimpleNamespace(is_running=True, elapsed_time=timedelta(), current_date="2026-10-19",
                                    get_simulated_time=lambda: "12:00")
            sim.toggle_device_state(None, None, house, timer, runtime, x=50, y=50, render=False)
            sim.update_sensors(None, timer, None, house, runtime, schedule_next=False, force=True, fast=True, delta_override=200)
            self.assertEqual((obj.state, obj.current_consumption, sm.consumption), (1, obj.power, obj.power))
            self.assertFalse(house.active_cycles())
            sim.toggle_device_state(None, None, house, timer, runtime, x=50, y=50, render=False)
            sim.update_sensors(None, timer, None, house, runtime, schedule_next=False, force=True, fast=True)
            self.assertEqual(sm.consumption, 0)


if __name__ == "__main__":
    unittest.main()
