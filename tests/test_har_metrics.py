import copy
import math
import unittest

from har_metrics import summarize_predictions


def record(expected=(), predicted=(), *, abstained=False, house="target", run="run", time=0, **extra):
    return {"expected": list(expected), "predicted": list(predicted), "abstained": abstained,
            "house_id": house, "run_id": run, "time_minutes": time, **extra}


class HARMetricsTests(unittest.TestCase):
    def test_confusion_counts_and_macro_ignore_unused_classes(self):
        rows = [record(["cooking"], ["cooking"]), record(["cooking"], ["relaxing"]),
                record(["relaxing"], ["cooking"]), record()]
        result = summarize_predictions(rows, ["cooking", "relaxing", "sleeping"])
        cooking = result["per_activity"]["cooking"]
        self.assertEqual({key: cooking[key] for key in ("tp", "fp", "fn", "tn", "support")},
                         {"tp": 1, "fp": 1, "fn": 1, "tn": 1, "support": 2})
        self.assertEqual(cooking["precision"], .5)
        self.assertEqual(cooking["recall"], .5)
        self.assertEqual(cooking["f1"], .5)
        self.assertEqual(result["exact_match_accuracy"], .5)
        self.assertAlmostEqual(result["micro_f1"], 1 / 3)
        self.assertEqual(result["macro_f1"], .25)
        self.assertIsNone(result["per_activity"]["sleeping"]["f1"])
        self.assertEqual(result["per_activity"]["sleeping"]["tn"], 4)

    def test_unknown_target_activity_remains_a_false_negative(self):
        result = summarize_predictions([record(["cooking", "showering"], ["cooking"])], ["cooking"])
        self.assertEqual(result["unsupported_labels"], ["showering"])
        self.assertEqual(result["per_activity"]["showering"]["fn"], 1)
        self.assertEqual(result["per_activity"]["showering"]["f1"], 0)
        self.assertAlmostEqual(result["micro_f1"], 2 / 3)
        self.assertEqual(result["macro_f1"], .5)
        self.assertEqual(result["exact_match_accuracy"], 0)

    def test_unknown_predictions_count_as_false_positives(self):
        result = summarize_predictions([record([], ["unexpected"])], ["cooking"])
        self.assertEqual(result["per_activity"]["unexpected"]["fp"], 1)
        self.assertEqual(result["micro_f1"], 0)
        self.assertEqual(result["macro_f1"], 0)
        self.assertEqual(result["unsupported_labels"], [])

    def test_idle_abstention_is_incorrect_and_has_no_selective_accuracy(self):
        result = summarize_predictions([record(abstained=True)], ["cooking"])
        self.assertEqual(result["exact_match_accuracy"], 0)
        self.assertEqual(result["coverage"], 0)
        self.assertIsNone(result["selective_accuracy"])
        self.assertIsNone(result["micro_f1"])
        self.assertIsNone(result["macro_f1"])

    def test_all_idle_is_perfect_exact_accuracy_without_fabricated_f1(self):
        result = summarize_predictions([record(), record(time=1)], ["cooking"])
        self.assertEqual(result["exact_match_accuracy"], 1)
        self.assertEqual(result["coverage"], 1)
        self.assertEqual(result["selective_accuracy"], 1)
        self.assertIsNone(result["micro_f1"])
        self.assertIsNone(result["macro_f1"])
        self.assertIsNone(result["per_activity"]["cooking"]["precision"])
        self.assertIsNone(result["per_activity"]["cooking"]["recall"])

    def test_coverage_and_selective_accuracy_keep_abstained_rows_in_denominator(self):
        rows = [record(["cooking"], ["cooking"]), record(["cooking"], ["cooking"], abstained=True),
                record(["cooking"], [], abstained=True), record(["cooking"], [])]
        result = summarize_predictions(rows, ["cooking"])
        self.assertEqual(result["labelled_samples"], 4)
        self.assertEqual(result["exact_match_accuracy"], .25)
        self.assertEqual(result["coverage"], .5)
        self.assertEqual(result["selective_accuracy"], .5)
        self.assertEqual(result["per_activity"]["cooking"]["tp"], 2)
        self.assertEqual(result["per_activity"]["cooking"]["fn"], 2)
        self.assertAlmostEqual(result["micro_f1"], 2 / 3)

    def test_per_house_aggregation_and_session_ids(self):
        rows = [record(["cooking"], ["cooking"], house="a", run="first"),
                record(["cooking"], [], house="a", run="second"),
                record(["cooking"], [], house="b", run="first", abstained=True)]
        result = summarize_predictions(rows, ["cooking"])
        self.assertEqual(result["test_houses"], 2)
        self.assertEqual(result["test_sessions"], 3)
        self.assertEqual(result["per_house"]["a"]["labelled_samples"], 2)
        self.assertEqual(result["per_house"]["a"]["exact_match_accuracy"], .5)
        self.assertAlmostEqual(result["per_house"]["a"]["micro_f1"], 2 / 3)
        self.assertEqual(result["per_house"]["b"]["coverage"], 0)
        self.assertEqual(result["per_house"]["b"]["exact_match_accuracy"], 0)

    def test_empty_dataset_metrics_are_unavailable(self):
        result = summarize_predictions([], ["cooking"])
        self.assertEqual(result["labelled_samples"], 0)
        for key in ("exact_match_accuracy", "micro_f1", "macro_f1", "coverage", "selective_accuracy", "brier_score"):
            self.assertIsNone(result[key], key)
        self.assertEqual(result["per_house"], {})
        self.assertEqual(result["test_houses"], 0)
        self.assertEqual(result["test_sessions"], 0)

    def test_brier_score_scope_is_only_known_labels(self):
        rows = [record(["cooking", "showering"], ["cooking"],
                       probabilities={"cooking": .8, "relaxing": .2, "showering": .9}),
                record(["relaxing"], ["relaxing"], probabilities={"cooking": .1, "relaxing": .7})]
        result = summarize_predictions(rows, ["cooking", "relaxing"])
        self.assertAlmostEqual(result["brier_score"], (.04 + .04 + .01 + .09) / 4)
        self.assertIn("known activity labels only", result["brier_scope"])
        self.assertEqual(result["unsupported_labels"], ["showering"])

    def test_incomplete_probability_maps_do_not_selectively_report_brier(self):
        for rows in ([record(["cooking"], ["cooking"], probabilities={"cooking": .9}), record()],
                     [record(probabilities={"cooking": .1})]):
            with self.subTest(rows=rows):
                result = summarize_predictions(rows, ["cooking", "relaxing"])
                self.assertIsNone(result["brier_score"])
        self.assertIsNone(summarize_predictions([record(probabilities={})], [])["brier_score"])

    def test_input_is_not_modified_and_duplicate_labels_are_set_semantics(self):
        rows = [record(["cooking", "cooking"], ["cooking"], confidence=.8)]
        original = copy.deepcopy(rows)
        result = summarize_predictions(rows, ["cooking", "cooking"])
        self.assertEqual(rows, original)
        self.assertEqual(result["per_activity"]["cooking"]["tp"], 1)
        self.assertEqual(result["exact_match_accuracy"], 1)

    def test_invalid_metadata_and_labels_are_rejected(self):
        invalid = [
            {"expected": "cooking"}, {"predicted": [""]}, {"predicted": [1]},
            {"house_id": ""}, {"house_id": 1}, {"run_id": " trailing "},
            {"time_minutes": math.nan}, {"time_minutes": "1"}, {"time_minutes": True},
            {"abstained": "false"}, {"confidence": 1.1}, {"confidence": "0.8"},
            {"probabilities": []}, {"probabilities": {"cooking": math.inf}},
            {"probabilities": {"cooking": -1}}, {"probabilities": {"cooking": "0.3"}},
        ]
        for change in invalid:
            with self.subTest(change=change):
                row = record()
                row.update(change)
                with self.assertRaises(ValueError):
                    summarize_predictions([row], ["cooking"])
        for rows in ("not records", [None], [{"expected": []}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                summarize_predictions(rows, ["cooking"])
        for known in ("cooking", [" cooking"], [None]):
            with self.subTest(known=known), self.assertRaises(ValueError):
                summarize_predictions([], known)


if __name__ == "__main__":
    unittest.main()
