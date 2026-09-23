import math
import os
import tempfile
import unittest
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import graph
import sensor
import sim
from app.hardware import real_sensors
from house_state import HouseState
from models import Device, Sensor


def make_temperature_sensor(state=30.0):
    return Sensor("t_test", 0, 0, "Temperature", 0, 50, 0.1, state)


def make_oven(state=1, x=0, y=0):
    return Device("oven", x, y, "Oven", 1000, state, 0, 1000)


class TemperatureModelTests(unittest.TestCase):
    def setUp(self):
        sensor.reset_temperature_runtime_state()

    def tearDown(self):
        sensor.reset_temperature_runtime_state()

    def test_csv_is_full_baseline_without_synthetic_daily_curve(self):
        temp_sensor = make_temperature_sensor(state=20.0)
        with patch.object(sensor, "get_replay_temperature", return_value=30.0):
            result = sensor.compute_temperature(
                temp_sensor,
                heating_factor=0,
                delta_seconds=120,
                current_datetime=datetime(2026, 7, 14, 12, 0),
                active_devices=[],
            )

        self.assertEqual(result, 30.0)

    def test_csv_starts_at_the_selected_time_without_an_artificial_ramp(self):
        temp_sensor = make_temperature_sensor(state=23.0)
        with patch.object(sensor, "get_replay_temperature", return_value=30.0):
            result = sensor.compute_temperature(
                temp_sensor,
                heating_factor=0,
                delta_seconds=1,
                current_datetime=datetime(2026, 7, 14, 13, 0),
                active_devices=[],
            )

        self.assertEqual(result, 30.0)

    def test_changes_between_csv_samples_are_smoothed(self):
        temp_sensor = make_temperature_sensor(state=30.0)
        with patch.object(sensor, "get_replay_temperature", side_effect=[30.0, 32.0]):
            first = sensor.compute_temperature(
                temp_sensor, 0, 1, datetime(2026, 7, 14, 13, 0), []
            )
            temp_sensor.state = first
            second = sensor.compute_temperature(
                temp_sensor, 0, 1, datetime(2026, 7, 14, 13, 1), []
            )

        self.assertEqual(first, 30.0)
        self.assertGreater(second, 30.0)
        self.assertLess(second, 32.0)
        expected = round(
            30.0
            + (1.0 - math.exp(-1.0 / sensor.CSV_SMOOTHING_TIME_CONSTANT_MIN))
            * (32.0 - 30.0),
            2,
        )
        self.assertEqual(second, expected)

    def test_csv_adds_twenty_five_percent_of_nearby_device_heat(self):
        temp_sensor = make_temperature_sensor(state=30.0)
        oven = make_oven()
        with patch.object(sensor, "get_replay_temperature", return_value=30.0):
            result = sensor.compute_temperature(
                temp_sensor,
                heating_factor=1,
                delta_seconds=120,
                current_datetime=datetime(2026, 7, 14, 12, 0),
                active_devices=[oven],
            )

        target = 30.0 + sensor.CSV_DEVICE_EFFECT_SCALE * 3.5
        alpha = 1.0 - math.exp(-120.0 / sensor.DEVICE_EFFECT_TIME_CONSTANT_MIN)
        expected = round(30.0 + alpha * (target - 30.0), 2)
        self.assertEqual(result, expected)

    def test_nearby_oven_is_not_counted_twice(self):
        oven = make_oven()
        with patch.object(sensor, "get_replay_temperature", return_value=30.0):
            first = sensor.compute_temperature(
                make_temperature_sensor(), 0, 120, datetime(2026, 7, 14, 12), [oven]
            )
            sensor.reset_temperature_runtime_state()
            second = sensor.compute_temperature(
                make_temperature_sensor(), 1, 120, datetime(2026, 7, 14, 12), [oven]
            )

        self.assertEqual(first, second)

    def test_without_csv_heating_and_cooling_are_gradual(self):
        temp_sensor = make_temperature_sensor(state=18.0)
        oven = make_oven()

        with patch.object(sensor, "get_replay_temperature", return_value=None):
            first_heating = None
            for minute in range(120):
                temp_sensor.state = sensor.compute_temperature(
                    temp_sensor, 0, 1, datetime(2026, 7, 14, 12, 0), [oven]
                )
                if first_heating is None:
                    first_heating = temp_sensor.state

            heated = temp_sensor.state
            after_switch_off = sensor.compute_temperature(
                temp_sensor, 0, 1, datetime(2026, 7, 14, 14, 0), []
            )
            temp_sensor.state = after_switch_off
            for minute in range(60):
                temp_sensor.state = sensor.compute_temperature(
                    temp_sensor, 0, 1, datetime(2026, 7, 14, 14, 1), []
                )

        self.assertGreater(first_heating, 18.0)
        self.assertLess(first_heating, 21.5)
        self.assertGreater(heated, first_heating)
        self.assertLessEqual(abs(after_switch_off - heated), 0.05)
        self.assertGreater(after_switch_off, 18.0)
        self.assertLess(temp_sensor.state, after_switch_off)
        self.assertGreater(temp_sensor.state, 18.0)

    def test_without_csv_cooling_does_not_stall_after_rounding(self):
        temp_sensor = make_temperature_sensor(state=18.0)
        oven = make_oven()

        with patch.object(sensor, "get_replay_temperature", return_value=None):
            heated = sensor.compute_temperature(
                temp_sensor, 0, 120, datetime(2026, 7, 14, 12, 0), [oven]
            )
            temp_sensor.state = heated
            for minute in range(1, 301):
                temp_sensor.state = sensor.compute_temperature(
                    temp_sensor, 0, 1, datetime(2026, 7, 14, 12, 0), []
                )

        self.assertEqual(temp_sensor.state, 18.0)

    def test_reset_clears_temperature_runtime_and_csv_cache(self):
        sensor.TEMP_RECENT["t"] = deque([20.0])
        sensor.TEMP_GREEN_UNTIL["t"] = 10.0
        sensor.TEMP_BASELINE["t"] = 20.0
        sensor.TEMP_CSV_BASELINE["t"] = 20.0
        sensor.TEMP_DEVICE_OFFSET["t"] = 0.5
        sensor.TEMP_DEVICE_HEAT["t"] = 0.8
        sensor.TEMP_SOURCE_MODE["t"] = True
        sensor.TEMP_LAST_DATETIME["t"] = datetime(2026, 7, 14, 12, 0)
        sensor.TEMP_SIM_MIN["t"] = 10.0
        sensor.TEMP_SERIES["t"] = ([], [])
        sensor.TEMP_SERIES_SIGNATURE["t"] = ("source",)

        sensor.reset_temperature_runtime_state()

        self.assertFalse(sensor.TEMP_RECENT)
        self.assertFalse(sensor.TEMP_GREEN_UNTIL)
        self.assertFalse(sensor.TEMP_BASELINE)
        self.assertFalse(sensor.TEMP_CSV_BASELINE)
        self.assertFalse(sensor.TEMP_DEVICE_OFFSET)
        self.assertFalse(sensor.TEMP_DEVICE_HEAT)
        self.assertFalse(sensor.TEMP_SOURCE_MODE)
        self.assertFalse(sensor.TEMP_LAST_DATETIME)
        self.assertFalse(sensor.TEMP_SIM_MIN)
        self.assertFalse(sensor.TEMP_SERIES)
        self.assertFalse(sensor.TEMP_SERIES_SIGNATURE)

    def test_csv_cache_is_reloaded_when_source_changes(self):
        first = pd.DataFrame(
            {"value": [20.0]},
            index=pd.to_datetime(["2026-07-14 12:00"]),
        )
        second = pd.DataFrame(
            {"value": [25.0]},
            index=pd.to_datetime(["2026-07-14 12:00"]),
        )

        with patch.object(sensor, "_temperature_source_signature", side_effect=[("first",), ("second",)]), patch.object(
            sensor, "load_temp_by_label_any_csv", side_effect=[first, second]
        ) as loader:
            first_series = sensor._load_temp_series_for_sensor("t_test")
            second_series = sensor._load_temp_series_for_sensor("t_test")

        self.assertEqual(first_series[1], [20.0])
        self.assertEqual(second_series[1], [25.0])
        self.assertEqual(loader.call_count, 2)

    def test_replay_uses_closest_previous_available_day(self):
        datetimes = pd.to_datetime(
            [
                "2026-07-01 12:00",
                "2026-07-01 13:00",
                "2026-07-02 12:00",
                "2026-07-02 13:00",
            ]
        ).tolist()
        values = [20.0, 22.0, 30.0, 34.0]

        with patch.object(sensor, "_load_temp_series_for_sensor", return_value=(datetimes, values)):
            result = sensor.get_replay_temperature("t_test", datetime(2026, 7, 14, 12, 30))

        self.assertEqual(result, 32.0)

    def test_replay_smoothly_bridges_uncovered_overnight_hours(self):
        datetimes = pd.to_datetime(["2026-07-14 06:00", "2026-07-14 22:00"]).tolist()
        values = [23.0, 27.0]

        with patch.object(sensor, "_load_temp_series_for_sensor", return_value=(datetimes, values)):
            result = sensor.get_replay_temperature("t_test", datetime(2026, 7, 15, 0, 0))

        self.assertEqual(result, 26.0)

    def test_replay_uses_earliest_future_day_when_no_past_day_exists(self):
        datetimes = pd.to_datetime(
            ["2026-11-01 12:00", "2026-11-01 13:00", "2026-12-01 12:00"]
        ).tolist()
        values = [24.0, 26.0, 31.0]

        with patch.object(sensor, "_load_temp_series_for_sensor", return_value=(datetimes, values)):
            result = sensor.get_replay_temperature("t_test", datetime(2026, 9, 22, 12, 30))

        self.assertEqual(result, 25.0)

    def test_replay_prefers_exact_date_over_past_and_future_profiles(self):
        datetimes = pd.to_datetime(
            ["2026-08-01 12:00", "2026-09-22 12:00", "2026-11-01 12:00"]
        ).tolist()
        values = [20.0, 25.0, 30.0]

        with patch.object(sensor, "_load_temp_series_for_sensor", return_value=(datetimes, values)):
            result = sensor.get_replay_temperature("t_test", datetime(2026, 9, 22, 12, 0))

        self.assertEqual(result, 25.0)

    def test_constant_profile_remains_constant_at_every_time(self):
        datetimes = pd.to_datetime(["2026-07-14 06:00", "2026-07-14 18:00"]).tolist()
        values = [23.5, 23.5]

        with patch.object(sensor, "_load_temp_series_for_sensor", return_value=(datetimes, values)):
            results = [
                sensor.get_replay_temperature("t_test", datetime(2026, 7, 20, hour, 0))
                for hour in (0, 6, 12, 18, 23)
            ]

        self.assertEqual(results, [23.5] * 5)

    def test_recorded_temperature_never_borrows_past_or_future_data(self):
        datetimes = pd.to_datetime(["2026-08-01 12:00", "2026-11-01 12:00"]).tolist()
        values = [20.0, 30.0]

        with patch.object(sensor, "_load_temp_series_for_sensor", return_value=(datetimes, values)):
            result = sensor.get_recorded_temperature("t_test", datetime(2026, 9, 22, 12, 0))

        self.assertIsNone(result)

    def test_recorded_temperature_matches_only_the_exact_source_minute(self):
        datetimes = pd.to_datetime(["2026-09-22 12:00", "2026-09-22 12:02"]).tolist()
        values = [24.0, 26.0]

        with patch.object(sensor, "_load_temp_series_for_sensor", return_value=(datetimes, values)):
            exact = sensor.get_recorded_temperature("t_test", datetime(2026, 9, 22, 12, 0, 45))
            missing = sensor.get_recorded_temperature("t_test", datetime(2026, 9, 22, 12, 1))

        self.assertEqual(exact, 24.0)
        self.assertIsNone(missing)

    def test_nearby_devices_add_heat_and_far_devices_do_not(self):
        near_computer = Device("pc", 0, 0, "Computer", 100, 1, 0, 100)
        near_coffee = Device("coffee", 0, 0, "Coffee_Machine", 100, 1, 0, 100)
        far_oven = make_oven(x=1000, y=1000)

        with patch.object(sensor, "get_replay_temperature", return_value=25.0):
            baseline = sensor.compute_temperature(
                make_temperature_sensor(25.0), 0, 120, datetime(2026, 7, 14, 12), []
            )
            sensor.reset_temperature_runtime_state()
            with_nearby = sensor.compute_temperature(
                make_temperature_sensor(25.0),
                0,
                120,
                datetime(2026, 7, 14, 12),
                [near_computer, near_coffee, far_oven],
            )

        self.assertEqual(baseline, 25.0)
        self.assertGreater(with_nearby, baseline)

    def test_sensors_keep_independent_temperature_state(self):
        first_sensor = Sensor("t_a", 0, 0, "Temperature", 0, 50, 0.1, 20.0)
        second_sensor = Sensor("t_b", 1000, 1000, "Temperature", 0, 50, 0.1, 28.0)
        oven = make_oven()

        with patch.object(sensor, "get_replay_temperature", return_value=None):
            first_result = sensor.compute_temperature(
                first_sensor, 0, 120, datetime(2026, 7, 14, 12), [oven]
            )
            second_result = sensor.compute_temperature(
                second_sensor, 0, 120, datetime(2026, 7, 14, 12), [oven]
            )

        self.assertGreater(first_result, 20.0)
        self.assertEqual(second_result, 28.0)
        self.assertEqual(sensor.TEMP_BASELINE["t_a"], 20.0)
        self.assertEqual(sensor.TEMP_BASELINE["t_b"], 28.0)

    def test_zero_and_negative_delta_do_not_advance_device_heat(self):
        oven = make_oven()
        with patch.object(sensor, "get_replay_temperature", return_value=None):
            zero = sensor.compute_temperature(
                make_temperature_sensor(20.0), 0, 0, datetime(2026, 7, 14, 12), [oven]
            )
            negative = sensor.compute_temperature(
                make_temperature_sensor(zero), 0, -5, datetime(2026, 7, 14, 12), [oven]
            )

        self.assertEqual(zero, 20.0)
        self.assertEqual(negative, 20.0)

    def test_temperature_honors_configured_sensor_bounds(self):
        bounded = Sensor("bounded", 0, 0, "Temperature", 18, 26, 0.1, 22.0)
        with patch.object(sensor, "get_replay_temperature", return_value=35.0):
            high = sensor.compute_temperature(
                bounded, 0, 120, datetime(2026, 7, 14, 12), []
            )
        sensor.reset_temperature_runtime_state()
        bounded.state = 10.0
        with patch.object(sensor, "get_replay_temperature", return_value=None):
            low = sensor.compute_temperature(
                bounded, 0, 120, datetime(2026, 7, 14, 12), []
            )

        self.assertEqual(high, 26.0)
        self.assertEqual(low, 18.0)

    def test_rewinding_time_resets_csv_smoothing_to_the_new_target(self):
        temp_sensor = make_temperature_sensor(25.0)
        with patch.object(sensor, "get_replay_temperature", side_effect=[25.0, 30.0, 20.0]):
            first = sensor.compute_temperature(temp_sensor, 0, 1, datetime(2026, 9, 22, 12), [])
            temp_sensor.state = first
            second = sensor.compute_temperature(temp_sensor, 0, 1, datetime(2026, 9, 22, 13), [])
            temp_sensor.state = second
            rewound = sensor.compute_temperature(temp_sensor, 0, 1, datetime(2026, 9, 22, 11), [])

        self.assertGreater(second, 25.0)
        self.assertEqual(rewound, 20.0)

    def test_removing_csv_source_does_not_double_the_existing_offset(self):
        temp_sensor = make_temperature_sensor(25.0)
        oven = make_oven()
        with patch.object(sensor, "get_replay_temperature", side_effect=[25.0, None]):
            with_csv = sensor.compute_temperature(
                temp_sensor, 0, 120, datetime(2026, 9, 22, 12), [oven]
            )
            temp_sensor.state = with_csv
            after_removal = sensor.compute_temperature(
                temp_sensor, 0, 0, datetime(2026, 9, 22, 12, 1), [oven]
            )

        self.assertEqual(after_removal, with_csv)


