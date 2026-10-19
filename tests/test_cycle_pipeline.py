"""Small numerical and end-to-end fixtures for appliance cycle analysis."""

import copy
import os
import pickle
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# Keep native numerical imports bounded on workstations with many CPU cores.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np
import pandas as pd

from LLM.smartmeter.common import cycle_pipeline as pipeline
from app.controllers import llm_smartmeter as controller


START = datetime(2026, 1, 2, 12)


def frame(minutes, values, start=START):
    return pd.DataFrame({
        "time": [start + timedelta(minutes=minute) for minute in minutes],
        "value": values,
    })


def parameters():
    return {
        "cycle": {
            "threshold_watts": 50, "min_duration_minutes": 2,
            "min_off_minutes": 1, "max_idle_gap_minutes": 5,
            "tail_samples": 1, "max_analysis_minutes": 9999,
            "force_rebuild_pkl": True,
        },
        "features": {"target_len": 8, "curve_norm_mode": "zscore"},
        "hybrid": {"alpha": 1, "beta": 1},
        "k": {
            "search_max": 3, "human_k_min": 2,
            "human_max_largest_cluster_frac": 1,
            "human_max_singleton_frac": 1, "human_min_cluster_size": 1,
        },
    }


class CycleExtractionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "power.tsv"

    def extract(self, data, **overrides):
        data.to_csv(self.path, sep="\t", index=False)
        options = dict(threshold=50, min_duration_minutes=2,
                       min_off_minutes=1, tail_samples=1, max_idle_gap_minutes=5)
        options.update(overrides)
        return pipeline.build_cycles_raw_data(self.path, **options)

    def test_threshold_duration_off_confirmation_and_tail_have_exact_boundaries(self):
        cycles = self.extract(frame(range(10), [50, 100, 120, 140, 0, 0, 0, 200, 220, 240]))
        self.assertEqual([cycle["cycle_id"] for cycle in cycles], [0, 1])
        self.assertEqual(cycles[0]["start_time"], pd.Timestamp(START + timedelta(minutes=1)))
        self.assertEqual(cycles[0]["end_time"], pd.Timestamp(START + timedelta(minutes=3)))
        self.assertEqual(cycles[0]["duration_minutes"], 2)
        self.assertEqual([row["value"] for row in cycles[0]["data"]], [100, 120, 140, 0])
        self.assertEqual([row["value"] for row in cycles[1]["data"]], [200, 220, 240])

    def test_brief_off_dip_is_kept_inside_the_same_cycle(self):
        cycles = self.extract(frame(range(6), [100, 0, 120, 140, 0, 0]))
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["duration_minutes"], 3)
        self.assertEqual([row["value"] for row in cycles[0]["data"]], [100, 0, 120, 140, 0])

    def test_no_on_samples_or_too_short_cycles_produce_no_cycles(self):
        for values in ([0, 50, 0, 0], [100, 120, 0, 0]):
            with self.subTest(values=values):
                self.assertEqual(self.extract(frame(range(4), values)), [])

    def test_long_acquisition_gap_splits_cycles_without_leaking_the_next_cycle_into_tail(self):
        cycles = self.extract(frame([0, 1, 2, 20, 21, 22], [100, 120, 140, 200, 220, 240]), tail_samples=3)
        self.assertEqual(len(cycles), 2)
        self.assertEqual([row["value"] for row in cycles[0]["data"]], [100, 120, 140])
        self.assertEqual(cycles[1]["start_time"], pd.Timestamp(START + timedelta(minutes=20)))

    def test_requested_tail_does_not_include_the_next_active_cycle(self):
        cycles = self.extract(frame(range(8), [100, 120, 140, 0, 0, 200, 220, 240]), tail_samples=5)
        self.assertEqual(len(cycles), 2)
        self.assertEqual([row["value"] for row in cycles[0]["data"]], [100, 120, 140, 0, 0])

    def test_invalid_time_numeric_and_infinite_readings_are_ignored(self):
        data = frame(range(7), [100, "bad", 120, float("inf"), 140, 0, 0])
        data.loc[len(data)] = ["not-a-date", 999]
        cycles = self.extract(data)
        self.assertEqual(len(cycles), 1)
        self.assertEqual([row["value"] for row in cycles[0]["data"]], [100, 120, 140, 0])

    def test_zero_tail_keeps_the_complete_active_segment(self):
        cycles = self.extract(frame([0, 1, 2, 20, 21, 22], [100, 120, 140, 200, 220, 240]), tail_samples=0)
        self.assertEqual([row["value"] for row in cycles[0]["data"]], [100, 120, 140])


