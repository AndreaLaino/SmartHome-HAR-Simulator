"""Sensor and hardware contracts, exercised without devices or network access."""

import copy
import csv
import math
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import pandas as pd

import consumption_profiles as consumption
import prediction
import sensor
from app.hardware import real_sensors, smartmeter
from house_state import HouseState
from models import Device, Sensor


NOW = datetime(2026, 1, 2, 12)


def make_sensor(name="meter", kind="Smart Meter", associated_device="oven"):
    return Sensor(name, 0, 0, kind, 0, 5000, 1, 0, associated_device=associated_device)


def write_csv(path, fields, rows):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(fields)
        writer.writerows(rows)


class ConsumptionProfileRegressionTests(unittest.TestCase):
    def test_linear_interpolation_handles_empty_single_and_unsorted_profiles(self):
        cases = [
            ({}, 10, 7),
            ({4: 12}, -100, 12),
            ({4: 12}, 100, 12),
            ({10: 100, 0: 0, 3: 60}, -1, 0),
            ({10: 100, 0: 0, 3: 60}, 1.5, 30),
            ({10: 100, 0: 0, 3: 60}, 6.5, 80),
            ({10: 100, 0: 0, 3: 60}, 100, 100),
        ]
        for profile, minute, expected in cases:
            with self.subTest(profile=profile, minute=minute):
                self.assertEqual(consumption.interpolated_consumption(profile, minute, 7), expected)

    def test_step_profile_changes_at_sample_boundary_and_can_repeat(self):
        profile = {0: 10, 3: 40, 10: 80}
        for minute, repeat, expected in [
            (-1, False, 10), (2.999, False, 10), (3, False, 40),
            (10, False, 80), (100, False, 80), (10, True, 10),
            (13, True, 40),
        ]:
            with self.subTest(minute=minute, repeat=repeat):
                self.assertEqual(consumption.consumption_step(profile, minute, 5, repeat), expected)
        self.assertEqual(consumption.consumption_step({}, 10, 5, repeat=True), 5)
        self.assertEqual(consumption.consumption_step({0: 9}, 10, 5, repeat=True), 9)

    def test_all_supported_devices_are_zero_when_off_and_standby_when_idle(self):
        for kind, profile in consumption.consumption_profiles.items():
            with self.subTest(kind=kind):
                self.assertEqual(consumption.get_device_consumption("d", kind, NOW, {}, 0), 0)
                self.assertEqual(
                    consumption.get_device_consumption("d", kind, NOW, {}, 1, add_random_noise=False),
                    profile["standby"],
                )

    def test_cycle_accepts_current_and_legacy_storage_without_mutating_them(self):
        started = NOW - timedelta(minutes=1.5)
        entries = [
            {"start_time": started, "cycle_type": "Oven"},
            (started, "Oven"),
            [started, "Oven"],
        ]
        for entry in entries:
            with self.subTest(entry=entry):
                cycles = {"oven": entry}
                original = copy.deepcopy(cycles)
                actual = consumption.get_device_consumption(
                    "oven", "Oven", NOW, cycles, add_random_noise=False
                )
                self.assertAlmostEqual(actual, (942.8 + 995.3) / 2)
                self.assertEqual(cycles, original)

    def test_missing_cycle_start_uses_standby_instead_of_crashing(self):
        for entry in ({}, None, [], {"cycle_type": "Computer"}):
            with self.subTest(entry=entry):
                self.assertEqual(consumption.get_device_consumption("pc", "Computer", NOW, {"pc": entry}), 40)

    def test_future_cycle_start_clamps_to_first_reading(self):
        cycles = {"oven": (NOW + timedelta(minutes=5), "Oven")}
        self.assertEqual(consumption.get_device_consumption("oven", "Oven", NOW, cycles, add_random_noise=False), 942.8)

    def test_fridge_wraps_while_nonrepeating_appliances_hold_the_last_sample(self):
        for kind, entry in consumption.consumption_profiles.items():
            with self.subTest(kind=kind):
                duration = max(entry["profile"])
                cycles = {"d": (NOW, kind)}
                result = consumption.get_device_consumption(
                    "d", kind, NOW + timedelta(minutes=duration + 1), cycles, add_random_noise=False
                )
                expected = consumption.interpolated_consumption(entry["profile"], 1, 0) if kind == "Fridge" else entry["profile"][duration]
                self.assertAlmostEqual(result, expected)

    def test_invalid_llm_duration_metadata_falls_back_to_the_profile_duration(self):
        for duration in (None, "bad", -1, 0):
            with self.subTest(duration=duration):
                cycles = {"oven": {"start_time": NOW, "llm": {"duration_minutes": duration}}}
                self.assertEqual(
                    consumption.get_device_consumption("oven", "Oven", NOW + timedelta(minutes=100), cycles, add_random_noise=False),
                    947.7,
                )

    def test_noise_is_opt_in_for_deterministic_consumption(self):
        cycles = {"oven": (NOW, "Oven")}
        with patch.object(consumption, "add_noise", return_value=123) as noise:
            deterministic = consumption.get_device_consumption("oven", "Oven", NOW, cycles, add_random_noise=False)
            noise.assert_not_called()
            noisy = consumption.get_device_consumption("oven", "Oven", NOW, cycles)
        self.assertEqual(deterministic, 942.8)
        self.assertEqual(noisy, 123)
        noise.assert_called_once_with(942.8)

    def test_noise_cannot_produce_negative_power_and_scales_with_load(self):
        with patch("random.gauss", return_value=-100) as gaussian:
            self.assertEqual(consumption.add_noise(1000), 0)
        gaussian.assert_called_once_with(1000.0, 20.0)
        with patch("random.gauss", return_value=0) as gaussian:
            self.assertEqual(consumption.add_noise(1), 0)
        gaussian.assert_called_once_with(1.0, 3.0)


