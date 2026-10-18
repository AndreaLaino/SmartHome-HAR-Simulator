"""Graph data fidelity and shape comparisons, without a Tk root or plotting UI."""

import csv
import json
import pickle
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import graph


class GraphInteractionDataTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "interactions.csv"

    def write_rows(self, rows):
        with self.path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["timestamp_sim", "event_type", "name", "value", "extra"])
            writer.writeheader()
            writer.writerows(rows)

    def test_consumption_recovery_filters_sensor_events_and_preserves_zero(self):
        self.write_rows([
            {"timestamp_sim": "2026-01-02 12:00", "event_type": "sensor", "name": "meter", "value": "0"},
            {"timestamp_sim": "2026-01-02 12:01", "event_type": "sensor", "name": "meter", "value": "5.5"},
            {"timestamp_sim": "2026-01-02 12:01", "event_type": "sensor", "name": "meter", "value": "8.5"},
            {"timestamp_sim": "2026-01-02 12:02", "event_type": "sensor", "name": "other", "value": "999"},
            {"timestamp_sim": "2026-01-02 12:03", "event_type": "device", "name": "meter", "value": "999"},
            {"timestamp_sim": "2026-01-02 12:04", "event_type": "sensor", "name": "meter", "value": "bad"},
        ])
        with patch.object(graph, "_latest_interactions_csv", return_value=str(self.path)):
            actual = graph._load_consumption_from_interactions("meter")
        self.assertEqual(actual, {"2026-01-02 12:00": 0, "2026-01-02 12:01": 8.5})

    def test_missing_interactions_are_an_empty_history(self):
        for path in (None, str(self.path)):
            with self.subTest(path=path), patch.object(graph, "_latest_interactions_csv", return_value=path):
                self.assertEqual(graph._load_consumption_from_interactions("meter"), {})
                self.assertEqual(graph._logged_llm_cases("meter"), ([], []))

    def test_full_timestamp_and_legacy_time_matching_keep_missing_values(self):
        actual = graph._match_full_or_suffix(
            ["12:00", "2026-01-02 12:01", "12:02", "2026-01-03 12:01"],
            {"2026-01-02 12:00": 0, "2026-01-02 12:01": 8.5},
        )
        self.assertEqual(actual, [0, 8.5, None, None])

    def test_llm_history_separates_recorded_and_generated_cases(self):
        rows = []
        for index, event_type in enumerate(("normal", "completion_match", "dt_prediction")):
            rows.append({"timestamp_sim": f"2026-01-02 12:0{index}", "event_type": "llm_cycle", "name": "meter", "value": index, "extra": json.dumps({"cycle_id": index, "cluster": 0, "event_type": event_type, "duration_minutes": 10})})
        rows.extend([
            {"timestamp_sim": "2026-01-02 12:04", "event_type": "llm_cycle", "name": "other", "value": 99, "extra": "{}"},
            {"timestamp_sim": "2026-01-02 12:05", "event_type": "sensor", "name": "meter", "value": 99, "extra": "{}"},
            {"timestamp_sim": "2026-01-02 12:06", "event_type": "llm_cycle", "name": "meter", "value": "bad", "extra": "not-json"},
        ])
        self.write_rows(rows)
        with patch.object(graph, "_latest_interactions_csv", return_value=str(self.path)):
            normal, generated = graph._logged_llm_cases("meter")
        self.assertEqual([item["cycle_id"] for item in normal], [0])
        self.assertEqual([item["cycle_id"] for item in generated], [1, 2])
        self.assertEqual([item["event_type"] for item in generated], ["completion_match", "dt_prediction"])
        self.assertEqual(normal[0]["cluster"], 0)
        self.assertEqual(normal[0]["duration_minutes"], 10)


class GraphProfileDataTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.path = self.directory / "cycles.pkl"
        self.info = {"pkl_path": str(self.path), "selected_cycle_id": 7, "output_dir": str(self.directory), "chosen_k": 2, "selected_cluster": 1}

    def write_cycles(self, cycles):
        with self.path.open("wb") as stream:
            pickle.dump(cycles, stream)

    def test_representative_uses_the_actual_runtime_cycle_over_catalog_default(self):
        self.write_cycles([
            {"cycle_id": 7, "data": [{"value": 10}, {"value": 20}]},
            {"cycle_id": 8, "data": [{"value": 0}, {"value": 45.5}, {"value": "bad"}]},
        ])
        np.testing.assert_array_equal(graph._load_representative_cycle_values({**self.info, "runtime_cycle_id": 8}), [0, 45.5])

    def test_representative_sorts_recordings_and_ignores_invalid_time_or_power(self):
        self.write_cycles([{"cycle_id": 7, "data": [
            {"time": "2026-01-02 12:03", "value": 30},
            {"time": "2026-01-02 12:00", "value": 0},
            {"time": "2026-01-02 12:01", "value": 10},
            {"time": "bad", "value": 999},
            {"time": "2026-01-02 12:02", "value": float("inf")},
        ]}])
        np.testing.assert_array_equal(graph._load_representative_cycle_values(self.info), [0, 10, 30])

    def test_missing_corrupt_or_wrong_cycle_files_return_no_reference(self):
        self.assertEqual(graph._load_representative_cycle_values(self.info).size, 0)
        for data in (b"corrupt", pickle.dumps({"cycles": []}), pickle.dumps([{"cycle_id": 8, "data": [{"value": 999}]}])):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                self.assertEqual(graph._load_representative_cycle_values(self.info).size, 0)

    def test_predefined_reference_follows_minute_order_and_keeps_standby_zeroes(self):
        with patch.dict(graph.consumption_profiles, {"Fixture": {"profile": {3: 30, 0: 0, 1: 10}}}, clear=True):
            np.testing.assert_array_equal(graph._load_profile_reference_values({"device_type": "Fixture"}), [0, 10, 30])
            self.assertEqual(graph._load_profile_reference_values({"device_type": "missing"}).size, 0)

    def test_cluster_reference_uses_only_requested_cluster_and_valid_cycles(self):
        wave = np.sin(np.linspace(0, np.pi, 40)) * 100 + 20
        self.write_cycles([
            {"cycle_id": 7, "data": [{"value": float(value)} for value in wave]},
            {"cycle_id": 8, "data": [{"value": float(value)} for value in wave * 3 + 40]},
            {"cycle_id": 9, "data": [{"value": 999}]},
        ])
        results = self.directory / "results_k2"
        results.mkdir()
        pd.DataFrame({"cycle_id": [7, 8, 9, 99], "cluster": [1, 1, 2, 1]}).to_csv(results / "clusters.csv", index=False)
        actual = graph._load_cluster_mean_values(self.info)
        expected = graph._zscore(graph._resample_by_index(graph._extract_activity_segment(wave)))
        np.testing.assert_allclose(actual, expected, atol=1e-12)
        self.assertTrue(np.isfinite(actual).all())
        self.assertEqual(actual.size, 200)


class GraphShapePreprocessingTests(unittest.TestCase):
    def test_resampling_preserves_curve_endpoints_and_does_not_mutate_source(self):
        original = np.array([0, 20, 0], dtype=float)
        actual = graph._resample_by_index(original, target_len=5)
        np.testing.assert_array_equal(actual, [0, 10, 20, 10, 0])
        np.testing.assert_array_equal(original, [0, 20, 0])

    def test_resampling_handles_constant_and_empty_series(self):
        np.testing.assert_array_equal(graph._resample_by_index(np.array([0]), target_len=5), [0] * 5)
        self.assertEqual(graph._resample_by_index(np.array([]), target_len=5).size, 0)
        np.testing.assert_array_equal(graph._zscore(np.ones(10) * 20), np.zeros(10))

    def test_activity_extraction_keeps_distinct_runs_and_their_source_windows(self):
        values = np.r_[np.zeros(20), np.ones(12) * 100, np.zeros(20), np.ones(16) * 200, np.zeros(20)]
        runs = graph._extract_activity_runs(values)
        self.assertEqual(len(runs), 2)
        for segment, start, end in runs:
            np.testing.assert_array_equal(segment, values[start:end])
            self.assertGreater(segment.max(), 0)
            self.assertGreaterEqual(start, 0)
            self.assertLessEqual(end, len(values))
        self.assertLess(runs[0][2], runs[1][1])

    def test_representative_activity_segment_selects_the_dominant_cycle(self):
        values = np.r_[np.zeros(20), np.ones(12) * 100, np.zeros(20), np.ones(16) * 200, np.zeros(20)]
        segment = graph._extract_activity_segment(values)
        self.assertEqual(segment.max(), 200)
        self.assertNotIn(100, segment)
        self.assertLess(segment.size, values.size)

    def test_best_lag_aligns_a_delayed_signal_without_changing_its_shape(self):
        signal = np.sin(np.linspace(0, 8 * np.pi, 100)) + np.linspace(0, 1, 100)
        delayed = np.r_[np.zeros(7), signal[:-7]]
        first, second = graph._align_by_best_lag(signal, delayed, max_lag=10)
        np.testing.assert_allclose(first, second, atol=1e-12)
        self.assertEqual(first.size, 93)


