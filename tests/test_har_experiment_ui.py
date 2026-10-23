"""Opt-in real Tk checks for the controlled HAR experiment window.

Run with SIMULATOR_GUI_TESTS=1. Every generated report stays in a temporary
library; most tests replace the worker backend with deterministic results.
"""

import copy
import gc
import json
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import recognition_library
from app.context import AppContext
from app.ui.har_experiment import open_har_experiment


GUI_ENABLED = os.environ.get("SIMULATOR_GUI_TESTS") == "1"


def example_report(output):
    target = {"labelled_samples": 12, "exact_match_accuracy": .75, "micro_f1": .8,
              "macro_f1": .6, "coverage": .9, "unsupported_labels": ["showering"],
              "per_activity": {"cooking": {"precision": .8, "recall": 1, "f1": .89, "support": 8}},
              "per_house": {"target-microwave": {"labelled_samples": 12, "exact_match_accuracy": .75,
                                                 "micro_f1": .8}}}
    source = {"labelled_samples": 10, "exact_match_accuracy": .9, "micro_f1": .92,
              "macro_f1": .9, "coverage": 1}
    methods = {name: {"label": label, "target": copy.deepcopy(target), "source": copy.deepcopy(source)}
               for name, label in (("reference_rules", "Reference-transfer rules"),
                                   ("direct_rules", "Direct-property rules"),
                                   ("semantic_rf", "Semantic Random Forest"), ("raw_rf", "Raw Random Forest"))}
    return {"scope": "synthetic", "source_house_id": "source-oven",
            "training_runs": ["train-a", "train-b", "train-c"], "validation_runs": ["validation-d"],
            "source_test_runs": ["source-test-e"], "target_test_runs": ["target-test-f"],
            "methods": methods, "output_dir": str(output), "model_fingerprint": "mock-fingerprint",
            "calibration": {"semantic_rf": {"cooking": {"method": "uncalibrated", "positive_count": 2,
                                                          "negative_count": 3,
                                                          "reason": "Not enough source validation examples."}}},
            "limitations": ["Scripted synthetic labels demonstrate controlled behavior only."]}