class PredictionRegressionTests(unittest.TestCase):
    def test_samples_cover_exact_horizon_without_overshooting(self):
        for horizon, step, offsets in [
            (120, 60, [0, 60, 120]),
            (125, 60, [0, 60, 120, 125]),
            (30, 60, [0, 30]),
            (60, 60, [0, 60]),
        ]:
            with self.subTest(horizon=horizon, step=step):
                samples = prediction.predict_device_consumption(
                    "oven", "Oven", NOW, {}, horizon_seconds=horizon, step_seconds=step
                )
                self.assertEqual([(item.timestamp - NOW).total_seconds() for item in samples], offsets)

    def test_nonpositive_horizon_or_step_has_no_samples(self):
        for horizon, step in [(0, 60), (-1, 60), (60, 0), (60, -1)]:
            with self.subTest(horizon=horizon, step=step):
                with patch.object(prediction, "get_device_consumption") as get_power:
                    result = prediction.predict_device_consumption("oven", "Oven", NOW, {}, horizon_seconds=horizon, step_seconds=step)
                self.assertEqual(result, [])
                get_power.assert_not_called()

    def test_forecast_interpolates_forward_deterministically_and_leaves_cycle_intact(self):
        cycles = {"oven": {"start_time": NOW, "cycle_type": "Oven"}}
        original = copy.deepcopy(cycles)
        with patch.object(consumption, "add_noise", side_effect=AssertionError("forecast used random noise")):
            first = prediction.predict_device_consumption("oven", "Oven", NOW, cycles, horizon_seconds=180, step_seconds=90)
            second = prediction.predict_device_consumption("oven", "Oven", NOW, cycles, horizon_seconds=180, step_seconds=90)
        self.assertEqual(first, second)
        self.assertEqual(cycles, original)
        self.assertEqual([sample.value for sample in first], [942.8, (942.8 + 995.3) / 2, 995.3])

    def test_off_device_forecast_stays_zero_even_during_an_active_cycle(self):
        samples = prediction.predict_device_consumption("oven", "Oven", NOW, {"oven": (NOW, "Oven")}, 0)
        self.assertTrue(samples)
        self.assertTrue(all(sample.value == 0 for sample in samples))

    def test_smart_meter_alias_uses_the_same_deterministic_device_forecast(self):
        cycles = {"oven": (NOW, "Oven")}
        expected = prediction.predict_device_consumption("oven", "Oven", NOW, cycles, horizon_seconds=95, step_seconds=30)
        actual = prediction.predict_smart_meter_for_associated_device("oven", "Oven", NOW, cycles, horizon_seconds=95, step_seconds=30)
        self.assertEqual(actual, expected)


class SensorAdapterRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.device = Device("oven", 0, 0, "Oven", 1000, 1, 0, 1000)
        self.devices = [self.device]
        self.state = HouseState(initial_context={"devices": self.devices, "current_datetime": NOW, "delta_seconds": 3, "heating_factor": 2})

    def test_temperature_adapter_resolves_omitted_arguments_from_runtime(self):
        thermometer = make_sensor("temp", "Temperature")
        with patch.object(sensor, "compute_temperature", return_value=22) as compute:
            result = sensor.TemperatureSensorAdapter().update(self.state, thermometer, render=False)
        self.assertEqual(result, ("temp", 22))
        compute.assert_called_once_with(thermometer, 2.0, 3.0, NOW, self.devices)

    def test_temperature_adapter_preserves_explicit_zero_and_empty_overrides(self):
        thermometer = make_sensor("temp", "Temperature")
        with patch.object(sensor, "compute_temperature", return_value=20) as compute:
            sensor.TemperatureSensorAdapter().update(self.state, thermometer, heating_factor=0, delta_seconds=0, current_datetime=NOW + timedelta(minutes=1), active_devices=[])
        compute.assert_called_once_with(thermometer, 0.0, 0.0, NOW + timedelta(minutes=1), [])
        self.assertEqual(self.state.runtime()["delta_seconds"], 3)
        self.assertIs(self.state.runtime()["devices"], self.devices)

    def test_smart_meter_adapter_resolves_runtime_and_uses_shared_cycle_store(self):
        meter = make_sensor()
        self.state.active_cycles()["oven"] = (NOW, "Oven")
        with patch.object(sensor, "compute_smartmeter_consumption", return_value=123) as compute:
            result = sensor.SmartMeterSensorAdapter().update(self.state, meter, render=False)
        self.assertEqual(result, ("meter", 123))
        compute.assert_called_once_with(meter, self.devices, 3.0, NOW, self.state.active_cycles())

    def test_smart_meter_explicit_empty_devices_and_zero_delta_override_runtime(self):
        meter = make_sensor()
        with patch.object(sensor, "compute_smartmeter_consumption", return_value=0) as compute:
            sensor.SmartMeterSensorAdapter().update(self.state, meter, devices_list=[], delta_seconds=0)
        compute.assert_called_once_with(meter, [], 0.0, NOW, self.state.active_cycles())

    def test_smart_meter_requires_a_device_collection(self):
        with self.assertRaisesRegex(RuntimeError, "devices"):
            sensor.SmartMeterSensorAdapter().update(HouseState(), make_sensor())


class SmartMeterAssociationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(sensor.reset_llm_runtime_state)
        sensor.reset_llm_runtime_state()
        self.device = Device("oven", 0, 0, "Oven", 1000, 1, 0, 1000)
        self.meter = make_sensor()
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.contexts.enter_context(patch.object(sensor, "devices_file", []))

    def test_unassociated_or_missing_device_produces_zero_without_fallback(self):
        for associated in (None, "missing"):
            with self.subTest(associated=associated):
                self.meter.associated_device = associated
                with patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=None), patch.object(sensor, "get_device_consumption") as fallback:
                    self.assertEqual(sensor.compute_smartmeter_consumption(self.meter, [], 1, NOW), 0)
                fallback.assert_not_called()

    def test_synthetic_fallback_uses_the_associated_runtime_device_and_active_cycles(self):
        cycles = {"oven": (NOW, "Oven")}
        with patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=None), patch.object(sensor, "get_device_consumption", return_value=150) as fallback:
            result = sensor.compute_smartmeter_consumption(self.meter, [self.device], 1, NOW, cycles)
        self.assertEqual(result, 150)
        fallback.assert_called_once_with("oven", "Oven", NOW, cycles, 1)

    def test_runtime_device_takes_precedence_over_stale_file_state(self):
        stale = Device("oven", 0, 0, "Oven", 1000, 0, 0, 1000)
        with patch.object(sensor, "devices_file", [stale]), patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=None), patch.object(sensor, "get_device_consumption", return_value=42):
            self.assertEqual(sensor.compute_smartmeter_consumption(self.meter, [self.device], 1, NOW), 42)

    def test_file_device_is_used_when_absent_from_runtime(self):
        with patch.object(sensor, "devices_file", [self.device]), patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=23):
            self.assertEqual(sensor.compute_smartmeter_consumption(self.meter, [], 1, NOW), 23)

    def test_off_device_does_not_enter_synthetic_cycle(self):
        self.device.state = 0
        with patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=None), patch.object(sensor, "get_device_consumption") as fallback:
            self.assertEqual(sensor.compute_smartmeter_consumption(self.meter, [self.device], 1, NOW), 0)
        fallback.assert_not_called()

    def test_llm_zero_is_a_valid_result_and_does_not_trigger_synthetic_fallback(self):
        for watts in (0, -3, 65):
            with self.subTest(watts=watts):
                with patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=watts), patch.object(sensor, "get_device_consumption") as fallback:
                    result = sensor.compute_smartmeter_consumption(self.meter, [self.device], 1, NOW)
                self.assertEqual(result, max(0, watts))
                fallback.assert_not_called()


