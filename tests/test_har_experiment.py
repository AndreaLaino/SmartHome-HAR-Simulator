import copy
import itertools
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import recognition_library as library
from har_benchmark import build_synthetic_benchmark
from har_experiment import (fingerprint, replay_experiment, run_experiment,
                            run_library_experiment, split_sessions, _rule_records)
from har_metrics import summarize_predictions


def compact_benchmark():
    benchmark = build_synthetic_benchmark(seed=42, sessions_per_house=5)
    for dataset in benchmark["datasets"]:
        selected = []
        for _intent, group in itertools.groupby(dataset["frames"], key=lambda frame: frame["scenario_intent"]):
            group = list(group)
            indices = sorted({round(index * (len(group) - 1) / 4) for index in range(5)})
            selected.extend(group[index] for index in indices)
        dataset["frames"] = selected
    return benchmark


class ExperimentProtocolTests(unittest.TestCase):
    def setUp(self):
        self.benchmark = compact_benchmark()

    def split(self, datasets=None):
        return split_sessions(self.benchmark["datasets"] if datasets is None else datasets,
                              self.benchmark["source_house_id"], seed=42)

    def test_complete_source_sessions_are_disjoint_and_target_houses_stay_held_out(self):
        split = self.split()
        self.assertEqual([len(split[key]) for key in ("train", "validation", "source_test", "target_test")], [3, 1, 1, 5])
        groups = [{row["run_id"] for row in split[key]} for key in ("train", "validation", "source_test", "target_test")]
        for first, second in itertools.combinations(groups, 2):
            self.assertFalse(first & second)
        self.assertTrue(all(row["house_id"] == self.benchmark["source_house_id"] for row in split["train"]))
        self.assertTrue(all(row["house_id"] != self.benchmark["source_house_id"] for row in split["target_test"]))
        self.assertEqual(self.split(), split)

    def test_source_session_split_does_not_depend_on_target_labels(self):
        changed = copy.deepcopy(self.benchmark["datasets"])
        for dataset in changed:
            if dataset["partition"] == "test":
                for frame in dataset["frames"]:
                    frame["expected_activities"] = ["unknown target label"]
        original = self.split()
        other = self.split(changed)
        for key in ("train", "validation", "source_test"):
            self.assertEqual(original[key], other[key])

    def test_duplicate_run_or_declared_session_cannot_cross_partitions(self):
        for same_run in (True, False):
            with self.subTest(same_run=same_run):
                duplicate = copy.deepcopy(self.benchmark["datasets"][0])
                if not same_run:
                    duplicate["run_id"] = "new-name-for-same-session"
                duplicate["partition"] = "test"
                with self.assertRaisesRegex(ValueError, "unique|same simulation session"):
                    self.split([*self.benchmark["datasets"], duplicate])

    def test_copying_observations_with_new_ids_and_labels_cannot_fake_independence(self):
        duplicate = copy.deepcopy(self.benchmark["datasets"][0])
        duplicate.update(run_id="renamed-copy", session_id="invented-new-session")
        for frame in duplicate["frames"]:
            frame["expected_activities"] = ["different label"]
        with self.assertRaisesRegex(ValueError, "identical observation traces"):
            self.split([*self.benchmark["datasets"], duplicate])

    def test_insufficient_source_or_missing_target_is_reported_before_training(self):
        with self.assertRaisesRegex(ValueError, "at least 5"):
            self.split(self.benchmark["datasets"][1:])
        with self.assertRaisesRegex(ValueError, "different target house"):
            self.split([row for row in self.benchmark["datasets"] if row["partition"] == "reference"])

    def test_mismatched_house_identity_or_padded_labels_are_rejected(self):
        changed = copy.deepcopy(self.benchmark["datasets"])
        changed[0]["house"]["house_id"] = "different"
        with self.assertRaisesRegex(ValueError, "inconsistent house"):
            self.split(changed)
        changed = copy.deepcopy(self.benchmark["datasets"])
        changed[0]["frames"][0]["expected_activities"] = [" cooking "]
        with self.assertRaisesRegex(ValueError, "invalid ground-truth"):
            self.split(changed)

    def test_rule_idle_certainty_is_subject_to_same_requested_cutoff(self):
        sample = {"house_id": "target", "run_id": "session", "time_minutes": 0,
                  "expected": [], "rule_prediction": [], "rule_confidence": .8,
                  "abstained": False, "reference_matches": []}
        records = _rule_records([sample], .9)
        metrics = summarize_predictions(records, ["cooking"])
        self.assertEqual(metrics["coverage"], 0)
        self.assertEqual(metrics["exact_match_accuracy"], 0)


class LearnedTransferIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.benchmark = compact_benchmark()
        cls.original = copy.deepcopy(cls.benchmark)
        cls.report = cls.run_case(cls.benchmark, cls.root / "baseline")

    @classmethod
    def run_case(cls, benchmark, path):
        return run_experiment(benchmark["datasets"], benchmark["config"], benchmark["source_house_id"],
                              output_dir=path, seed=42, scope="synthetic")

    def test_reference_and_semantic_classifier_transfer_physical_capabilities(self):
        methods = self.report["methods"]
        for name in ("reference_rules", "semantic_rf"):
            self.assertGreater(methods[name]["target"]["micro_f1"], .8)
        self.assertGreater(methods["semantic_rf"]["target"]["micro_f1"], methods["raw_rf"]["target"]["micro_f1"])
        self.assertEqual(methods["direct_rules"]["target"]["micro_f1"], 0)
        self.assertEqual(methods["semantic_rf"]["target"]["test_houses"], 1)
        self.assertTrue(all("synthetic" in item for item in self.report["target_test_runs"]))
        self.assertEqual(self.report["scope"], "synthetic")
        self.assertIn("not real-world", self.report["limitations"][0])
        self.assertEqual(self.benchmark, self.original)

    def test_export_contains_frozen_observations_models_features_predictions_and_reports(self):
        path = Path(self.report["output_dir"])
        for filename in ("experiment.json", "models.json", "features.json", "predictions.json", "report.json",
                         "report.md", "report.html", "comparison.csv", "per_activity.csv"):
            self.assertTrue((path / filename).is_file(), filename)
        models = json.loads((path / "models.json").read_text())
        self.assertEqual(set(models), {"semantic_rf", "raw_rf"})
        self.assertTrue(all(model["trees"] for model in models.values()))
        self.assertNotIn("pickle", (path / "models.json").read_text())
        features = json.loads((path / "features.json").read_text())
        self.assertFalse(any("expected" in name or "prediction" in name for name in features["feature_names"]["reference"]))

    def test_target_labels_change_metrics_but_not_models_features_or_predictions(self):
        benchmark = copy.deepcopy(self.benchmark)
        for dataset in benchmark["datasets"]:
            if dataset["partition"] == "test":
                for frame in dataset["frames"]:
                    frame["expected_activities"] = ["showering"]
        changed = self.run_case(benchmark, self.root / "changed-target-labels")
        self.assertEqual(changed["model_fingerprint"], self.report["model_fingerprint"])
        self.assertEqual(changed["feature_fingerprint"], self.report["feature_fingerprint"])
        self.assertNotEqual(changed["dataset_fingerprint"], self.report["dataset_fingerprint"])
        self.assertEqual(changed["methods"]["semantic_rf"]["target"]["exact_match_accuracy"], 0)
        self.assertEqual(changed["methods"]["semantic_rf"]["target"]["unsupported_labels"], ["showering"])
        original = json.loads((Path(self.report["output_dir"]) / "predictions.json").read_text())
        modified = json.loads((Path(changed["output_dir"]) / "predictions.json").read_text())
        for name in original:
            left = [{key: value for key, value in row.items() if key != "expected"} for row in original[name]["target"]]
            right = [{key: value for key, value in row.items() if key != "expected"} for row in modified[name]["target"]]
            self.assertEqual(left, right)

    def test_replay_never_reads_live_library_or_refits_and_reproduces_predictions(self):
        with patch("har_classifier.fit_classifier", side_effect=AssertionError("No fitting during replay")), \
             patch.object(library, "load_library", side_effect=AssertionError("No live model reads")):
            replay = replay_experiment(self.report["output_dir"], output_dir=self.root / "replay")
        self.assertEqual(replay["model_fingerprint"], self.report["model_fingerprint"])
        self.assertEqual(replay["methods"], self.report["methods"])
        original = json.loads((Path(self.report["output_dir"]) / "predictions.json").read_text())
        repeated = json.loads((Path(replay["output_dir"]) / "predictions.json").read_text())
        self.assertEqual(original, repeated)

    def test_replay_detects_modified_labels_and_code_version(self):
        original_path = Path(self.report["output_dir"])
        manifest = json.loads((original_path / "experiment.json").read_text())
        folder = self.root / "tampered"
        folder.mkdir()
        manifest["datasets"][0]["frames"][0]["expected_activities"] = ["tampered"]
        library.atomic_json(folder / "experiment.json", manifest)
        with self.assertRaisesRegex(ValueError, "observations/labels changed"):
            replay_experiment(folder, output_dir=self.root / "must-not-exist")
        manifest["inference_code_fingerprint"] = "old-version"
        library.atomic_json(folder / "experiment.json", manifest)
        with self.assertRaisesRegex(ValueError, "code changed"):
            replay_experiment(folder)

    def test_experiment_output_never_overwrites_previous_results(self):
        with self.assertRaisesRegex(ValueError, "will not be overwritten"):
            self.run_case(self.benchmark, Path(self.report["output_dir"]))

    def test_target_house_cannot_supply_source_reference_associations(self):
        benchmark = copy.deepcopy(self.benchmark)
        benchmark["config"]["reference_bindings"]["source-oven-cooking"]["reference"]["house_id"] = "synthetic-target"
        with self.assertRaisesRegex(ValueError, "held-out target house"):
            self.run_case(benchmark, self.root / "leak")

    def test_library_source_selection_and_synthetic_provenance_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(library, "LIBRARY_DIR", Path(folder)):
            shared = library.ensure_library()
            shared["recognition_config"] = copy.deepcopy(self.benchmark["config"])
            shared["reference_house_id"] = self.benchmark["source_house_id"]
            library.save_library(shared, shared["revision"])
            for index, dataset in enumerate(self.benchmark["datasets"]):
                library.atomic_json(Path(folder) / "datasets" / f"{index}.json", dataset)
            report = run_library_experiment(output_dir=Path(folder) / "results")
            self.assertEqual(report["source_house_id"], self.benchmark["source_house_id"])
            self.assertEqual(report["scope"], "synthetic")
            self.assertEqual(report["methods"], self.report["methods"])
            self.assertIn("synthetic", report["ground_truth_provenance"])


if __name__ == "__main__":
    unittest.main()
