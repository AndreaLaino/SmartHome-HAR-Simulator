"""Local profile fixtures keep replay and LLM sensor tests self-contained."""

import pickle
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import sensor
from models import Sensor


class TemperatureSeriesLoadingTests(unittest.TestCase):
    def setUp(self):
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        for name in (
            "TEMP_RECENT", "TEMP_GREEN_UNTIL", "TEMP_BASELINE", "TEMP_CSV_BASELINE",
            "TEMP_DEVICE_OFFSET", "TEMP_DEVICE_HEAT", "TEMP_SOURCE_MODE",
            "TEMP_LAST_DATETIME", "TEMP_SIM_MIN", "TEMP_SERIES", "TEMP_SERIES_SIGNATURE",
        ):
            self.contexts.enter_context(patch.dict(getattr(sensor, name), clear=True))
        self.contexts.enter_context(patch.object(sensor, "_temperature_source_signature", return_value=("fixture",)))
        self.contexts.enter_context(patch("builtins.print"))

    def test_series_cache_reuses_unchanged_source(self):
        frame = pd.DataFrame({"value": [21, 22]}, index=pd.to_datetime(["2026-01-02 12:00", "2026-01-02 12:01"]))
        with patch.object(sensor, "load_temp_by_label_any_csv", return_value=frame) as loader:
            first = sensor._load_temp_series_for_sensor("temp")
            second = sensor._load_temp_series_for_sensor("temp")
        self.assertIs(first, second)
        loader.assert_called_once_with("temp")

    def test_series_normalizes_timezone_sorts_and_merges_duplicate_timestamps(self):
        frame = pd.DataFrame(
            {"value": [24, 20, 22, float("inf"), "bad"]},
            index=pd.to_datetime(["2026-01-02 12:01", "2026-01-02 12:00", "2026-01-02 12:00", "2026-01-02 12:02", "2026-01-02 12:03"]).tz_localize("Europe/Paris"),
        )
        with patch.object(sensor, "load_temp_by_label_any_csv", return_value=frame):
            times, values = sensor._load_temp_series_for_sensor("temp")
        self.assertEqual(times, [datetime(2026, 1, 2, 12), datetime(2026, 1, 2, 12, 1)])
        self.assertEqual(values, [21, 24])

    def test_gpio_binding_fills_in_when_label_has_no_recordings(self):
        frame = pd.DataFrame({"value": [19]}, index=pd.to_datetime(["2026-01-02 12:00"]))
        with patch.object(sensor, "load_temp_by_label_any_csv", return_value=pd.DataFrame()), patch.object(sensor, "_load_sensor_map", return_value={"temp": {"by": "dht", "gpio": "4"}}), patch.object(sensor, "load_temp_by_gpio_any_csv", return_value=frame) as loader:
            self.assertEqual(sensor._load_temp_series_for_sensor("temp")[1], [19])
        loader.assert_called_once_with(4)

    def test_valid_label_recording_takes_precedence_over_gpio_binding(self):
        frame = pd.DataFrame({"value": [19]}, index=pd.to_datetime(["2026-01-02 12:00"]))
        with patch.object(sensor, "load_temp_by_label_any_csv", return_value=frame), patch.object(sensor, "load_temp_by_gpio_any_csv") as gpio_loader:
            self.assertEqual(sensor._load_temp_series_for_sensor("temp")[1], [19])
        gpio_loader.assert_not_called()

    def test_only_invalid_values_yield_no_series(self):
        frame = pd.DataFrame({"value": [float("nan"), float("inf"), "bad"]}, index=pd.to_datetime(["2026-01-02 12:00", "2026-01-02 12:01", "2026-01-02 12:02"]))
        with patch.object(sensor, "load_temp_by_label_any_csv", return_value=frame):
            self.assertIsNone(sensor._load_temp_series_for_sensor("temp"))
        self.assertIsNone(sensor.TEMP_SERIES["temp"])

    def test_runtime_reset_can_preserve_source_cache(self):
        sensor.TEMP_SERIES["temp"] = ([datetime(2026, 1, 2, 12)], [19])
        sensor.TEMP_SERIES_SIGNATURE["temp"] = ("fixture",)
        with patch.dict(sensor.TEMP_SIM_MIN, {"temp": 5}, clear=True), patch.dict(sensor.TEMP_DEVICE_OFFSET, {"temp": 1}, clear=True):
            sensor.reset_temperature_runtime_state(clear_series_cache=False)
            self.assertEqual(sensor.TEMP_SIM_MIN, {})
            self.assertEqual(sensor.TEMP_DEVICE_OFFSET, {})
        self.assertEqual(sensor.TEMP_SERIES["temp"][1], [19])
        self.assertEqual(sensor.TEMP_SERIES_SIGNATURE["temp"], ("fixture",))

    def test_temperature_remains_finite_with_corrupt_initial_values_and_bounds(self):
        for state, lower, upper, delta, heating in [
            (float("nan"), 0, 50, 1, 0),
            ("bad", float("nan"), float("inf"), 1, 0),
            (20, 30, 10, float("inf"), float("nan")),
        ]:
            with self.subTest(state=state, lower=lower, upper=upper):
                thermometer = Sensor("corrupt", 0, 0, "Temperature", lower, upper, 0.1, state)
                with patch.dict(sensor.TEMP_BASELINE, clear=True), patch.dict(sensor.TEMP_DEVICE_OFFSET, clear=True), patch.dict(sensor.TEMP_DEVICE_HEAT, clear=True), patch.object(sensor, "get_replay_temperature", return_value=None):
                    actual = sensor.compute_temperature(thermometer, heating, delta, datetime(2026, 1, 2, 12), [])
                self.assertGreaterEqual(actual, 0)
                self.assertLessEqual(actual, 50)


class LlmSensorProfileFixtureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.contexts.enter_context(patch.dict(sensor.LLM_CYCLE_CURVE_CACHE, clear=True))
        self.contexts.enter_context(patch.dict(sensor.LLM_CLUSTER_CASES_CACHE, clear=True))
        self.profile = {"appliance_key": "oven", "selected_cycle_id": 7, "pkl_path": str(self.directory / "cycles.pkl"), "output_dir": str(self.directory), "chosen_k": 2}

    def write_cycles(self, data):
        with (self.directory / "cycles.pkl").open("wb") as stream:
            pickle.dump(data, stream)

    def write_completion(self, rows):
        pd.DataFrame(rows, columns=["time", "value", "segment"]).to_csv(self.directory / "completed_last_cluster_data.csv", index=False)

    def test_historical_curve_selects_requested_cycle_and_uses_relative_minutes(self):
        self.write_cycles([
            {"cycle_id": 2, "data": [{"time": "2026-01-01 00:00", "value": 999}]},
            {"cycle_id": 7, "data": [{"time": "2026-01-02 12:02", "value": 30}, {"time": "2026-01-02 12:00", "value": 10}]},
        ])
        self.assertEqual(sensor._load_llm_cycle_curve(self.profile), ([0, 2], [10, 30]))
        self.assertIsNone(sensor._load_llm_cycle_curve_for_cycle_id(self.profile, cycle_id_override=99))

    def test_historical_curve_is_cached_per_cycle(self):
        self.write_cycles([{"cycle_id": 7, "data": [{"time": "2026-01-02 12:00", "value": 10}, {"time": "2026-01-02 12:02", "value": 30}]}])
        first = sensor._load_llm_cycle_curve(self.profile)
        with patch.object(sensor.pickle, "load", side_effect=AssertionError("unchanged curve reloaded")):
            second = sensor._load_llm_cycle_curve(self.profile)
        self.assertIs(first, second)

    def test_missing_corrupt_and_malformed_catalog_data_have_no_curve(self):
        self.assertIsNone(sensor._load_llm_cycle_curve(self.profile))
        for payload in ({"not": "a list"}, [{"cycle_id": 7, "data": []}], [{"cycle_id": 7, "data": [{"time": "bad", "value": "bad"}]}], [{"cycle_id": 7, "data": [{"time": "2026-01-02 12:00", "value": 10}]}]):
            with self.subTest(payload=payload):
                sensor.LLM_CYCLE_CURVE_CACHE.clear()
                self.write_cycles(payload)
                self.assertIsNone(sensor._load_llm_cycle_curve(self.profile))
        sensor.LLM_CYCLE_CURVE_CACHE.clear()
        (self.directory / "cycles.pkl").write_bytes(b"corrupt")
        self.assertIsNone(sensor._load_llm_cycle_curve(self.profile))

    def test_historical_curve_ignores_nonfinite_power(self):
        self.write_cycles([{"cycle_id": 7, "data": [{"time": "2026-01-02 12:00", "value": 10}, {"time": "2026-01-02 12:01", "value": float("inf")}, {"time": "2026-01-02 12:02", "value": 30}]}])
        self.assertEqual(sensor._load_llm_cycle_curve(self.profile), ([0, 2], [10, 30]))

    def test_completion_curve_combines_partial_and_strictly_later_completion(self):
        self.write_completion([
            ["2026-01-02 12:00", 10, "initial_partial"],
            ["2026-01-02 12:01", 20, "initial_partial"],
            ["2026-01-02 12:02", 30, "added_completion"],
            ["2026-01-02 12:03", 0, "added_completion"],
        ])
        self.assertEqual(sensor._load_llm_completion_curve(self.profile), ([0, 1, 2, 3], [10, 20, 30, 0]))

    def test_completion_rejects_missing_or_overlapping_segments(self):
        for rows in [
            [["2026-01-02 12:00", 10, "initial_partial"], ["2026-01-02 12:01", 20, "initial_partial"]],
            [["2026-01-02 12:00", 10, "initial_partial"], ["2026-01-02 12:01", 20, "initial_partial"], ["2026-01-02 12:01", 30, "added_completion"]],
        ]:
            with self.subTest(rows=rows):
                self.write_completion(rows)
                self.assertIsNone(sensor._load_llm_completion_curve(self.profile))

    def test_completion_curve_ignores_nonfinite_power(self):
        self.write_completion([
            ["2026-01-02 12:00", 10, "initial_partial"],
            ["2026-01-02 12:01", float("inf"), "initial_partial"],
            ["2026-01-02 12:02", 30, "added_completion"],
        ])
        self.assertEqual(sensor._load_llm_completion_curve(self.profile), ([0, 2], [10, 30]))

    def test_cluster_catalog_filters_bad_rows_and_derives_weekday(self):
        results = self.directory / "results_k2"
        results.mkdir()
        pd.DataFrame([
            {"cycle_id": "7", "cluster": "1", "start_time": "2026-01-02 12:00", "end_time": "2026-01-02 12:02"},
            {"cycle_id": "bad", "cluster": "1", "start_time": "2026-01-02 12:00", "end_time": "2026-01-02 12:02"},
            {"cycle_id": "8", "cluster": "1", "start_time": "bad", "end_time": "2026-01-02 12:02"},
        ]).to_csv(results / "clusters.csv", index=False)
        frame = sensor._load_llm_cluster_cases(self.profile)
        self.assertEqual(frame["cycle_id"].tolist(), [7])
        self.assertEqual(frame["cluster"].tolist(), [1])
        self.assertEqual(frame["weekday"].tolist(), [4])

    def test_realtime_power_standardization_supports_public_column_aliases(self):
        for time_column in ("time", "timestamp", "timestamp_iso", "datetime"):
            for power_column in ("value", "power_W", "power", "consumption"):
                with self.subTest(time_column=time_column, power_column=power_column):
                    raw = pd.DataFrame({time_column: ["2026-01-02 12:01", "2026-01-02 12:00"], power_column: ["20", "10"]})
                    frame = sensor._standardize_realtime_power_df(raw)
                    self.assertEqual(frame["value"].tolist(), [10, 20])
                    self.assertEqual(frame["time"].tolist(), [pd.Timestamp("2026-01-02 12:00"), pd.Timestamp("2026-01-02 12:01")])

    def test_realtime_power_standardization_filters_nonfinite_and_invalid_rows(self):
        raw = pd.DataFrame({"time": ["2026-01-02 12:00", "2026-01-02 12:01", "2026-01-02 12:02", "bad"], "value": [0, float("inf"), 20, 30]})
        frame = sensor._standardize_realtime_power_df(raw)
        self.assertEqual(frame["value"].tolist(), [0, 20])
        self.assertIsNone(sensor._standardize_realtime_power_df(pd.DataFrame({"time": ["2026-01-02 12:00"], "value": [10]})))

    def test_cycle_interpolation_clamps_negative_power_and_honors_repeat_policy(self):
        for minute, repeat, expected in [(-1, False, 10), (1, False, 20), (2, False, 30), (3, False, 0), (3, True, 20)]:
            with self.subTest(minute=minute, repeat=repeat):
                self.assertEqual(sensor._interp_cycle_value([0, 2], [10, 30], minute, repeat=repeat), expected)
        self.assertEqual(sensor._interp_cycle_value([0, 2], [-10, -30], 1, repeat=False), 0)
        self.assertEqual(sensor._interp_cycle_value([], [], 1), 0)


if __name__ == "__main__":
    unittest.main()
