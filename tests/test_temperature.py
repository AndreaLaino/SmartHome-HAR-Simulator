import math
import os
import tempfile
import unittest
from collections import deque
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import graph
import sensor
import sim
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
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.chdir(tmpdir)
                self.assertEqual(graph._get_binding_dht_gpio("t2"), 17)
        finally:
            os.chdir(previous_cwd)

    def test_latest_previous_real_day_is_rebased_for_future_simulation(self):
        real = pd.DataFrame(
            {"value": [22.0, 23.0]},
            index=pd.to_datetime(["2026-07-01 12:00", "2026-07-01 12:01"]),
        )
        simulated = pd.DataFrame(
            {"value": [22.0, 23.0]},
            index=pd.to_datetime(["2026-07-14 12:00", "2026-07-14 12:01"]),
        )

        aligned = graph._align_real_series_to_simulation(real, simulated)

        self.assertEqual(aligned.index[0], pd.Timestamp("2026-07-14 12:00"))
        self.assertEqual(aligned["value"].tolist(), [22.0, 23.0])


if __name__ == "__main__":
    unittest.main()