class GraphShapeSimilarityTests(unittest.TestCase):
    def setUp(self):
        self.wave = np.sin(np.linspace(0, np.pi, 60)) * 100 + 20
        self.frame = pd.DataFrame({"value": self.wave}, index=pd.date_range("2026-01-02 12:00", periods=len(self.wave), freq="1min"))

    def compare(self, frame, reference):
        with patch.object(graph, "_load_representative_cycle_values", return_value=reference):
            return graph._compute_shape_similarity(frame, {})

    def test_identical_shapes_have_high_similarity_and_real_timestamp_window(self):
        actual = self.compare(self.frame, self.wave)
        self.assertTrue(actual["shape_available"])
        self.assertGreater(actual["shape_corr"], 0.999)
        self.assertLess(actual["shape_rmse"], 0.01)
        self.assertEqual(actual["shape_ref"], "runtime_cycle")
        self.assertIsNotNone(actual["shape_window_start"])
        self.assertIsNotNone(actual["shape_window_end"])
        self.assertEqual(actual["_sim_norm"].size, actual["_rep_norm"].size)

    def test_shape_comparison_is_invariant_to_positive_gain_and_baseline(self):
        baseline = self.compare(self.frame, self.wave)
        shifted = self.frame.copy()
        shifted["value"] = shifted["value"] * 4 + 300
        actual = self.compare(shifted, self.wave)
        self.assertTrue(actual["shape_available"])
        self.assertAlmostEqual(actual["shape_corr"], baseline["shape_corr"])
        self.assertAlmostEqual(actual["shape_rmse"], baseline["shape_rmse"])

    def test_missing_reference_or_no_simulation_has_no_misleading_score(self):
        for frame in (None, pd.DataFrame(), pd.DataFrame({"value": [None, "bad"]})):
            with self.subTest(frame=frame):
                actual = graph._compute_shape_similarity(frame, {})
                self.assertFalse(actual["shape_available"])
                self.assertIsNone(actual["shape_corr"])
                self.assertIsNone(actual["shape_rmse"])
        with patch.object(graph, "_load_profile_reference_values", return_value=np.array([])):
            self.assertFalse(self.compare(self.frame, np.array([]))["shape_available"])

    def test_predefined_profile_is_used_when_runtime_reference_is_unavailable(self):
        with patch.object(graph, "_load_profile_reference_values", return_value=self.wave):
            actual = self.compare(self.frame, np.array([]))
        self.assertTrue(actual["shape_available"])
        self.assertEqual(actual["shape_ref"], "consumption_profile")
        self.assertGreater(actual["shape_corr"], 0.999)

    def test_nonfinite_simulation_measurement_does_not_poison_similarity(self):
        frame = self.frame.copy()
        frame.iloc[30, 0] = float("inf")
        actual = self.compare(frame, np.delete(self.wave, 30))
        self.assertTrue(actual["shape_available"])
        self.assertTrue(np.isfinite(actual["shape_rmse"]))
        self.assertGreater(actual["shape_corr"], 0.999)

    def test_constant_profiles_have_finite_error_without_undefined_correlation(self):
        actual = self.compare(pd.DataFrame({"value": np.zeros(30)}, index=pd.date_range("2026-01-02", periods=30, freq="1min")), np.zeros(30))
        self.assertTrue(actual["shape_available"])
        self.assertIsNone(actual["shape_corr"])
        self.assertEqual(actual["shape_rmse"], 0)

    def test_comparison_does_not_modify_caller_dataframe(self):
        original = self.frame.copy(deep=True)
        self.compare(self.frame, self.wave)
        pd.testing.assert_frame_equal(self.frame, original)


if __name__ == "__main__":
    unittest.main()
