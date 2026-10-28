"""UI workflow checks; real Tk tests opt in with SIMULATOR_GUI_TESTS=1."""
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from app.context import AppContext
from app.ui.learned_har import LearnedHARWindow, format_evaluation, parse_labels
from har import catalogue, storage
from models import Device, Sensor


class LearnedHARPresentationTests(unittest.TestCase):
    def test_labels_are_multiple_observed_activities(self):
        self.assertEqual(parse_labels(" cooking, relaxing, cooking,  "), ["cooking", "relaxing"])
        self.assertEqual(parse_labels(""), [])

    def test_report_distinguishes_evaluation_from_confidence(self):
        text = format_evaluation({"labelled_samples": 10, "exact_match_accuracy": .8,
                                  "micro_f1": .9, "test_houses": ["target"],
                                  "excluded_source_sessions": 3,
                                  "per_activity": {"cooking": {"precision": 1, "recall": .75, "f1": .86}}})
        self.assertIn("Exact activity-set accuracy: 80.0%", text)
        self.assertIn("cooking: precision 100.0%", text)
        self.assertIn("Source sessions excluded: 3", text)
        self.assertIn("Record labelled target sessions", format_evaluation({"labelled_samples": 0}))
        self.assertIn("SYNTHETIC DEMONSTRATION", format_evaluation({"labelled_samples": 10, "scope": "synthetic"}))


