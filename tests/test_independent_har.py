"""Independent learned activities, unseen simultaneous evidence and uncertainty."""
import copy
from datetime import timedelta
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import activity
from har import storage
from har.catalogue import default_catalogue
from har.features import FeatureEncoder
from har.learning import _export_forest, evaluate_model, predict_vector, train_source, validate_model
from house_state import HouseState


def sensor_sessions():
    """Synthetic load/entertainment evidence; source labels never overlap."""
    sessions = []
    for index in range(6):
        target = index == 5
        rng = random.Random(100 + index)
        meter_name = "new-console-meter" if target else "tv-meter"
        load_name = "new-seat-load" if target else "seat-load"
        device_name = "console" if target else "television"
        rated = 200 if target else 100
        frames, now = [], 0
        for _cycle in range(20):
            states = [(0, 0), (1, 0), (0, 1)] + ([(1, 1)] if target else [])
            rng.shuffle(states)
            for eating, relaxing in states:
                power = rated * rng.uniform(.75, .95) if relaxing else 0
                frames.append({
                    "time_seconds": now, "labels": (["eating"] if eating else []) + (["relaxing"] if relaxing else []),
                    "devices": [{"name": device_name, "type": "PlayStation" if target else "TV",
                                 "x": index * 10, "y": 0, "state": relaxing, "power": rated,
                                 "max_consumption": rated, "current_consumption": power,
                                 "categories": ["Device_Entertainment"]}],
                    "sensors": [
                        {"name": load_name, "type": "Pressure Mat" if target else "Weight", "x": 0, "y": index * 10,
                         "min_val": 0, "max_val": 1, "categories": ["Sensor_Weight"]},
                        {"name": meter_name, "type": "Smart Meter", "x": index * 10, "y": 0,
                         "min_val": 0, "max_val": rated, "categories": ["Sensor_Power"], "associated_device": device_name},
                    ],
                    "sensor_states": {load_name: {"state": [rng.uniform(.8, 1) if eating else rng.uniform(0, .1)]},
                                      meter_name: {"consumption": [power]}}, "rooms": [],
                })
                now += rng.randint(35, 75)
        sessions.append({"schema_version": 1, "session_id": f"independent-{index}",
                         "simulation_session_id": f"simulation-{index}", "house_id": "target" if target else "source",
                         "house_label": "Target" if target else "Source", "partition": "target" if target else "source",
                         "provenance": "scripted-synthetic", "catalogue": default_catalogue(), "frames": frames})
    return sessions


class IndependentActivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sessions = sensor_sessions()
        cls.model = train_source(cls.sessions, "source", window_seconds=30)

    def test_separately_learned_sensor_evidence_can_recognize_both_in_another_house(self):
        self.assertEqual(self.model["schema_version"], 2)
        self.assertEqual(self.model["kind"], "independent_category_random_forest")
        self.assertEqual(set(self.model["classifiers"]), {"eating", "relaxing"})
        for session in self.sessions[:-1]:
            self.assertTrue(all(len(frame["labels"]) <= 1 for frame in session["frames"]))
        report = evaluate_model(self.model, [self.sessions[-1]])
        both = [row for row in report["predictions"] if set(row["expected"]) == {"eating", "relaxing"}]
        self.assertEqual(len(both), 20)
        self.assertGreaterEqual(sum(set(row["activities"]) == {"eating", "relaxing"} for row in both) / len(both), .9)
        self.assertGreater(report["exact_match_accuracy"], .9)
        self.assertEqual(report["scope"], "synthetic")
        self.assertEqual(report["unknown_activities"], [])

    def test_portable_independent_probabilities_match_each_sklearn_classifier(self):
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier

        x = np.asarray([[0, 0], [0, 1], [1, 0], [1, 1]] * 8, dtype=np.float32)
        model = {"kind": "independent_category_random_forest", "feature_names": ["load", "power"],
                 "labels": ["eating", "relaxing"], "classifiers": {}}
        forests = []
        for column, label in enumerate(model["labels"]):
            forest = RandomForestClassifier(n_estimators=12, random_state=42).fit(x, x[:, column])
            forests.append(forest)
            model["classifiers"][label] = {"trees": _export_forest(forest, 1), "threshold": .5, "minimum_certainty": .55}
        for vector in ([0, 0], [1, 0], [0, 1], [1, 1], [.499999999, .500000001]):
            result = predict_vector(model, vector)
            for forest, label in zip(forests, model["labels"]):
                self.assertAlmostEqual(result["probabilities"][label], forest.predict_proba(np.asarray([vector]))[0, 1])

    def constant_model(self, eating, relaxing):
        model = copy.deepcopy(self.model)
        for label, probability in (("eating", eating), ("relaxing", relaxing)):
            model["classifiers"][label]["trees"] = [
                {"left": [-1], "right": [-1], "feature": [-2], "threshold": [-2.0], "positive": [[probability]]},
            ]
        return validate_model(model)

    def test_uncertain_activity_does_not_suppress_another_and_evaluation_counts_partial_results(self):
        model = self.constant_model(.52, .9)
        vector = [0] * len(model["feature_names"])
        result = predict_vector(model, vector)
        self.assertEqual(result["activities"], ["relaxing"])
        self.assertEqual(result["uncertain_activities"], ["eating"])
        self.assertAlmostEqual(result["activity_confidences"]["relaxing"], .9)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["abstained"])  # The complete activity set remains uncertain.
        report = evaluate_model(model, [self.sessions[-1]])
        self.assertEqual(report["coverage"], 0)
        self.assertEqual(report["per_activity_coverage"], {"eating": 0, "relaxing": 1})
        self.assertGreater(report["per_activity"]["relaxing"]["tp"], 0)
        self.assertEqual(report["exact_match_accuracy"], 0)

    def test_fully_uncertain_and_confidently_inactive_states_remain_distinct(self):
        vector = [0] * len(self.model["feature_names"])
        uncertain = predict_vector(self.constant_model(.51, .49), vector)
        self.assertEqual(uncertain["status"], "uncertain")
        self.assertEqual(uncertain["activities"], [])
        self.assertEqual(set(uncertain["uncertain_activities"]), {"eating", "relaxing"})
        inactive = predict_vector(self.constant_model(.05, .1), vector)
        self.assertEqual(inactive["status"], "ready")
        self.assertEqual(inactive["activities"], [])
        self.assertFalse(inactive["abstained"])
        self.assertEqual(inactive["uncertain_activities"], [])

    def test_json_model_load_and_live_log_preserve_independent_confident_activities(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(storage, "DATA_DIR", Path(temporary)):
            model = self.constant_model(.8, .9)
            path = storage.save_model(model)
            before = path.read_bytes()
            self.assertEqual(validate_model(storage.load_model(model["model_id"])), model)
            house = HouseState()
            runtime = {"devices": [], "sensors": [], "rooms": [],
                       "har_config": {"model_id": model["model_id"], "house_id": "target"}}
            timer = SimpleNamespace(is_running=True, elapsed_time=timedelta(), get_simulated_time=lambda: "12:00")
            label = Mock()
            activity.process_activities(label, timer, {}, house, runtime)
            self.assertEqual(set(house.activity_log_state()["active_activities"]), {"eating", "relaxing"})
            self.assertIn("eating (80%)", label.config.call_args.kwargs["text"])
            self.assertIn("relaxing (90%)", label.config.call_args.kwargs["text"])
            inference = house.activity_state()["har_inference"]
            inference["model"]["classifiers"]["eating"]["trees"][0]["positive"][0][0] = .52
            timer.elapsed_time = timedelta(seconds=1)
            timer.get_simulated_time = lambda: "12:01"
            activity.process_activities(label, timer, {}, house, runtime)
            self.assertEqual(house.activity_log_state()["active_activities"], {"relaxing": "12:00"})
            self.assertEqual(house.activity_log_state()["activity_log"], [{"activity": "eating", "start": "12:00", "end": "12:01"}])
            text = label.config.call_args.kwargs["text"]
            self.assertIn("relaxing (90%)", text)
            self.assertIn("Uncertain: eating (52%)", text)
            self.assertEqual(path.read_bytes(), before)

    def test_missing_classifier_and_invalid_binary_tree_are_rejected(self):
        for mutate in (
            lambda model: model["classifiers"].pop("eating"),
            lambda model: model["classifiers"]["eating"].update(threshold=2),
            lambda model: model["classifiers"]["eating"]["trees"][0]["positive"].__setitem__(0, [.1, .2]),
        ):
            invalid = copy.deepcopy(self.model)
            mutate(invalid)
            with self.assertRaises(ValueError):
                validate_model(invalid)


if __name__ == "__main__":
    unittest.main()
