"""UI workflow checks; real Tk tests opt in with SIMULATOR_GUI_TESTS=1."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from app.context import AppContext
from app.ui.learned_har import LearnedHARWindow, format_evaluation, model_display_names, parse_labels
from har import catalogue, storage
from models import Device, Sensor


class LearnedHARPresentationTests(unittest.TestCase):
    def test_labels_are_multiple_observed_activities(self):
        self.assertEqual(parse_labels(" cooking, relaxing, cooking,  "), ["cooking", "relaxing"])
        self.assertEqual(parse_labels(""), [])

    def test_model_names_use_source_metadata_or_source_recordings_and_creation_order(self):
        models = [
            {"model_id": "later", "source_house_id": "source-a", "created_at": "2026-10-28T13:00:00+00:00"},
            {"model_id": "first", "source_house_id": "source-a", "created_at": "2026-10-28T12:00:00+00:00"},
            {"model_id": "next-day", "source_house_id": "source-a", "created_at": "2026-10-29T12:00:00+00:00"},
            {"model_id": "other", "source_house_id": "source-b", "source_house_label": "House B",
             "created_at": "2026-10-28T12:00:00+00:00"},
        ]
        sessions = [
            {"house_id": "source-a", "house_label": "House A", "partition": "source"},
            {"house_id": "source-a", "house_label": "Wrong target label", "partition": "target"},
            {"house_id": "source-b", "house_label": "Old house name", "partition": "source"},
        ]
        before = deepcopy((models, sessions))
        expected = {
            "first": "House_A_2026-10-28_001", "later": "House_A_2026-10-28_002",
            "next-day": "House_A_2026-10-29_001", "other": "House_B_2026-10-28_001",
        }
        self.assertEqual(model_display_names(models, sessions), expected)
        self.assertEqual(model_display_names(list(reversed(models)), list(reversed(sessions))), expected)
        self.assertEqual((models, sessions), before)

    def test_model_names_keep_saved_names_and_disambiguate_same_visible_house_names(self):
        models = [
            {"model_id": "legacy", "source_house_id": "a", "source_house_label": "House A",
             "created_at": "2026-10-28T12:00:00+00:00"},
            {"model_id": "saved", "source_house_id": "a", "source_house_label": "House A",
             "created_at": "2026-10-28T13:00:00+00:00", "display_name": "House_A_2026-10-28_002"},
            {"model_id": "fresh", "source_house_id": "a", "source_house_label": "House A",
             "created_at": "2026-10-28T14:00:00+00:00"},
            {"model_id": "different-house", "source_house_id": "b", "source_house_label": "House_A",
             "created_at": "2026-10-28T15:00:00+00:00"},
            {"model_id": "unicode", "source_house_id": "c", "source_house_label": "  Mia   càsa  ",
             "created_at": "2026-10-28T12:00:00+00:00"},
            {"model_id": "unknown", "source_house_id": "d"},
        ]
        before = deepcopy(models)
        names = model_display_names(models, [])
        self.assertEqual(names["legacy"], "House_A_2026-10-28_001")
        self.assertEqual(names["saved"], "House_A_2026-10-28_002")
        self.assertEqual(names["fresh"], "House_A_2026-10-28_003")
        self.assertEqual(names["different-house"], "House_A_2026-10-28_004")
        self.assertEqual(names["unicode"], "Mia_càsa_2026-10-28_001")
        self.assertEqual(names["unknown"], "Unknown_house_undated_001")
        self.assertEqual(len(set(names.values())), len(models))
        self.assertEqual(models, before)

    def test_model_names_have_deterministic_ties_and_safe_missing_metadata_fallbacks(self):
        models = [
            {"model_id": "b", "source_house_id": "unknown", "created_at": "invalid"},
            {"model_id": "a", "source_house_id": "unknown", "created_at": None},
            {"model_id": "c", "source_house_id": "unknown", "display_name": "Kept readable name"},
            {"model_id": "d", "source_house_id": "unknown", "display_name": "Kept readable name"},
        ]
        names = model_display_names(models, [
            {"house_id": "unknown", "partition": "target", "house_label": "Not the source name"},
        ])
        self.assertEqual(names["a"], "Unknown_house_undated_001")
        self.assertEqual(names["b"], "Unknown_house_undated_002")
        self.assertEqual(names["c"], "Kept readable name")
        self.assertEqual(len(set(names.values())), len(models))
        self.assertEqual(model_display_names(list(reversed(models)), []), names)

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

    def test_readable_legacy_model_choices_preserve_selection_ids_and_saved_files(self):
        dictionary = catalogue.load_catalogue()
        session_path = storage.save_session({
            "schema_version": 1, "session_id": "legacy-source-session", "house_id": "source-house",
            "house_label": "House A", "partition": "source", "catalogue": dictionary, "frames": [],
        })
        first_id, selected_id, added_id = "a" * 32, "b" * 32, "c" * 32
        models = [
            {"model_id": first_id, "source_house_id": "source-house", "catalogue": dictionary,
             "created_at": "2026-10-28T12:00:00+00:00"},
            {"model_id": selected_id, "source_house_id": "source-house", "catalogue": dictionary,
             "created_at": "2026-10-28T13:00:00+00:00"},
        ]
        paths = [storage.save_model(model) for model in models]
        original_files = {path: path.read_bytes() for path in [*paths, session_path]}
        self.ctx.har_config.update(house_label="House B", model_id=selected_id)
        config_before = self.ctx.har_config.copy()
        self.ui.refresh_data()
        self.assertEqual(set(self.ui.model_choices), {"House_A_2026-10-28_001", "House_A_2026-10-28_002"})
        choice = next(label for label, identifier in self.ui.model_choices.items() if identifier == selected_id)
        self.ui.model.set(choice)
        with patch("har.learning.validate_model", side_effect=lambda model: model):
            self.ui.activate_button.invoke()
        self.assertIn(choice, self.ui.model_status.get())
        self.assertNotIn(selected_id, self.ui.model_status.get())
        self.assertEqual(self.ctx.har_config, config_before)

        # Adding an earlier legacy model can renumber fallback labels; selection
        # must continue to identify the same model rather than follow its old text.
        added = dict(models[0], model_id=added_id, created_at="2026-10-28T12:30:00+00:00")
        storage.save_model(added)
        self.ui.refresh_data()
        self.assertEqual(self.ui.model_choices[self.ui.model.get()], selected_id)
        self.assertIn(self.ui.model.get(), self.ui.model_status.get())
        self.ui.close()
        self.ui = LearnedHARWindow(self.ctx)
        self.pump()
        active_name = next(label for label, identifier in self.ui.model_choices.items() if identifier == selected_id)
        self.assertIn(active_name, self.ui.model_status.get())
        self.assertEqual(self.ctx.har_config, config_before)
        for path, content in original_files.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual({path.name for path in (storage.DATA_DIR / "models").glob("*.json")}, {
            f"{first_id}.json", f"{selected_id}.json", f"{added_id}.json",
        })

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
        self.ctx.har_config.update(house_id="target_house", house_label="Target")
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
        self.assertEqual(model["source_house_label"], "Source")
        self.assertRegex(model["display_name"], r"^Source_\d{4}-\d{2}-\d{2}_001$")
        self.assertIn(model["display_name"], self.ui.model_status.get())
        self.assertEqual(self.ui.model_choices[self.ui.model.get()], model["model_id"])
        self.assertTrue((storage.DATA_DIR / "models" / f"{model['model_id']}.json").is_file())
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