class CycleNumericalTests(unittest.TestCase):
    def test_energy_integrates_watts_over_irregular_time_intervals_in_kwh(self):
        data = frame([0, 10, 30], [1000, 1000, 1000])
        self.assertAlmostEqual(pipeline.compute_energy_kwh(data), 0.5)
        self.assertAlmostEqual(controller._energy_kwh(data), 0.5)
        ramp = frame([0, 60], [0, 2000])
        self.assertAlmostEqual(pipeline.compute_energy_kwh(ramp), 1)
        self.assertEqual(pipeline.compute_energy_kwh(data.iloc[:1]), 0)

    def test_extraction_sorts_and_removes_invalid_measurements_without_mutating_cycle(self):
        cycle = {"data": [
            {"time": "2026-01-02 12:02", "value": "30"},
            {"time": "bad", "value": 999},
            {"time": "2026-01-02 12:01", "value": float("inf")},
            {"time": "2026-01-02 12:00", "value": "10"},
        ]}
        original = copy.deepcopy(cycle)
        result = pipeline.extract_cycle_df(cycle)
        self.assertEqual(result["value"].tolist(), [10, 30])
        self.assertEqual(result["time"].tolist(), [pd.Timestamp(START), pd.Timestamp(START + timedelta(minutes=2))])
        self.assertEqual(cycle, original)

    def test_shape_resampling_uses_elapsed_time_and_handles_constant_timestamps(self):
        np.testing.assert_allclose(pipeline.resample_curve(frame([0, 1, 4], [0, 10, 40]), 5), [0, 10, 20, 30, 40])
        np.testing.assert_array_equal(pipeline.resample_curve(frame([0, 0], [5, 7]), 3), [5, 5, 5])
        self.assertTrue(np.isnan(pipeline.resample_curve(frame([0], [5]), 3)).all())

    def test_curve_and_distance_normalization_are_finite_for_constant_data(self):
        values = np.array([10, 20, 30], dtype=float)
        zscore = pipeline.normalize_curve(values)
        self.assertAlmostEqual(float(zscore.mean()), 0)
        self.assertAlmostEqual(float(zscore.std()), 1)
        np.testing.assert_array_equal(pipeline.normalize_curve(values, "minmax"), [0, 0.5, 1])
        for mode in ("minmax", "zscore"):
            np.testing.assert_array_equal(pipeline.normalize_curve(np.full(3, 4), mode), [0, 0, 0])
        np.testing.assert_array_equal(pipeline.normalize_distance_matrix(np.zeros((3, 3))), np.zeros((3, 3)))
        np.testing.assert_array_equal(pipeline.normalize_distance_matrix(np.array([[0, 4], [4, 0]])), [[0, 1], [1, 0]])
        np.testing.assert_array_equal(values, [10, 20, 30])

    def test_shape_trimming_keeps_two_samples_and_includes_cutoff(self):
        data = frame([0, 1, 2, 3], [0, 10, 20, 30])
        self.assertEqual(pipeline.trim_cycle_for_shape(data, 2)["value"].tolist(), [0, 10, 20])
        self.assertEqual(pipeline.trim_cycle_for_shape(data, 0)["value"].tolist(), [0, 10])
        pd.testing.assert_frame_equal(pipeline.trim_cycle_for_shape(data, 9999), data)


