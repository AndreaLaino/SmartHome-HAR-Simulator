import copy
import json
import unittest
from unittest import mock

import numpy as np

from har_classifier import fit_classifier, predict_probabilities


class HARClassifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sklearn.ensemble import RandomForestClassifier

        cls.x = np.random.RandomState(7).normal(size=(60, 3))
        cls.y = np.column_stack((cls.x[:, 0] > 0, cls.x[:, 1] > .2,
                                 np.ones(len(cls.x)), np.zeros(len(cls.x)))).astype(int)
        cls.labels = ["cooking", "relaxing", "always", "never"]
        cls.features = ["power", "presence", "duration"]
        cls.model = fit_classifier(cls.x, cls.y, [], [], labels=cls.labels, feature_names=cls.features)
        cls.reference = RandomForestClassifier(n_estimators=128, min_samples_leaf=2, max_depth=12,
                                               n_jobs=1, random_state=42,
                                               class_weight="balanced_subsample").fit(cls.x, cls.y)

    def test_serialized_probabilities_match_forest_and_constant_outputs(self):
        actual = predict_probabilities(self.model, self.x)
        expected = self.reference.predict_proba(self.x)
        for output, label in enumerate(self.labels):
            classes = list(self.reference.classes_[output])
            values = expected[output][:, classes.index(1)] if 1 in classes else np.zeros(len(self.x))
            np.testing.assert_allclose([row[label] for row in actual], values, rtol=1e-12, atol=1e-12)
        self.assertEqual(self.model["class_mapping"][2:], [[1], [0]])
        self.assertTrue(all(row["always"] == 1 and row["never"] == 0 for row in actual))
        self.assertEqual(self.model["calibrators"]["always"]["method"], "uncalibrated")
        self.assertIn("one class", self.model["calibrators"]["always"]["reason"])

    def test_json_roundtrip_and_prediction_never_fit_an_estimator(self):
        portable = json.loads(json.dumps(self.model, allow_nan=False))
        expected = predict_probabilities(self.model, self.x[:5])
        with mock.patch("sklearn.ensemble.RandomForestClassifier.fit", side_effect=AssertionError("fit called")), \
                mock.patch("sklearn.linear_model.LogisticRegression.fit", side_effect=AssertionError("fit called")):
            self.assertEqual(predict_probabilities(portable, self.x[:5]), expected)
        self.assertEqual(predict_probabilities(portable, []), [])

    def test_single_output_constant_positive_and_negative(self):
        for target in (0, 1):
            with self.subTest(target=target):
                model = fit_classifier(self.x[:8], [target] * 8, [], [],
                                       labels=["activity"], feature_names=self.features)
                self.assertEqual(model["class_mapping"], [[target]])
                self.assertEqual(predict_probabilities(model, self.x[:3]), [{"activity": float(target)}] * 3)

    def test_calibration_uses_separate_validation_with_sufficient_class_counts(self):
        validation = np.random.RandomState(19).normal(size=(20, 3))
        validation_y = np.column_stack(([0] * 10 + [1] * 10, [0] * 18 + [1] * 2))
        model = fit_classifier(self.x, self.y[:, :2], validation, validation_y,
                               labels=self.labels[:2], feature_names=self.features)
        calibrated = model["calibrators"]["cooking"]
        sparse = model["calibrators"]["relaxing"]
        self.assertEqual(calibrated["method"], "sigmoid")
        self.assertEqual((calibrated["positive_count"], calibrated["negative_count"]), (10, 10))
        self.assertIsInstance(calibrated["coefficient"], float)
        self.assertEqual(sparse["method"], "uncalibrated")
        self.assertEqual((sparse["positive_count"], sparse["negative_count"]), (2, 18))
        self.assertIn("Source validation", sparse["reason"])
        self.assertEqual(model["training_samples"], 60)
        self.assertEqual(model["validation_samples"], 20)
        portable = json.loads(json.dumps(model, allow_nan=False))
        probabilities = predict_probabilities(portable, validation)
        self.assertTrue(all(0 <= value <= 1 for row in probabilities for value in row.values()))
        raw = copy.deepcopy(portable)
        raw["calibrators"]["cooking"]["method"] = "uncalibrated"
        self.assertNotEqual([row["cooking"] for row in probabilities],
                            [row["cooking"] for row in predict_probabilities(raw, validation)])

    def test_source_targets_and_feature_names_affect_exported_model(self):
        changed_y = self.y.copy()
        changed_y[:, 0] = 1 - changed_y[:, 0]
        names = ["food preparation", *self.labels[1:]]
        features = ["semantic power", *self.features[1:]]
        changed = fit_classifier(self.x, changed_y, [], [], labels=names, feature_names=features)
        self.assertEqual(changed["labels"], names)
        self.assertEqual(changed["feature_names"], features)
        before = predict_probabilities(self.model, self.x)
        after = predict_probabilities(changed, self.x)
        self.assertGreater(max(abs(a["cooking"] - b["food preparation"]) for a, b in zip(before, after)), .5)

    def test_invalid_shapes_targets_and_nonfinite_features_are_rejected_before_fitting(self):
        valid = {"train_x": [[0], [1]], "train_y": [[0], [1]], "validation_x": [], "validation_y": [],
                 "labels": ["cooking"], "feature_names": ["power"]}
        changes = [{"train_x": [0, 1]}, {"train_x": []}, {"train_x": [[float("nan")], [1]]},
                   {"train_x": [[float("inf")], [1]]}, {"train_x": [[1e100], [1]]},
                   {"train_x": [["0"], ["1"]]}, {"train_y": [[0], [.3]]},
                   {"train_y": [[0]]}, {"validation_x": [[0, 1]], "validation_y": [[1]]},
                   {"validation_x": [[1]], "validation_y": []},
                   {"labels": ["cooking", "cooking"]}, {"feature_names": [""]}, {"seed": True}]
        with mock.patch("sklearn.ensemble.RandomForestClassifier.fit", side_effect=AssertionError("fit called")):
            for change in changes:
                with self.subTest(change=change), self.assertRaises(ValueError):
                    fit_classifier(**{**valid, **change})
        for matrix in ([0, 1], [[0, 1]], [[float("nan"), 0, 0]], [["0", "0", "0"]]):
            with self.subTest(matrix=matrix), self.assertRaises(ValueError):
                predict_probabilities(self.model, matrix)

    def test_malformed_tree_indices_and_cycles_are_rejected(self):
        changes = ("child bounds", "feature bounds", "cycle", "one missing child", "unnormalized value")
        for change in changes:
            with self.subTest(change=change):
                model = copy.deepcopy(self.model)
                tree = model["trees"][0]
                if change == "child bounds":
                    tree["children_left"][0] = len(tree["children_left"])
                elif change == "feature bounds":
                    tree["feature"][0] = len(self.features)
                elif change == "cycle":
                    tree["children_left"][0] = 0
                elif change == "one missing child":
                    tree["children_left"][0] = -1
                else:
                    tree["value"][0][0] = [2, 0]
                with self.assertRaises(ValueError):
                    predict_probabilities(model, self.x[:1])


if __name__ == "__main__":
    unittest.main()