class SmartMeterCsvRegressionTests(unittest.TestCase):
    fields = ["timestamp_iso", "device", "device_id", "ip", "power_W", "voltage_V", "current_A"]

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "smartmeter_fixture.csv"

    def write_rows(self, rows):
        write_csv(self.path, self.fields, rows)

    def test_csv_loader_filters_device_and_preserves_zero_and_missing_values(self):
        self.write_rows([
            ["2026-01-02 12:00:00.000", "oven", "Oven-1", "192.0.2.1", "0", "230", "0"],
            ["2026-01-02 12:01:00.000", "other", "PC", "192.0.2.2", "15", "", "bad"],
            ["", "oven", "Oven-1", "192.0.2.1", "20", "230", "1"],
        ])
        readings = smartmeter.load_csv(str(self.path), device="oven")
        self.assertEqual(list(readings), ["2026-01-02 12:00:00.000"])
        self.assertEqual(readings["2026-01-02 12:00:00.000"]["power"], 0)
        other = smartmeter.load_csv(str(self.path), device="other")
        self.assertIsNone(other["2026-01-02 12:01:00.000"]["current"])
        self.assertEqual(smartmeter.load_csv(str(self.path.with_name("missing.csv"))), {})

    def test_power_dataframe_preserves_zero_power_and_ignores_nonfinite_readings(self):
        self.write_rows([
            ["2026-01-02 12:00:00.000", "oven", "OVEN", "192.0.2.1", 0, 230, 0],
            ["2026-01-02 12:01:00.000", "oven", "OVEN", "192.0.2.1", 10, 230, 1],
            ["2026-01-02 12:01:20.000", "oven", "OVEN", "192.0.2.1", "inf", 230, 1],
            ["2026-01-02 12:01:40.000", "oven", "OVEN", "192.0.2.1", "nan", 230, 1],
        ])
        data = smartmeter.load_power_df(str(self.path))
        self.assertEqual(data["value"].tolist(), [0, 10])
        self.assertEqual(data.index[0], pd.Timestamp(NOW))

    def test_power_dataframe_respects_requested_resampling_aggregation(self):
        self.write_rows([
            ["2026-01-02 12:00:01.000", "oven", "OVEN", "192.0.2.1", 10, 230, 1],
            ["2026-01-02 12:00:20.000", "oven", "OVEN", "192.0.2.1", 20, 230, 1],
            ["2026-01-02 12:00:40.000", "oven", "OVEN", "192.0.2.1", 90, 230, 1],
        ])
        for aggregation, expected in [("median", 20), ("max", 90), ("mean", 40)]:
            with self.subTest(aggregation=aggregation):
                data = smartmeter.load_power_df(str(self.path), agg=aggregation)
                self.assertEqual(data["value"].tolist(), [expected])

    def test_power_dataframe_accepts_logger_timestamps_with_and_without_milliseconds(self):
        self.write_rows([
            ["2026-01-02 12:00:00", "oven", "OVEN", "192.0.2.1", 12, 230, 1],
            ["2026-01-02 12:01:00.000", "oven", "OVEN", "192.0.2.1", 13, 230, 1],
        ])
        self.assertEqual(smartmeter.load_power_df(str(self.path))["value"].tolist(), [12, 13])

    def test_all_invalid_timestamps_produce_an_empty_dataframe(self):
        self.write_rows([["invalid", "oven", "OVEN", "192.0.2.1", 12, 230, 1]])
        self.assertTrue(smartmeter.load_power_df(str(self.path)).empty)

    def test_id_and_ip_loaders_merge_matching_files_and_keep_zero_minutes(self):
        self.write_rows([
            ["2026-01-02 12:00:00", "oven", "Oven-1", "192.0.2.1", 0, 230, 0],
            ["2026-01-02 12:01:00", "other", "other", "192.0.2.2", 999, 230, 1],
        ])
        write_csv(self.path.with_name("smartmeter_second.csv"), self.fields, [
            ["2026-01-02 12:01:00", "oven", "OVEN_1", "192.0.2.1", 25, 230, 1],
            ["2026-01-02 12:01:30", "oven", "OVEN_1", "192.0.2.1", 30, 230, 1],
        ])
        by_id = smartmeter.load_power_by_device_id_any_csv("oven 1", logs_dir=self.directory.name)
        by_ip = smartmeter.load_power_by_ip_any_csv("192.0.2.1", logs_dir=self.directory.name)
        self.assertEqual(by_id["value"].tolist(), [0, 30])
        pd.testing.assert_frame_equal(by_id, by_ip)

    def test_csv_header_is_idempotent_and_repairs_missing_final_newline(self):
        smartmeter.csv_ensure_header(str(self.path))
        initial = self.path.read_text(encoding="utf-8")
        self.path.write_text(initial.rstrip("\n"), encoding="utf-8")
        smartmeter.csv_ensure_header(str(self.path))
        smartmeter.csv_ensure_header(str(self.path))
        with self.path.open(newline="", encoding="utf-8") as stream:
            self.assertEqual(list(csv.reader(stream)), [self.fields])

    def test_existing_empty_csv_gets_a_header_before_logging(self):
        self.path.touch()
        smartmeter.csv_ensure_header(str(self.path))
        with self.path.open(newline="", encoding="utf-8") as stream:
            self.assertEqual(list(csv.reader(stream)), [self.fields])