@unittest.skipUnless(os.environ.get("SIMULATOR_GUI_TESTS") == "1", "Set SIMULATOR_GUI_TESTS=1 for real Tk tests")
class LearnedHARWindowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(storage, "DATA_DIR", Path(self.temp.name)).start()
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda _type, error, _trace: self.errors.append(error)
        self.addCleanup(self.close_root)
        self.oven = Device("source_oven", 20, 20, "Oven", 1000, 0, 1000, 1000)
        self.pir = Sensor("source_motion", 30, 30, "PIR", 0, 1, 1, 0)
        self.ctx = AppContext(window=self.root, load_active=True)
        self.ctx.read_devices = [self.oven]
        self.ctx.read_sensors = [self.pir]
        self.ui = LearnedHARWindow(self.ctx)
        self.pump()

    def close_root(self):
        if self.root.winfo_exists():
            for job in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.tk.call("after", "cancel", job)
            self.root.destroy()

    def pump(self):
        self.root.update_idletasks()
        self.root.update()
        self.assertEqual(self.errors, [])

    def select_object(self, name):
        key = next(key for key, (_, obj) in self.ui.objects.items() if obj.name == name)
        self.ui.object_tree.selection_set(key)
        self.ui._select_object()
        self.pump()

    def choose_categories(self, names):
        self.ui.category_list.selection_clear(0, tk.END)
        for index in range(self.ui.category_list.size()):
            if self.ui.category_list.get(index) in names:
                self.ui.category_list.selection_set(index)

    def test_categories_are_shared_type_defaults_without_activity_assignment(self):
        self.select_object("source_oven")
        self.assertEqual(self.ui._chosen_categories(), ["Device_Cooking"])
        self.choose_categories(["Device_Cooking", "Device_Cleaning"])
        self.ui.apply_categories()
        self.assertEqual(set(self.oven.categories), {"Device_Cooking", "Device_Cleaning"})
        self.ui.save_type_defaults()
        self.assertIsNone(self.oven.categories)
        other_oven = Device("another_house_oven", 0, 0, "Oven", 1000, 0, 1000, 1000)
        self.assertEqual(set(catalogue.categories_for(other_oven, "device")), {"Device_Cooking", "Device_Cleaning"})
        self.assertNotIn("activities", catalogue.load_catalogue())
        self.ui.new_kind.set("sensor")
        self.ui.new_category.set("Sensor_DoorPresence")
        self.ui.new_description.set("Presence at a doorway")
        self.ui.new_measurement.set("presence")
        self.ui.new_unit.set("binary")
        self.ui.add_category()
        self.assertEqual(catalogue.load_catalogue()["sensor_categories"]["Sensor_DoorPresence"]["measurement"], "presence")
        self.select_object("source_motion")
        self.choose_categories([])
        self.ui.apply_categories()
        self.assertEqual(self.pir.categories, [])

    def test_ground_truth_requires_explicit_no_activity_and_identity_cannot_change_mid_recording(self):
        calls = []
        with patch("har.runtime.set_recording_labels", side_effect=lambda ctx, labels: calls.append(labels)), \
                patch("har.runtime.recording_status", return_value={"recording": True, "frames": 4, "labels": None}):
            with self.assertRaisesRegex(ValueError, "Enter an activity"):
                self.ui.apply_labels()
            self.ui.labels.set("cooking, relaxing")
            self.ui.apply_labels()
            self.assertEqual(calls[-1], ["cooking", "relaxing"])
            self.ui.no_activity.set(True)
            with self.assertRaisesRegex(ValueError, "Clear activity"):
                self.ui.apply_labels()
            self.ui.labels.set("")
            self.ui.apply_labels()
            self.assertEqual(calls[-1], [])
            self.ui.house_name.set("Copied house")
            with self.assertRaisesRegex(ValueError, "Stop and save"):
                self.ui.new_identity()
            self.select_object("source_oven")
            self.choose_categories([])
            with self.assertRaisesRegex(ValueError, "Stop and save"):
                self.ui.apply_categories()
            self.assertIsNone(self.oven.categories)
        self.ui.house_name.set("Target house")
        self.ui.new_identity()
        self.assertEqual(self.ctx.har_config["house_label"], "Target house")
        self.assertIsNone(self.ctx.har_config["model_id"])

    def test_worker_returns_to_main_thread_and_saved_model_activates(self):
        seen = []
        self.ui._run_worker(lambda: {"answer": 42}, lambda value: seen.append(value))
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            self.pump()
            time.sleep(.02)
        self.assertEqual(seen, [{"answer": 42}])
        self.assertFalse(self.ui._busy)
        saved = {"model_id": "test_model", "source_house_id": "source_house", "catalogue": catalogue.load_catalogue()}
        storage.save_model(saved)
        self.ui.refresh_data()
        with patch("har.learning.validate_model", return_value=saved):
            self.ui.activate_model()
        self.assertEqual(self.ctx.har_config["model_id"], "test_model")
        self.ui.deactivate_model()
        self.assertIsNone(self.ctx.har_config["model_id"])

    def test_recording_can_start_paused_and_saves_unlabelled_and_labelled_frames(self):
        from types import SimpleNamespace
        from har.runtime import current_runtime, process_frame

        self.ui.house_name.set("Recorded source")
        with self.assertRaisesRegex(ValueError, "Simulation → Manual"):
            self.ui.start_recording()
        self.ctx.timer_app_instance = SimpleNamespace(is_running=False)
        self.ui.start_recording()
        process_frame({}, current_runtime(self.ctx), {}, 0, house_state=self.ctx.house_state)
        self.ui.labels.set("cooking")
        self.ui.apply_labels()
        process_frame({}, current_runtime(self.ctx), {}, 60, house_state=self.ctx.house_state)
        self.ui.stop_recording()
        sessions = storage.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["house_label"], "Recorded source")
        self.assertIsNone(sessions[0]["frames"][0]["labels"])
        self.assertEqual(sessions[0]["frames"][1]["labels"], ["cooking"])
        self.assertEqual(str(self.ui.stop_button.cget("state")), "disabled")

    def test_real_source_training_target_evaluation_and_activation(self):
        dictionary = catalogue.load_catalogue()
        for index in range(4):
            source = index < 3
            frames = []
            for tick in range(12):
                on = int(tick >= 6)
                frames.append({
                    "time_seconds": tick * (60 + index * 5), "labels": ["cooking"] if on else [],
                    "devices": [{"name": "oven" if source else "renamed_microwave", "type": "Oven" if source else "Microwave",
                                 "x": 0, "y": 0, "categories": ["Device_Cooking"], "state": on,
                                 "power": 1000 if source else 700, "max_consumption": 1000 if source else 700,
                                 "current_consumption": on * (1000 if source else 700)}],
                    "sensors": [], "sensor_states": {}, "rooms": [],
                })
            storage.save_session({
                "schema_version": 1, "session_id": f"session_{index}", "simulation_session_id": f"simulation_{index}",
                "house_id": "source_house" if source else "target_house", "house_label": "Source" if source else "Target",
                "partition": "source" if source else "target", "catalogue": dictionary, "frames": frames,
            })
        self.ui.refresh_data()
        self.ui.train()
        deadline = time.monotonic() + 30
        while self.ui._busy and time.monotonic() < deadline:
            self.pump()
            time.sleep(.02)
        self.assertFalse(self.ui._busy)
        self.assertIn("Training completed", self.ui.status.get())
        self.assertIn("Labelled test observations: 12", self.ui.report_text.get("1.0", tk.END))
        self.ui.activate_model()
        model = storage.load_model(self.ctx.har_config["model_id"])
        self.assertEqual(model["source_house_id"], "source_house")
        self.assertTrue(list((storage.DATA_DIR / "reports").glob("*_evaluation.json")))
        dictionary["device_categories"]["Device_Defrosting"] = "Additional appliance capability."
        catalogue.save_catalogue(dictionary)
        self.oven.categories = ["Device_Cooking", "Device_Defrosting"]
        self.ui.activate_model()
        self.assertIn("Extra categories unused", self.ui.status.get())
        self.ui.deactivate_model()
        dictionary["sensor_categories"]["Sensor_Power"]["unit"] = "kW"
        catalogue.save_catalogue(dictionary)
        with self.assertRaises(ValueError):
            self.ui.activate_model()
        self.assertIsNone(self.ctx.har_config["model_id"])

    def test_synthetic_demo_is_explicit_repeatable_and_does_not_change_current_house(self):
        config_before = self.ctx.har_config.copy()
        self.ui.add_demo_sessions()
        self.assertEqual(len(storage.list_sessions()), 10)
        self.assertEqual(self.ui.source_houses[self.ui.source.get()], "demo-source")
        self.assertIn("Synthetic demonstration only", self.ui.report_text.get("1.0", tk.END))
        self.assertEqual(self.ctx.har_config, config_before)
        self.ui.add_demo_sessions()
        sessions = storage.list_sessions()
        self.assertEqual(len(sessions), 10)
        self.assertTrue(all(session.get("provenance") == "scripted-synthetic" for session in sessions))

    def test_layout_keeps_primary_controls_inside_minimum_window(self):
        self.ui.window.geometry("900x650")
        for tab in (self.ui.category_tab, self.ui.record_tab, self.ui.train_tab):
            self.ui.tabs.select(tab)
            self.pump()
            for widget in (self.ui.category_list,) if tab is self.ui.category_tab else \
                    (self.ui.start_button, self.ui.stop_button) if tab is self.ui.record_tab else \
                    (self.ui.train_button, self.ui.activate_button, self.ui.evaluate_button, self.ui.demo_button):
                bottom = widget.winfo_rooty() + widget.winfo_height()
                self.assertLessEqual(bottom, self.ui.window.winfo_rooty() + self.ui.window.winfo_height())
                self.assertGreater(widget.winfo_width(), 1)