class CyclePipelineIntegrationTests(unittest.TestCase):
    def run_pipeline(self, profiles, *, exact_k=2):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        data = pd.concat([
            frame([i * 10 + offset for offset in range(5)], [*profile, 0, 0])
            for i, profile in enumerate(profiles)
        ], ignore_index=True)
        source, output, cache = base / "input.tsv", base / "results", base / "cycles.pkl"
        data.to_csv(source, sep="\t", index=False)
        progress = []
        # Keep the real DTW/clustering algorithms; avoid platform-specific C
        # extensions and writing charts in numerical regression tests.
        with patch.object(pipeline.dtw, "distance_matrix_fast", side_effect=OverflowError("fixture fallback")), patch(
            "matplotlib.figure.Figure.savefig"
        ), patch("builtins.print"):
            pipeline.run_cycle_pipeline(source, output, cache, "synthetic", parameters(), exact_k,
                                        progress_callback=lambda percent, message: progress.append((percent, message)))
        return output, cache, progress

    def test_tiny_source_reaches_dtw_clustering_and_writes_consistent_artifacts(self):
        profiles = [[100, 120, 140], [110, 120, 130], [100, 110, 160],
                    [500, 650, 500], [540, 600, 480], [520, 660, 530]]
        output, cache, progress = self.run_pipeline(profiles)
        with cache.open("rb") as stream:
            cycles = pickle.load(stream)
        self.assertEqual(len(cycles), 6)
        clusters = pd.read_csv(output / "results_k2" / "clusters.csv")
        summary = pd.read_csv(output / "results_k2" / "cluster_summary.csv")
        representatives = pd.read_csv(output / "results_k2" / "cluster_representatives.csv")
        self.assertEqual(set(clusters["cycle_id"]), set(range(6)))
        self.assertEqual(clusters["cluster"].nunique(), 2)
        self.assertEqual(summary["n_cycles"].sum(), 6)
        self.assertEqual(set(representatives["cluster"]), set(clusters["cluster"]))
        for _, representative in representatives.iterrows():
            selected = clusters[clusters["cycle_id"] == representative["cycle_id"]].iloc[0]
            self.assertEqual(selected["cluster"], representative["cluster"])
        self.assertAlmostEqual(clusters.iloc[0]["energy_kwh"], 310 / 60000)
        self.assertTrue(np.isfinite(clusters.select_dtypes(include="number").to_numpy()).all())
        self.assertTrue((output / "optimal_k_report.txt").is_file())
        self.assertEqual(progress[0][0], 0)
        self.assertEqual(progress[-1], (100, "Cycle pipeline complete"))
        self.assertEqual([percent for percent, _ in progress], sorted(percent for percent, _ in progress))

    def test_exact_k_handles_identical_historical_cycles_without_invalid_silhouette(self):
        output, _cache, _progress = self.run_pipeline([[100, 100, 100]] * 3)
        clusters = pd.read_csv(output / "results_k2" / "clusters.csv")
        self.assertEqual(clusters["cluster"].nunique(), 2)
        self.assertEqual(len(clusters), 3)
        self.assertIn("scores are unavailable", (output / "optimal_k_report.txt").read_text(encoding="utf-8"))

    def test_automatic_mode_handles_a_single_natural_group_of_identical_cycles(self):
        output, _cache, _progress = self.run_pipeline([[100, 100, 100]] * 3, exact_k=None)
        clusters = pd.read_csv(output / "results_k2" / "clusters.csv")
        self.assertEqual(len(clusters), 3)
        self.assertEqual(clusters["cluster"].nunique(), 1)

    def test_fewer_than_three_cycles_has_a_clear_error_before_distance_computation(self):
        with self.assertRaisesRegex(RuntimeError, "at least 3 cycles"):
            self.run_pipeline([[100, 120, 140], [500, 600, 500]])


class CycleCompletionTests(unittest.TestCase):
    def test_completion_preserves_partial_and_shifts_only_later_reference_samples(self):
        partial = frame([0, 1], [11, 21])
        reference = frame([0, 1, 2, 3], [10, 20, 30, 0], start=datetime(2025, 1, 1))
        completed, suffix = controller._build_completed_cycle(partial, reference)
        self.assertEqual(completed["value"].tolist(), [11, 21, 30, 0])
        self.assertEqual(suffix["time"].tolist(), [pd.Timestamp(START + timedelta(minutes=2)), pd.Timestamp(START + timedelta(minutes=3))])
        pd.testing.assert_frame_equal(partial, frame([0, 1], [11, 21]))

    def test_reference_shorter_than_partial_adds_no_samples(self):
        partial = frame([0, 1, 2], [10, 20, 30])
        completed, suffix = controller._build_completed_cycle(partial, frame([0, 1], [10, 20]))
        pd.testing.assert_frame_equal(completed, partial)
        self.assertTrue(suffix.empty)

    def test_trailing_cycle_is_selected_only_when_short_and_not_confirmed_off(self):
        data = frame(range(8), [100, 120, 140, 0, 0, 0, 100, 120])
        options = dict(threshold=50, min_duration_minutes=2, min_off_minutes=1, max_idle_gap_minutes=5)
        result = controller._extract_trailing_short_cycle(data, **options)
        self.assertEqual(result["value"].tolist(), [100, 120])
        self.assertTrue(controller._extract_trailing_short_cycle(pd.concat([data, frame([8, 9], [0, 0])]), **options).empty)

    def test_controller_csv_loader_handles_mixed_timestamps_and_rejects_infinite_power(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text("time,value\n2026-01-02 12:00,10\n2026-01-02 12:01:00,20\n2026-01-02 12:02,inf\nbad,999\n", encoding="utf-8")
            result = controller._load_time_value_csv(path)
        self.assertEqual(result["value"].tolist(), [10, 20])


if __name__ == "__main__":
    unittest.main()
