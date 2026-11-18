"""Learned categories transfer observations, never hand-written activities."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from har import storage
from har.catalogue import default_catalogue
from har.demo import demo_sessions
from har.features import FeatureEncoder, encode_session
from har.learning import evaluate_model, predict_vector, train_source, validate_model


class LearnedCategoryModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sessions = demo_sessions()
        cls.model = train_source(cls.sessions, "demo-source")

    def test_supervised_model_transfers_to_other_device_types_and_extra_categories(self):
        report = evaluate_model(self.model, self.sessions)
        self.assertEqual(report["scope"], "synthetic")
        self.assertEqual(report["test_houses"], 1)
        self.assertEqual(report["target_independent_sessions"], 5)
        self.assertGreater(report["labelled_samples"], 100)
        # This is a regression on this controlled script, not a human-accuracy claim.
        self.assertGreater(report["exact_match_accuracy"], .8)
        self.assertGreater(report["micro_f1"], .85)
        self.assertEqual(report["unknown_activities"], [])
        self.assertEqual(set(self.model["labels"]), {"cooking", "relaxing"})
        for forbidden in ("target_microwave", "source_oven", "source_tv", "target_console", "recent_cooking"):
            self.assertFalse(any(forbidden in name for name in self.model["feature_names"]))
        source_ids = {session["session_id"] for session in self.sessions if session["house_id"] == "demo-source"}
        splits = [set(self.model[key]) for key in
                  ("source_training_sessions", "source_validation_sessions", "source_test_sessions")]
        self.assertEqual(set.union(*splits), source_ids)
        self.assertTrue(all(not first & second for index, first in enumerate(splits) for second in splits[index + 1:]))

    def test_changing_target_labels_and_observations_cannot_change_the_fitted_model(self):
        changed = copy.deepcopy(self.sessions)
        for session in changed:
            if session["partition"] == "target":
                session["frames"] = [{"arbitrary": "target data must never be inspected during fitting"}]
                session["catalogue"] = {}
        other = train_source(changed, "demo-source")
        for key in ("classifiers", "labels", "catalogue", "feature_names", "source_training_sessions", "training_samples"):
            self.assertEqual(self.model[key], other[key], key)

    def test_activity_names_are_learned_labels_instead_of_fixed_category_rules(self):
        changed = copy.deepcopy(self.sessions)
        for session in changed:
            for frame in session["frames"]:
                if frame["labels"] is not None:
                    frame["labels"] = ["preparazione" if label == "cooking" else "svago" for label in frame["labels"]]
        other = train_source(changed, "demo-source")
        self.assertEqual(set(other["labels"]), {"preparazione", "svago"})
        report = evaluate_model(other, changed)
        self.assertGreater(report["exact_match_accuracy"], .8)
        self.assertNotIn("cooking", report["per_activity"])

    def test_manual_labels_and_future_frames_do_not_enter_observation_features(self):
        frames = copy.deepcopy(self.sessions[0]["frames"][:4])
        original = FeatureEncoder(default_catalogue())
        altered = FeatureEncoder(default_catalogue())
        for frame in frames:
            changed = copy.deepcopy(frame)
            changed["labels"] = ["an_activity_never_defined_in_any_rule"]
            self.assertEqual(original.update(frame), altered.update(changed))
        before_future = copy.deepcopy(original.history)
        future = copy.deepcopy(frames[-1])
        future["time_seconds"] += 1000
        future["devices"][0]["state"] = 1 - future["devices"][0]["state"]
        altered.update(future)
        self.assertEqual(original.history, before_future)

    def test_unlabelled_frames_supply_context_without_becoming_negative_examples(self):
        session = copy.deepcopy(self.sessions[0])
        catalogue = session["catalogue"]
        session["frames"] = session["frames"][:4]
        session["frames"][0]["labels"] = None
        session["frames"][1]["labels"] = None
        session["frames"][2]["labels"] = []
        session["frames"][3]["labels"] = ["custom_activity"]
        names, rows = encode_session(session, catalogue, 600)
        self.assertEqual(len(rows), 2)
        self.assertEqual([labels for _, labels in rows], [[], ["custom_activity"]])
        self.assertGreater(rows[0][0][names.index("observed_window_seconds")], 0)

    def test_model_json_roundtrip_preserves_predictions_without_pickle(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(storage, "DATA_DIR", Path(temp)):
            storage.save_model(self.model)
            loaded = validate_model(storage.load_model(self.model["model_id"]))
            frame = self.sessions[-1]["frames"][20]
            vector = FeatureEncoder(loaded["catalogue"]).update(frame)
            self.assertEqual(predict_vector(self.model, vector), predict_vector(loaded, vector))

    def test_renamed_categories_preserve_old_model_predictions_and_frozen_schema(self):
        # Recreate the spelling used by recordings and models before the rename.
        legacy = json.loads(json.dumps(self.sessions).replace("Device_", "Device").replace("Sensor_", "Sensor"))
        legacy_model = train_source(legacy, "demo-source")
        old_targets = [session for session in legacy if session["partition"] == "target"]
        new_targets = [session for session in self.sessions if session["partition"] == "target"]
        with tempfile.TemporaryDirectory() as temp, patch.object(storage, "DATA_DIR", Path(temp)):
            path = storage.save_model(legacy_model)
            loaded = validate_model(storage.load_model(legacy_model["model_id"]))
            before = copy.deepcopy(loaded)
            for model in (self.model, loaded):
                with self.subTest(legacy_schema=model is loaded):
                    old_report = evaluate_model(model, old_targets)
                    new_report = evaluate_model(model, new_targets)
                    self.assertEqual(old_report["predictions"], new_report["predictions"])
            self.assertEqual(loaded, before)
            self.assertEqual(storage.read_json(path), before)
            for model, targets, power_category in (
                (loaded, new_targets, "Sensor_Power"),
                (self.model, old_targets, "SensorPower"),
            ):
                incompatible = copy.deepcopy(targets[0])
                incompatible["catalogue"]["sensor_categories"][power_category]["unit"] = "kW"
                with self.subTest(unit_category=power_category), self.assertRaisesRegex(ValueError, "unit"):
                    evaluate_model(model, [incompatible])

    def test_portable_tree_probabilities_match_sklearn_for_multiple_activities(self):
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from har.learning import _export_forest
        x = np.asarray([[0, 0], [0, 1], [1, 0], [1, 1]] * 5, dtype=np.float32)
        y = np.asarray([[0, 0], [0, 1], [1, 0], [1, 1]] * 5)
        forest = RandomForestClassifier(n_estimators=12, random_state=42).fit(x, y)
        model = {"feature_names": ["first", "second"], "labels": ["activity-a", "activity-b"],
                 "trees": _export_forest(forest, 2), "threshold": .5, "minimum_certainty": 0}
        for vector in ([0, 0], [1, 1], [.499999999, .500000001], [.50000001, .49999999]):
            expected = forest.predict_proba(np.asarray([vector]))
            actual = predict_vector(model, vector)["probabilities"]
            for column, label in enumerate(model["labels"]):
                self.assertAlmostEqual(actual[label], expected[column][0, 1])

    def test_invalid_or_cyclic_tree_is_rejected_before_activation(self):
        label = self.model["labels"][0]
        index = next(index for index, left in enumerate(self.model["classifiers"][label]["trees"][0]["left"]) if left != -1)
        for field, value in (("left", 0.5), ("feature", True), ("left", index)):
            with self.subTest(field=field, value=value):
                invalid = copy.deepcopy(self.model)
                invalid["classifiers"][label]["trees"][0][field][index] = value
                with self.assertRaises(ValueError):
                    validate_model(invalid)

    def test_original_joint_model_json_keeps_its_predictions_and_whole_set_abstention(self):
        legacy = copy.deepcopy(self.model)
        legacy.pop("classifiers")
        legacy.update(schema_version=1, kind="category_random_forest", threshold=.5, minimum_certainty=.55)
        legacy["trees"] = [{"left": [-1], "right": [-1], "feature": [-2], "threshold": [-2.0],
                            "positive": [[.9, .2]]}]
        vector = [0] * len(legacy["feature_names"])
        with tempfile.TemporaryDirectory() as temp, patch.object(storage, "DATA_DIR", Path(temp)):
            path = storage.save_model(legacy)
            before = path.read_bytes()
            loaded = validate_model(storage.load_model(legacy["model_id"]))
            result = predict_vector(loaded, vector)
            self.assertEqual(result["activities"], ["cooking"])
            self.assertEqual(result["status"], "ready")
            self.assertAlmostEqual(result["confidence"], .8)
            self.assertNotIn("activity_confidences", result)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(loaded, legacy)
        legacy["trees"][0]["positive"][0][1] = .5
        result = predict_vector(validate_model(legacy), vector)
        self.assertTrue(result["abstained"])
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["activities"], [])

    def test_multiple_recordings_in_one_simulation_cannot_be_split_as_independent(self):
        changed = copy.deepcopy(self.sessions)
        source = [session for session in changed if session["house_id"] == "demo-source"]
        source[1]["simulation_session_id"] = source[0]["simulation_session_id"]
        with self.assertRaisesRegex(ValueError, "one simulation"):
            train_source(changed, "demo-source")
        source[1]["simulation_session_id"] = "different-simulation"
        source[1]["frames"] = copy.deepcopy(source[0]["frames"])
        with self.assertRaisesRegex(ValueError, "Copied observation"):
            train_source(changed, "demo-source")

    def test_target_cannot_copy_source_even_when_evaluated_without_source_documents(self):
        source = self.sessions[0]
        target = copy.deepcopy(self.sessions[-1])
        target["simulation_session_id"] = source["simulation_session_id"]
        with self.assertRaisesRegex(ValueError, "source simulation"):
            evaluate_model(self.model, [target])
        target["simulation_session_id"] = "different-simulation"
        target["frames"] = copy.deepcopy(source["frames"])
        with self.assertRaisesRegex(ValueError, "copies source"):
            evaluate_model(self.model, [target])

    def test_missing_or_unknown_target_labels_are_not_silently_ignored(self):
        target = copy.deepcopy(self.sessions[-1])
        for frame in target["frames"]:
            frame["labels"] = ["unseen_activity"]
        report = evaluate_model(self.model, [target])
        self.assertEqual(report["unknown_activities"], ["unseen_activity"])
        self.assertEqual(report["per_activity"]["unseen_activity"]["recall"], 0)
        self.assertEqual(report["exact_match_accuracy"], 0)

    def test_sensor_units_must_match_the_frozen_training_dictionary(self):
        target = copy.deepcopy(self.sessions[-1])
        target["catalogue"]["sensor_categories"]["Sensor_Power"]["unit"] = "kW"
        with self.assertRaisesRegex(ValueError, "Sensor_Power"):
            evaluate_model(self.model, [target])

    def test_new_house_identity_cannot_reuse_old_temporal_history_with_same_model(self):
        from har.runtime import process_frame
        frame = self.sessions[0]["frames"][0]
        runtime = {"devices": [SimpleNamespace(**item) for item in frame["devices"]],
                   "sensors": [SimpleNamespace(**item) for item in frame["sensors"]], "rooms": [],
                   "har_config": {"model_id": self.model["model_id"], "house_id": "original-house"}}
        with tempfile.TemporaryDirectory() as temp, patch.object(storage, "DATA_DIR", Path(temp)):
            storage.save_model(self.model)
            state = {}
            process_frame(frame["sensor_states"], runtime, state, 0)
            process_frame(frame["sensor_states"], runtime, state, 60)
            previous = state["har_inference"]["encoder"]
            self.assertEqual(len(previous.history), 2)
            runtime["har_config"]["house_id"] = "another-house"
            process_frame(frame["sensor_states"], runtime, state, 120)
            self.assertIsNot(state["har_inference"]["encoder"], previous)
            self.assertEqual(len(state["har_inference"]["encoder"].history), 1)

    def test_invalid_numeric_sensor_reading_is_not_replaced_with_an_idle_zero(self):
        from har.runtime import process_frame
        frame = self.sessions[0]["frames"][0]
        runtime = {"devices": [], "sensors": [], "rooms": [], "har_config": {}}
        with tempfile.TemporaryDirectory() as temp, patch.object(storage, "DATA_DIR", Path(temp)):
            with self.assertRaisesRegex(ValueError, "Invalid numeric reading"):
                process_frame({"broken-sensor": {"state": [float("nan")]}}, runtime, {}, 0)


if __name__ == "__main__":
    unittest.main()
