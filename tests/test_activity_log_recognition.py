"""Recognize, compose and log concurrent activities through a complete session."""

import copy
import csv
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import log
from activity import close_current_activity, process_activities
from house_state import HouseState
from models import Device, Sensor
from recognition_evaluation import transfer_report
from recognition_library import house_snapshot


class ConcurrentActivityLogTests(unittest.TestCase):
    def make_session(self, cooking_type="Oven", entertainment_type="TV"):
        cook = Device("food appliance", 50, 50, cooking_type, 1000, 0, 1000, 1000)
        screen = Device("entertainment", 50, 50, entertainment_type, 120, 0, 120, 120)
        sensors = [Sensor("food meter", 50, 50, "Smart Meter", 0, 5000, 1, 0,
                          associated_device=cook.name),
                   Sensor("screen meter", 50, 50, "Smart Meter", 0, 5000, 1, 0,
                          associated_device=screen.name)]
        runtime = {"points": [], "walls": [], "doors": [], "rooms": [],
                   "devices": [cook, screen], "sensors": sensors,
                   "recognition_config": {"meal_window_minutes": 10, "minimum_confidence": .5}}
        house = HouseState()
        house.sim_state()["avatar_position"] = (50, 50)
        timer = SimpleNamespace(is_running=True, elapsed_time=timedelta())
        start = datetime(2026, 10, 21, 18, 37)
        timer.get_simulated_time = lambda: (start + timedelta(
            minutes=timer.elapsed_time.total_seconds())).strftime("%H:%M")

        def observe(minute, *, cooking=False, relaxing=False):
            timer.elapsed_time = timedelta(seconds=minute)
            for sensor, device, active in zip(sensors, (cook, screen), (cooking, relaxing)):
                device.state = int(active)
                device.current_consumption = device.power if active else 0
                house.sensor_states()[sensor.name] = {
                    "type": "Smart Meter", "associated_device": device.name,
                    "state": [int(active)], "consumption": [device.current_consumption]}
            process_activities(None, timer, house.sensor_states(), house, runtime)
            return set(house.activity_state()["current_activities"])

        return house, runtime, timer, observe

    def test_relaxation_keeps_one_interval_during_and_after_the_meal(self):
        for cooking_type, screen_type in (("Oven", "TV"), ("Microwave", "PlayStation")):
            with self.subTest(source=cooking_type, target=screen_type):
                house, _runtime, timer, observe = self.make_session(cooking_type, screen_type)
                self.assertEqual(observe(0, cooking=True), {"cooking"})
                observe(1, cooking=True)
                self.assertEqual(observe(2, relaxing=True),
                                 {"eating", "relaxing", "eating while relaxing"})
                for minute in range(3, 172):
                    active = observe(minute, relaxing=True)
                    if "eating while relaxing" in active:
                        self.assertTrue({"eating", "relaxing"} <= active)
                    if minute >= 12:
                        self.assertEqual(active, {"relaxing"})
                self.assertEqual(house.activity_log_state()["active_activities"],
                                 {"relaxing": "18:39"})
                close_current_activity(timer, house_state=house)
                entries = house.activity_log_state()["activity_log"]
                relaxation = [entry for entry in entries if entry["activity"] == "relaxing"]
                self.assertEqual(relaxation, [{"activity": "relaxing", "start": "18:39", "end": "21:28"}])
                meal = next(entry for entry in entries if entry["activity"] == "eating while relaxing")
                self.assertLess(meal["end"], relaxation[0]["end"])
                self.assertEqual(house.activity_log_state()["active_activities"], {})

    def test_composite_does_not_replace_cooking_when_the_appliance_is_still_active(self):
        _house, _runtime, _timer, observe = self.make_session()
        observe(0, cooking=True)
        for minute in (2, 20, 80):
            self.assertEqual(observe(minute, cooking=True, relaxing=True), {"cooking", "relaxing"})

    def test_disabled_or_uncertain_components_cannot_create_a_composite(self):
        for disabled in ("eating", "relaxing"):
            with self.subTest(disabled=disabled):
                house, runtime, _timer, observe = self.make_session()
                runtime["recognition_config"]["rules"] = {disabled: {"weight": 0}}
                observe(0, cooking=True)
                active = observe(2, relaxing=True)
                self.assertNotIn(disabled, active)
                self.assertNotIn("eating while relaxing", active)
                result = house.activity_state()["recognition_results"]["eating while relaxing"]
                self.assertIn(disabled, result["missing_components"])
                self.assertEqual(result["confidence"], 0)

        house, runtime, _timer, observe = self.make_session()
        runtime["recognition_config"]["minimum_confidence"] = .9
        observe(0, cooking=True)
        self.assertEqual(observe(2, relaxing=True), {"relaxing"})
        self.assertFalse(house.activity_state()["recognition_results"]["eating while relaxing"]["active"])

    def test_valid_composite_keeps_each_component_and_unrelated_work(self):
        house, runtime, _timer, observe = self.make_session(entertainment_type="Computer")
        runtime["devices"][1].properties = {"working": 1, "relaxing": 1}
        observe(0, cooking=True)
        self.assertEqual(observe(2, relaxing=True),
                         {"eating", "relaxing", "working", "eating while relaxing"})
        results = house.activity_state()["recognition_results"]
        composite = results["eating while relaxing"]
        self.assertEqual(composite["components"], ["eating", "relaxing"])
        self.assertLessEqual(composite["confidence"],
                             min(results[name]["confidence"] for name in composite["components"]))

    def test_open_activity_is_visible_and_exported_without_closing_or_mutating_it(self):
        house, _runtime, _timer, observe = self.make_session()
        observe(0, relaxing=True)
        state = house.activity_log_state()
        before = copy.deepcopy(state)
        self.assertEqual(log.activity_log_entries(state),
                         [{"activity": "relaxing", "start": "18:37", "end": None}])
        text = log.activity_log_text(state)
        self.assertIn("relaxing", text)
        self.assertIn("In progress", text)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activities.csv"
            log.save_activity_log(path, state)
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(rows, [{"activity": "relaxing", "start": "18:37", "end": ""}])
        self.assertEqual(state, before)
        observe(1, relaxing=False)
        self.assertNotIn("In progress", log.activity_log_text(state))
        self.assertIn("18:38", log.activity_log_text(state))

    def test_transfer_report_requires_each_constituent_activity(self):
        for disabled in ("eating", "relaxing"):
            with self.subTest(disabled=disabled):
                _house, source_runtime, _timer, _observe = self.make_session()
                _house, target_runtime, _timer, _observe = self.make_session("Microwave", "PlayStation")
                source = house_snapshot(source_runtime, "source", "Source")
                target = house_snapshot(target_runtime, "target", "Target")
                config = copy.deepcopy(source_runtime["recognition_config"])
                self.assertTrue(transfer_report(source, target, config)["activities"][
                    "eating while relaxing"]["transferable"])
                config["rules"] = {disabled: {"weight": 0}}
                composite = transfer_report(source, target, config)["activities"]["eating while relaxing"]
                self.assertEqual(composite["property_coverage"], 1)
                self.assertFalse(composite["transferable"])
                self.assertFalse(composite["supported_in_reference"])
                self.assertIn(disabled, composite["missing_components"])

    def test_open_interval_compaction_preserves_a_real_gap_and_original_history(self):
        state = {"activity_log": [
            {"activity": "relaxing", "start": "18:39", "end": "18:50"},
            {"activity": "relaxing", "start": "19:00", "end": "19:10"}],
            "active_activities": {"relaxing": "19:10"}}
        before = copy.deepcopy(state)
        self.assertEqual(log.activity_log_entries(state), [
            {"activity": "relaxing", "start": "18:39", "end": "18:50"},
            {"activity": "relaxing", "start": "19:00", "end": None}])
        self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()
