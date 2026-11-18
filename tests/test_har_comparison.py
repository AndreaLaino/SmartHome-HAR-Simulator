"""Paired source-only learning, portable boosting, and synchronized live logs."""

import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import activity
from har import storage
from har.features import FeatureEncoder
from har.learning import (
    ALGORITHM_NAMES, COMPARISON_KIND, _export_boosting, evaluate_model,
    predict_vector, train_comparison, validate_model,
)
from har.runtime import process_frame, start_recording, set_recording_labels
from house_state import HouseState
import log
from tests.test_independent_har import sensor_sessions


class HARComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sessions = sensor_sessions()
        cls.model = train_comparison(cls.sessions, "source", window_seconds=30)

    def test_models_share_split_features_and_source_provenance(self):
        self.assertEqual(self.model["kind"], COMPARISON_KIND)
        rf, gb = (self.model["models"][name] for name in ALGORITHM_NAMES)
        for key in ("catalogue", "feature_names", "labels", "window_seconds", "seed", "training_samples",
                    "source_training_sessions", "source_validation_sessions", "source_test_sessions",
                    "source_session_fingerprints", "source_observation_fingerprints"):
            self.assertEqual(rf[key], gb[key], key)
        self.assertNotEqual(rf["model_id"], gb["model_id"])
        self.assertEqual(set(rf["classifiers"]), {"eating", "relaxing"})
        self.assertEqual(set(gb["classifiers"]), {"eating", "relaxing"})
        self.assertEqual(gb["hyperparameters"]["loss"], "log_loss")
        self.assertEqual(gb["sample_weighting"], "balanced")
        partitions = [set(rf[key]) for key in (
            "source_training_sessions", "source_validation_sessions", "source_test_sessions")]
        self.assertTrue(all(not first & second for i, first in enumerate(partitions) for second in partitions[i + 1:]))

    def test_target_mutations_cannot_change_either_trained_classifier(self):
        sessions = copy.deepcopy(self.sessions)
        sessions[-1]["catalogue"] = {"invalid": "target schema must not affect training"}
        sessions[-1]["frames"] = [{"labels": ["target-only"], "time_seconds": -1}]
        changed = train_comparison(sessions, "source", window_seconds=30)
        for name in ALGORITHM_NAMES:
            before, after = self.model["models"][name], changed["models"][name]
            for key in ("classifiers", "catalogue", "feature_names", "labels", "source_training_sessions",
                        "source_validation_sessions", "source_test_sessions"):
                self.assertEqual(before[key], after[key], (name, key))

    def test_both_models_recognize_unseen_simultaneous_evidence_and_score_identical_truth(self):
        report = evaluate_model(self.model, self.sessions)
        rf, gb = (report["algorithms"][name] for name in ALGORITHM_NAMES)
        self.assertEqual([(row["session_id"], row["expected"]) for row in rf["predictions"]],
                         [(row["session_id"], row["expected"]) for row in gb["predictions"]])
        for name, scores in report["algorithms"].items():
            both = [row for row in scores["predictions"] if len(row["expected"]) == 2]
            self.assertEqual(len(both), 20)
            self.assertGreaterEqual(sum(len(row["activities"]) == 2 for row in both) / len(both), .9, name)
            self.assertGreater(scores["micro_f1"], .9, name)
            self.assertEqual(scores["scope"], "synthetic")
            self.assertEqual(scores["labelled_samples"], 80)
            self.assertEqual(set(scores["per_activity"]), {"eating", "relaxing"})
            self.assertIn("coverage", scores)
        self.assertEqual(set(report["source_validation"]["algorithms"]), set(ALGORITHM_NAMES))

    def test_pair_rejects_mismatched_training_or_nested_comparisons(self):
        for field, value in (("seed", 7), ("source_training_sessions", ["different-session"]),
                             ("window_seconds", 60), ("labels", ["other"])):
            model = copy.deepcopy(self.model)
            model["models"]["gradient_boosting"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_model(model)
        model = copy.deepcopy(self.model)
        model["models"]["gradient_boosting"] = model
        with self.assertRaises(ValueError):
            validate_model(model)

    def test_invalid_boosting_trees_and_parameters_are_rejected(self):
        for field, value in (("learning_rate", 0), ("initial_log_odds", float("inf")),
                             ("trees", [{"left": [-1], "right": [-1], "feature": [-2],
                                         "threshold": [-2], "values": [float("nan")]}]),
                             ("trees", [{"left": [0], "right": [0], "feature": [0],
                                         "threshold": [0], "values": [1]}])):
            model = copy.deepcopy(self.model["models"]["gradient_boosting"])
            model["classifiers"]["eating"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_model(model)

    def test_portable_boosting_matches_sklearn_with_unbalanced_prior_and_split_boundaries(self):
        import numpy as np
        from sklearn.ensemble import GradientBoostingClassifier

        x = np.asarray([[0, 0], [0, 1], [1, 0], [1, 1], [2, 1]] * 8, dtype=np.float32)
        y = np.asarray([0, 0, 0, 1, 0] * 8)
        estimator = GradientBoostingClassifier(n_estimators=19, max_depth=2, learning_rate=.17,
                                              random_state=19).fit(x, y)
        model = {"kind": "independent_gradient_boosting", "feature_names": ["a", "b"],
                 "labels": ["activity"], "classifiers": {"activity": _export_boosting(estimator)}}
        model = json.loads(json.dumps(model))
        vectors = x.tolist() + [[.499999999, .500000001], [1.4999999999, 1]]
        for tree in model["classifiers"]["activity"]["trees"]:
            for feature, threshold in zip(tree["feature"], tree["threshold"]):
                if feature >= 0:
                    for value in (threshold - 1e-9, threshold, threshold + 1e-9):
                        vector = [.3, .8]
                        vector[feature] = value
                        vectors.append(vector)
        for vector, expected in zip(vectors, estimator.predict_proba(vectors)[:, 1]):
            self.assertAlmostEqual(predict_vector(model, vector)["probabilities"]["activity"], expected, places=12)
        for raw, expected in ((1000, 1), (-1000, 0)):
            detector = copy.deepcopy(model["classifiers"]["activity"])
            detector.update(initial_log_odds=raw, trees=[{"left": [-1], "values": [0]}])
            model["classifiers"]["activity"] = detector
            self.assertEqual(predict_vector(model, [0, 0])["probabilities"]["activity"], expected)

    def test_save_reload_runtime_uses_one_encoder_and_labels_do_not_affect_predictions(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(storage, "DATA_DIR", Path(directory)):
            storage.save_model(self.model)
            target = self.sessions[-1]
            sources = {"devices": [], "sensors": [], "rooms": [],
                       "har_config": {"house_id": "target", "house_label": "Target", "model_id": self.model["model_id"]}}
            house = HouseState()
            ctx = SimpleNamespace(har_config=sources["har_config"], house_state=house)
            with patch("har.runtime.load_catalogue", return_value=target["catalogue"]):
                start_recording(ctx, "target")
                encoder = FeatureEncoder(self.model["catalogue"], 30)
                with patch("har.runtime.FeatureEncoder", return_value=encoder) as factory, \
                        patch.object(encoder, "update", wraps=encoder.update) as update:
                    set_recording_labels(ctx, ["eating"])
                    first = process_frame({}, sources, house.activity_state(), 0, house_state=house)
                    set_recording_labels(ctx, ["relaxing"])
                    second = process_frame({}, sources, house.activity_state(), 1, house_state=house)
                    self.assertEqual(first["comparisons"], second["comparisons"])
                    self.assertEqual(set(first["comparisons"]), set(ALGORITHM_NAMES))
                    self.assertEqual(factory.call_count, 1)
                    self.assertEqual(update.call_count, 2)
                sources["har_config"]["model_id"] = None
                self.assertEqual(process_frame({}, sources, house.activity_state(), 2)["status"], "no_model")
                self.assertNotIn("har_inference", house.activity_state())

    def test_live_comparison_log_has_same_clock_ids_truth_and_survives_write_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(storage, "DATA_DIR", Path(directory) / "har"), \
                patch.object(log, "get_or_create_current_save_session", return_value=Path(directory)), \
                patch("har.runtime.load_catalogue", return_value=self.model["catalogue"]):
            storage.save_model(self.model)
            house = HouseState()
            sources = {"devices": [], "sensors": [], "rooms": [], "har_config": {
                "house_id": "target", "house_label": "Target", "model_id": self.model["model_id"]}}
            ctx = SimpleNamespace(har_config=sources["har_config"], house_state=house)
            start_recording(ctx, "target")
            set_recording_labels(ctx, [])
            timer = SimpleNamespace(is_running=True, elapsed_time=timedelta(seconds=2),
                                    get_simulated_time=lambda: "12:02")
            log.start_interaction_log_session(house)
            try:
                for _ in range(2):
                    activity.process_activities(Mock(), timer, {}, house, sources)
                path = Path(directory) / "har_comparison.jsonl"
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["time_seconds"], 120)
                self.assertEqual(rows[0]["expected"], [])
                self.assertEqual(rows[0]["simulation_session_id"], house.values["har_simulation_id"])
                self.assertEqual(rows[0]["comparison_model_id"], self.model["model_id"])
                self.assertEqual(set(rows[0]["predictions"]), set(ALGORITHM_NAMES))
                self.assertEqual(rows[0]["model_ids"], {name: child["model_id"] for name, child in self.model["models"].items()})
                with patch("activity.log_har_comparison", side_effect=OSError("disk full")), \
                        self.assertLogs("activity", level="WARNING"):
                    activity.process_activities(Mock(), timer, {}, house, sources)
                self.assertIn("comparisons", house.activity_state()["har_prediction"])
                timer.is_running = False
                activity.process_activities(Mock(), timer, {}, house, sources)
                self.assertEqual(path.read_text(encoding="utf-8").count("\n"), 1)
            finally:
                log.stop_interaction_log_session(house)