class SmartMeterNetworkBoundaryTests(unittest.TestCase):
    def test_gen2_falls_back_to_power_only_when_apower_is_missing(self):
        response = Mock()
        response.json.return_value = {"voltage": 231, "apower": None, "power": 45, "current": 0.2}
        with patch.object(smartmeter.requests, "get", return_value=response) as request:
            self.assertEqual(smartmeter._get_voltage_gen2("192.0.2.1", timeout=4, auth=("u", "p")), (231, 45, 0.2))
        request.assert_called_once_with("http://192.0.2.1/rpc/Switch.GetStatus?id=0", timeout=4, auth=("u", "p"))

    def test_gen1_supports_emeter_and_top_level_fallbacks(self):
        response = Mock()
        response.json.return_value = {"emeter": [{"apower": 45, "current": 0.2}], "voltage": 230}
        with patch.object(smartmeter.requests, "get", return_value=response):
            self.assertEqual(smartmeter._get_voltage_gen1("192.0.2.1"), (230, 45, 0.2))

    def test_device_name_discovery_uses_name_then_id_then_offline_fallback(self):
        for payload, expected in [({"name": " Kitchen ", "id": "id"}, "Kitchen"), ({"id": "shelly-id"}, "shelly-id"), ({}, "Shelly_192-0-2-1")]:
            with self.subTest(payload=payload):
                response = Mock()
                response.json.return_value = payload
                with patch.object(smartmeter.requests, "get", return_value=response):
                    self.assertEqual(smartmeter.get_device_name_from_shelly("192.0.2.1"), expected)
        with patch.object(smartmeter.requests, "get", side_effect=smartmeter.requests.Timeout):
            self.assertEqual(smartmeter.get_device_name_from_shelly("192.0.2.1"), "Shelly_192-0-2-1")

    def test_single_poll_falls_back_to_gen1_and_derives_voltage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meter.csv"
            logger = smartmeter.SmartMeterLogger("oven", "192.0.2.1", csv_path=str(path), device_id="OVEN", interval=1)
            with patch.object(smartmeter, "_get_voltage_gen2", side_effect=smartmeter.requests.Timeout), patch.object(smartmeter, "_get_voltage_gen1", return_value=(None, "46", "0.2")), patch.object(smartmeter.time, "sleep", side_effect=lambda _: logger._stop_event.set()), patch.dict(smartmeter.CSV_WRITE_LOCKS, clear=True):
                logger._run_loop()
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1)
        self.assertEqual((float(rows[0]["power_W"]), float(rows[0]["voltage_V"]), float(rows[0]["current_A"])), (46, 230, 0.2))

    def test_registry_reuses_logger_refreshes_settings_and_stops_once(self):
        existing = Mock()
        existing.csv_path = "existing.csv"
        with patch.dict(smartmeter.LOGGERS, {"OVEN": existing}, clear=True):
            result = smartmeter.start_logger("new name", "192.0.2.2", interval=3, auth=("u", "p"), device_id="OVEN")
            self.assertIs(result, existing)
            self.assertEqual((existing.device_name, existing.shelly_ip, existing.interval, existing.auth), ("new name", "192.0.2.2", 3, ("u", "p")))
            existing.start.assert_called_once_with()
            smartmeter.stop_logger("OVEN")
            smartmeter.stop_logger("OVEN")
            self.assertEqual(smartmeter.LOGGERS, {})
        existing.stop.assert_called_once_with()

    def test_start_is_idempotent_and_stop_signals_and_joins_the_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = smartmeter.SmartMeterLogger("oven", "192.0.2.1", csv_path=str(Path(directory) / "meter.csv"))
            worker = Mock()
            worker.is_alive.return_value = True
            logger._stop_event.set()
            with patch.object(smartmeter.threading, "Thread", return_value=worker) as thread:
                logger.start()
                self.assertFalse(logger._stop_event.is_set())
                logger.start()
                thread.assert_called_once_with(target=logger._run_loop, daemon=True, name="SMLogger-oven")
                worker.start.assert_called_once_with()
                logger.stop()
            self.assertTrue(logger._stop_event.is_set())
            worker.join.assert_called_once_with(timeout=2)


class PhysicalSensorBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.contexts.enter_context(patch.object(real_sensors, "DEVICES_DIR", self.directory))
        self.contexts.enter_context(patch.object(real_sensors, "_USE_CPY_DHT", False))
        self.contexts.enter_context(patch.object(real_sensors, "_USE_ADA_DHT", False))
        self.contexts.enter_context(patch.object(real_sensors, "_USE_GPIOZERO", False))
        self.contexts.enter_context(patch.object(real_sensors, "_USE_RPI_GPIO", False))

    def test_reading_without_a_gpio_backend_is_unknown(self):
        logger = real_sensors.GPIOValueLogger("pir", "pir", 17)
        self.assertIsNone(logger._read_once())
        dht = real_sensors.DHTLogger("temp", 4)
        with self.assertLogs(real_sensors.logger, level="WARNING"):
            self.assertEqual(dht._read_once(), (None, None))

    def test_csv_headers_recover_empty_files_and_missing_final_newlines(self):
        cases = [
            (real_sensors.dht_csv_ensure_header, ["timestamp_iso", "label", "gpio", "temp_C", "hum_%"]),
            (real_sensors.gpio_csv_ensure_header, ["timestamp_iso", "label", "kind", "gpio", "value"]),
        ]
        for ensure_header, fields in cases:
            for content in ("", ",".join(fields)):
                with self.subTest(ensure_header=ensure_header.__name__, content=content):
                    path = Path(self.directory) / f"{ensure_header.__name__}.csv"
                    path.write_text(content, encoding="utf-8")
                    ensure_header(str(path))
                    ensure_header(str(path))
                    with path.open(newline="", encoding="utf-8") as stream:
                        self.assertEqual(list(csv.reader(stream)), [fields])
                    self.assertTrue(path.read_bytes().endswith((b"\r", b"\n")))

    def test_gpio_read_normalizes_binary_values_and_active_low_polarity(self):
        for raw in (0, 1):
            for active_low in (False, True):
                with self.subTest(raw=raw, active_low=active_low):
                    logger = real_sensors.GPIOValueLogger("pir", "pir", 17, active_low=active_low)
                    logger._device = SimpleNamespace(value=raw)
                    self.assertEqual(logger._read_once(), float(1 - raw if active_low else raw))

    def test_gpio_read_errors_are_unknown_instead_of_false_motion(self):
        logger = real_sensors.GPIOValueLogger("pir", "pir", 17)
        logger._device = Mock()
        type(logger._device).value = PropertyMock(side_effect=OSError("device disconnected"))
        with self.assertLogs(real_sensors.logger, level="WARNING"):
            self.assertIsNone(logger._read_once())

    def test_gpio_initialization_falls_back_when_gpiozero_is_unavailable(self):
        logger = real_sensors.GPIOValueLogger("door", "switch", 17, pull_up=True)
        gpio = Mock()
        with patch.object(real_sensors, "_USE_GPIOZERO", True), patch.object(real_sensors, "DigitalInputDevice", side_effect=RuntimeError("unsupported")), patch.object(real_sensors, "_USE_RPI_GPIO", True), patch.object(real_sensors, "RPI_GPIO", gpio), self.assertLogs(real_sensors.logger, level="WARNING"):
            logger._init_gpio()
            gpio.input.return_value = 1
            self.assertEqual(logger._read_once(), 1)
        gpio.setmode.assert_called_once_with(gpio.BCM)
        gpio.setup.assert_called_once_with(17, gpio.IN, pull_up_down=gpio.PUD_UP)

    def test_dht_legacy_backend_returns_temperature_then_humidity(self):
        logger = real_sensors.DHTLogger("temp", 4)
        backend = Mock()
        backend.read_retry.return_value = ("45.5", "21.25")
        with patch.object(real_sensors, "_USE_ADA_DHT", True), patch.object(real_sensors, "ADA_DHT", backend):
            self.assertEqual(logger._read_once(), (21.25, 45.5))
        backend.read_retry.assert_called_once_with(backend.DHT22, 4)

    def test_dht_circuitpython_transient_error_yields_no_reading(self):
        logger = real_sensors.DHTLogger("temp", 4)
        logger._dht_device = Mock()
        type(logger._dht_device).temperature = PropertyMock(side_effect=RuntimeError("checksum"))
        with patch.object(real_sensors, "_USE_CPY_DHT", True):
            self.assertEqual(logger._read_once(), (None, None))

    def test_dht_polling_skips_missing_and_nonfinite_measurements(self):
        for reading, expected_count in [((None, None), 0), ((math.nan, math.inf), 0), ((0, 0), 1), ((21.5, None), 1)]:
            with self.subTest(reading=reading):
                logger = real_sensors.DHTLogger(f"temp_{expected_count}_{reading[0]}", 4, interval=1)
                with patch.object(logger, "_read_once", return_value=reading), patch.object(real_sensors.time, "sleep", side_effect=lambda _: logger._stop.set()):
                    logger._run()
                with Path(logger.csv_path).open(newline="", encoding="utf-8") as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), expected_count)
                if expected_count:
                    self.assertEqual(float(rows[0]["temp_C"]), reading[0])

    def test_dht_csv_loader_selects_gpio_across_files_and_uses_minute_median(self):
        fields = ["timestamp_iso", "label", "gpio", "temp_C", "hum_%"]
        write_csv(Path(self.directory) / "dht_a.csv", fields, [
            ["2026-01-02 12:00:00", "a", 4, 20, 40],
            ["2026-01-02 12:00:30", "a", 4, 24, 40],
            ["2026-01-02 12:00:45", "a", 17, 99, 40],
            ["2026-01-02 12:01:00", "a", 4, "bad", 40],
        ])
        write_csv(Path(self.directory) / "dht_b.csv", fields, [["2026-01-02 12:01:00", "b", 4, 23, 40]])
        result = real_sensors.load_temp_by_gpio_any_csv(4, logs_dir=self.directory)
        self.assertEqual(result["value"].tolist(), [22, 23])
        self.assertTrue(real_sensors.load_temp_by_gpio_any_csv(27, logs_dir=self.directory).empty)

    def test_gpio_csv_loaders_filter_pin_and_label_and_ignore_invalid_samples(self):
        fields = ["timestamp_iso", "label", "kind", "gpio", "value"]
        write_csv(Path(self.directory) / "pir_hall.csv", fields, [
            ["2026-01-02 12:00:00", "hall", "pir", 17, 0],
            ["2026-01-02 12:01:00", "hall", "pir", 17, 1],
            ["invalid", "hall", "pir", 17, 1],
            ["2026-01-02 12:02:00", "hall", "pir", 17, "inf"],
        ])
        write_csv(Path(self.directory) / "pir_other.csv", fields, [["2026-01-02 12:00:00", "other", "pir", 27, 1]])
        by_label = real_sensors.load_value_by_label_any_csv("hall", "pir", logs_dir=self.directory)
        by_gpio = real_sensors.load_value_by_gpio_any_csv(17, "pir", logs_dir=self.directory)
        self.assertEqual(by_label["value"].tolist(), [0, 1])
        pd.testing.assert_frame_equal(by_label, by_gpio)

    def test_binding_dispatches_dht_and_gpio_to_the_correct_backend(self):
        with patch.object(real_sensors, "start_dht_logger") as dht, patch.object(real_sensors, "start_gpio_logger") as gpio:
            self.assertIs(real_sensors.start_bound_logger("temp", "DHT", 4, 3), dht.return_value)
            self.assertIs(real_sensors.start_bound_logger("pir", "pir", 17, 5, pull_up=True, active_low=True), gpio.return_value)
        dht.assert_called_once_with(sensor_label="temp", gpio=4, interval=3)
        gpio.assert_called_once_with(sensor_label="pir", kind="pir", gpio=17, interval=5, pull_up=True, active_low=True)

    def test_gpio_registry_recreates_logger_when_pin_or_pull_resistor_changes(self):
        original = Mock(gpio=17, pull_up=False)
        replacement = Mock()
        with patch.dict(real_sensors.GPIO_LOGGERS, {"pir:hall": original}, clear=True), patch.object(real_sensors, "GPIOValueLogger", return_value=replacement) as factory:
            result = real_sensors.start_gpio_logger("hall", "pir", 27, interval=0, pull_up=True, active_low=True)
            self.assertIs(result, replacement)
            self.assertIs(real_sensors.GPIO_LOGGERS["pir:hall"], replacement)
        original.stop.assert_called_once_with()
        replacement.start.assert_called_once_with()
        factory.assert_called_once_with(sensor_label="hall", kind="pir", gpio_bcm=27, interval=1, pull_up=True, active_low=True)

    def test_gpio_registry_updates_polarity_and_interval_without_recreating_backend(self):
        existing = Mock(gpio=17, pull_up=False)
        with patch.dict(real_sensors.GPIO_LOGGERS, {"pir:hall": existing}, clear=True), patch.object(real_sensors, "GPIOValueLogger") as factory:
            self.assertIs(real_sensors.start_gpio_logger("hall", "pir", 17, interval=7, active_low=True), existing)
        self.assertEqual(existing.interval, 7)
        self.assertTrue(existing.active_low)
        factory.assert_not_called()
        existing.stop.assert_not_called()
        existing.start.assert_called_once_with()

    def test_dht_registry_reuses_pin_and_recreates_when_pin_changes(self):
        existing = Mock(gpio=4)
        replacement = Mock()
        with patch.dict(real_sensors.DHT_LOGGERS, {"temp": existing}, clear=True), patch.object(real_sensors, "DHTLogger", return_value=replacement) as factory:
            self.assertIs(real_sensors.start_dht_logger("temp", 4, interval=0), existing)
            self.assertEqual(existing.interval, 1)
            factory.assert_not_called()
            self.assertIs(real_sensors.start_dht_logger("temp", 17, interval=5), replacement)
            self.assertIs(real_sensors.DHT_LOGGERS["temp"], replacement)
        existing.stop.assert_called_once_with()
        factory.assert_called_once_with(sensor_label="temp", gpio_bcm=17, interval=5)
        replacement.start.assert_called_once_with()

    def test_gpio_worker_starts_once_and_stop_closes_the_backend(self):
        logger = real_sensors.GPIOValueLogger("pir", "pir", 17)
        backend, worker = Mock(), Mock()
        worker.is_alive.return_value = True
        logger._device = backend
        with patch.object(logger, "_init_gpio") as initialize, patch.object(real_sensors.threading, "Thread", return_value=worker) as thread:
            logger.start()
            logger.start()
            logger.stop()
        initialize.assert_called_once_with()
        thread.assert_called_once_with(target=logger._run, daemon=True, name="PIR-pir")
        worker.start.assert_called_once_with()
        worker.join.assert_called_once_with(timeout=2)
        backend.close.assert_called_once_with()
        self.assertTrue(logger._stop.is_set())

    def test_dht_worker_starts_once_and_stop_releases_the_backend(self):
        logger = real_sensors.DHTLogger("temp", 4)
        backend, worker = Mock(), Mock()
        worker.is_alive.return_value = True
        logger._dht_device = backend
        with patch.object(real_sensors.threading, "Thread", return_value=worker) as thread:
            logger.start()
            logger.start()
            logger.stop()
        thread.assert_called_once_with(target=logger._run, daemon=True, name="DHT-temp")
        worker.start.assert_called_once_with()
        worker.join.assert_called_once_with(timeout=2)
        backend.exit.assert_called_once_with()
        self.assertTrue(logger._stop.is_set())

    def test_stop_all_releases_both_logger_registries_and_is_idempotent(self):
        dht, gpio = Mock(), Mock()
        with patch.dict(real_sensors.DHT_LOGGERS, {"temp": dht}, clear=True), patch.dict(real_sensors.GPIO_LOGGERS, {"pir:hall": gpio}, clear=True):
            real_sensors.stop_all()
            real_sensors.stop_all()
            self.assertEqual(real_sensors.DHT_LOGGERS, {})
            self.assertEqual(real_sensors.GPIO_LOGGERS, {})
        dht.stop.assert_called_once_with()
        gpio.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