class TemperatureGraphTests(unittest.TestCase):
    def test_recorded_real_temperature_uses_simulation_timestamps(self):
        data = {
            "real_state": [22.0, 23.0],
            "time": ["2026-07-14 12:00", "2026-07-14 12:01"],
        }

        result = graph._recorded_real_temperature_series(data["time"], data)

        self.assertEqual(result["value"].tolist(), [22.0, 23.0])
        self.assertEqual(result.index[0], pd.Timestamp("2026-07-14 12:00"))

    def test_temperature_store_keeps_real_and_simulated_samples_together(self):
        state_store = {}
        temp_sensor = make_temperature_sensor()
        updates = [(temp_sensor, "t_test", 30.5, 30.0)]

        sim._store_temperature_updates(state_store, "2026-07-14 12:00", updates)

        self.assertEqual(state_store["t_test"]["state"], [30.5])
        self.assertEqual(state_store["t_test"]["real_state"], [30.0])
        self.assertEqual(state_store["t_test"]["type"], "Temperature")

    def test_temperature_collection_uses_exact_recording_not_replay_profile(self):
        temp_sensor = make_temperature_sensor()
        house_state = HouseState(initial_context={"devices": [], "delta_seconds": 1})

        with patch.object(
            sim.TemperatureSensorAdapter, "update", return_value=("t_test", 25.0)
        ), patch.object(sim, "get_recorded_temperature", return_value=None) as recorded:
            updates = sim._collect_temperature_updates(
                house_state,
                [temp_sensor],
                [],
                delta_seconds=1,
                current_datetime=datetime(2026, 9, 22, 12),
            )

        self.assertIsNone(updates[0][3])
        recorded.assert_called_once_with("t_test", datetime(2026, 9, 22, 12))

    def test_real_temperature_prefers_label_csv(self):
        frame = pd.DataFrame(
            {"value": [22.0, 23.0]},
            index=pd.to_datetime(["2026-07-14 12:00", "2026-07-14 12:01"]),
        )
        with patch.object(graph.real_sensors, "load_temp_by_label_any_csv", return_value=frame), patch.object(
            graph.real_sensors, "load_temp_by_gpio_any_csv"
        ) as gpio_loader:
            result = graph._load_real_temperature_series("t2")

        pd.testing.assert_frame_equal(result, frame)
        gpio_loader.assert_not_called()

    def test_sensor_map_is_resolved_from_project_directory(self):
        previous_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                os.chdir(tmpdir)
                self.assertEqual(graph._get_binding_dht_gpio("t1"), 4)
            finally:
                os.chdir(previous_cwd)

    def test_real_data_from_another_day_is_not_plotted(self):
        real = pd.DataFrame(
            {"value": [22.0, 23.0]},
            index=pd.to_datetime(["2026-07-01 12:00", "2026-07-01 12:01"]),
        )
        simulated = pd.DataFrame(
            {"value": [22.0, 23.0]},
            index=pd.to_datetime(["2026-07-14 12:00", "2026-07-14 12:01"]),
        )

        aligned = graph._align_real_series_to_simulation(real, simulated)

        self.assertTrue(aligned.empty)

    def test_real_data_is_cropped_to_actual_simulation_overlap(self):
        real = pd.DataFrame(
            {"value": [21.0, 22.0, 23.0, 24.0]},
            index=pd.to_datetime(
                [
                    "2026-09-22 11:59",
                    "2026-09-22 12:00",
                    "2026-09-22 12:01",
                    "2026-09-22 12:02",
                ]
            ),
        )
        simulated = pd.DataFrame(
            {"value": [25.0, 25.1]},
            index=pd.to_datetime(["2026-09-22 12:00", "2026-09-22 12:01"]),
        )

        aligned = graph._align_real_series_to_simulation(real, simulated)

        self.assertEqual(aligned["value"].tolist(), [22.0, 23.0])
        self.assertEqual(aligned.index[0], pd.Timestamp("2026-09-22 12:00"))

    def test_real_alignment_handles_timezone_aware_data(self):
        real = pd.DataFrame(
            {"value": [22.0]},
            index=pd.DatetimeIndex([datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)]),
        )
        simulated = pd.DataFrame(
            {"value": [25.0]}, index=pd.to_datetime(["2026-09-22 12:00"])
        )

        aligned = graph._align_real_series_to_simulation(real, simulated)

        self.assertEqual(aligned["value"].tolist(), [22.0])
        self.assertIsNone(aligned.index.tz)

    def test_real_alignment_preserves_missing_minutes_as_line_breaks(self):
        real = pd.DataFrame(
            {"value": [22.0, float("nan"), 24.0]},
            index=pd.to_datetime(
                ["2026-09-22 12:00", "2026-09-22 12:01", "2026-09-22 12:02"]
            ),
        )
        simulated = pd.DataFrame(
            {"value": [25.0, 25.0, 25.0]},
            index=pd.to_datetime(
                ["2026-09-22 12:00", "2026-09-22 12:01", "2026-09-22 12:02"]
            ),
        )

        aligned = graph._align_real_series_to_simulation(real, simulated)

        self.assertEqual(len(aligned), 3)
        self.assertTrue(math.isnan(aligned["value"].iloc[1]))


class TemperatureCsvLoadingTests(unittest.TestCase):
    def test_invalid_and_infinite_csv_values_are_ignored(self):
        rows = [
            {"timestamp": "2026-09-22 12:00", "value": 22.0},
            {"timestamp": "2026-09-22 12:01", "value": float("nan")},
            {"timestamp": "2026-09-22 12:02", "value": float("inf")},
            {"timestamp": "not-a-date", "value": 30.0},
        ]

        loaded = real_sensors._df_from_rows(rows)

        self.assertEqual(loaded["value"].dropna().tolist(), [22.0])


if __name__ == "__main__":
    unittest.main()