@unittest.skipUnless(GUI_ENABLED, "Set SIMULATOR_GUI_TESTS=1 to run real Tk experiment tests")
class HARExperimentUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda _type, error, _traceback: self.callback_errors.append(error)
        self.stack = ExitStack()
        self.addCleanup(self.close)
        self.directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.stack.enter_context(patch.object(recognition_library, "LIBRARY_DIR", self.directory / "library"))
        self.stack.enter_context(patch("requests.sessions.Session.request",
                                       side_effect=AssertionError("GUI tests cannot access the network")))
        self.ctx = AppContext(window=self.root)
        self.window = open_har_experiment(self.ctx)
        self.pump()

    def close(self):
        root, window = self.root, self.window
        try:
            if root.winfo_exists():
                for job in root.tk.splitlist(root.tk.call("after", "info")):
                    root.tk.call("after", "cancel", job)
                root.destroy()
            # Break test-handle/widget cycles explicitly. A later numerical
            # worker must not become the thread that finalizes an old Tcl
            # interpreter or one of its StringVars.
            for name in tuple(vars(window)):
                if name.startswith("_experiment_"):
                    setattr(window, name, None)
            self.ctx._har_experiment_window = None
            root.report_callback_exception = None
            self.window = self.root = self.ctx = None
            del root, window
            gc.collect()
        finally:
            self.stack.close()

    def pump(self):
        self.root.update_idletasks()
        self.root.update()
        self.assertEqual(self.callback_errors, [], f"Tk callback errors: {self.callback_errors}")

    def pump_until(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while not condition():
            self.pump()
            if time.monotonic() >= deadline:
                self.fail(f"Timed out waiting for experiment UI: {self.window._experiment_status.get()}")
            time.sleep(.01)
        self.pump()

    def replace(self, name, value):
        field = self.window._experiment_fields[name]
        field.delete(0, tk.END)
        field.insert(0, str(value))

    def run_and_wait(self):
        self.window._experiment_run_button.invoke()
        self.pump_until(lambda: not self.window._experiment_busy)

    def test_invalid_settings_are_reported_inline_without_starting_worker(self):
        with patch("har_experiment.run_synthetic_benchmark") as backend:
            for name, invalid, original in (("seed", "not a number", "42"),
                                             ("window_minutes", "nan", "10"),
                                             ("sessions_per_house", "0", "20"),
                                             ("minimum_probability", "1.01", ".5")):
                with self.subTest(name=name):
                    self.replace(name, invalid)
                    self.window._experiment_run_button.invoke()
                    self.pump()
                    self.assertIn("Check experiment settings", self.window._experiment_status.get())
                    self.assertFalse(self.window._experiment_busy)
                    self.assertEqual(str(self.window._experiment_run_button["state"]), tk.NORMAL)
                    self.replace(name, original)
            backend.assert_not_called()

    def test_background_result_displays_comparison_splits_and_confidence_limits(self):
        report = example_report(self.directory / "results")
        with patch("har_experiment.run_synthetic_benchmark", return_value=report) as backend:
            self.run_and_wait()
        backend.assert_called_once_with(seed=42, window_minutes=10, minimum_probability=.5,
                                        strict_cooking=True, use_time_of_day=False, sessions_per_house=20)
        tree = self.window._experiment_tree
        self.assertEqual(len(tree.get_children()), 4)
        values = tree.item("semantic_rf", "values")
        self.assertEqual(values[2], "90.0%")
        self.assertEqual(values[5], "75.0%")
        details = self.window._experiment_report.get("1.0", tk.END)
        for expected in ("train-a", "validation-d", "source-test-e", "target-test-f", "showering",
                         "uncalibrated", "do not measure accuracy on human behaviour"):
            self.assertIn(expected, details)
        self.assertEqual(str(self.window._experiment_report["state"]), tk.DISABLED)
        self.assertIn(str(report["output_dir"]), self.window._experiment_status.get())
        self.assertEqual(str(self.window._experiment_copy_button["state"]), tk.NORMAL)

    def test_failure_clears_previous_metrics_and_reenables_controls(self):
        report = example_report(self.directory / "results")
        with patch("har_experiment.run_synthetic_benchmark",
                   side_effect=[report, ValueError("No held-out target house was registered.")]):
            self.run_and_wait()
            self.assertEqual(len(self.window._experiment_tree.get_children()), 4)
            self.window._experiment_run_button.invoke()
            self.assertEqual(self.window._experiment_tree.get_children(), ())
            self.assertIsNone(self.window._experiment_last_report)
            self.pump_until(lambda: not self.window._experiment_busy)
        self.assertIn("Experiment failed: No held-out target house", self.window._experiment_status.get())
        self.assertEqual(str(self.window._experiment_run_button["state"]), tk.NORMAL)
        self.assertEqual(str(self.window._experiment_fields["seed"]["state"]), tk.NORMAL)
        self.assertEqual(str(self.window._experiment_copy_button["state"]), tk.DISABLED)
        self.assertNotIn("train-a", self.window._experiment_report.get("1.0", tk.END))

    def test_registered_dataset_selector_uses_library_backend(self):
        report = example_report(self.directory / "results")
        report["scope"] = "human-labelled"
        source = self.window._experiment_fields["source"]
        source.set("Registered human-labelled datasets")
        source.event_generate("<<ComboboxSelected>>")
        self.pump()
        self.assertEqual(str(self.window._experiment_fields["sessions_per_house"]["state"]), tk.DISABLED)
        with patch("har_experiment.run_library_experiment", return_value=report) as backend, \
                patch("har_experiment.run_synthetic_benchmark") as demo:
            self.run_and_wait()
        backend.assert_called_once_with(seed=42, window_minutes=10, minimum_probability=.5,
                                        strict_cooking=True, use_time_of_day=False)
        demo.assert_not_called()
        self.assertIn("Human-labelled experiment complete", self.window._experiment_status.get())

    def test_close_while_worker_pending_has_no_late_tk_callbacks_or_duplicate_run(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def delayed(**_parameters):
            started.set()
            release.wait(5)
            finished.set()
            return example_report(self.directory / "results")

        with patch("har_experiment.run_synthetic_benchmark", side_effect=delayed) as backend:
            self.window._experiment_run_button.invoke()
            self.pump_until(started.is_set)
            self.window._experiment_run_button.invoke()
            backend.assert_called_once()
            self.window.destroy()
            self.pump()
            release.set()
            self.pump_until(finished.is_set)
            # The closed Toplevel's polling callback must have been cancelled.
            deadline = time.monotonic() + .2
            while time.monotonic() < deadline:
                self.pump()
                time.sleep(.01)
        self.assertEqual(self.callback_errors, [])

    def test_real_synthetic_backend_exports_frozen_experiment_and_models(self):
        self.replace("sessions_per_house", 5)
        self.window._experiment_run_button.invoke()
        self.pump_until(lambda: not self.window._experiment_busy, timeout=60)
        self.assertNotIn("failed", self.window._experiment_status.get().lower())
        report = self.window._experiment_last_report
        self.assertIsNotNone(report, self.window._experiment_status.get())
        self.assertEqual(report["scope"], "synthetic")
        self.assertEqual(len(self.window._experiment_tree.get_children()), 4)
        output = Path(report["output_dir"])
        self.assertTrue(output.is_relative_to(self.directory))
        for name in ("report.json", "experiment.json", "models.json"):
            self.assertTrue((output / name).is_file(), name)
        manifest = json.loads((output / "experiment.json").read_text(encoding="utf-8"))
        targets = {dataset["house_id"] for dataset in manifest["datasets"]
                   if dataset["run_id"] in report["target_test_runs"]}
        self.assertTrue(targets)
        self.assertNotIn(report["source_house_id"], targets)
        self.assertIn("do not measure accuracy on human behaviour", self.window._experiment_report.get("1.0", tk.END))
        self.assertEqual(self.callback_errors, [])


if __name__ == "__main__":
    unittest.main()
