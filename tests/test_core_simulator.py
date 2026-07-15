import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import pandas as pd

from consumption_profiles import (
    get_device_consumption,
    interpolated_consumption,
)
from house_state import HouseState
from models import Device, Sensor
from prediction import predict_device_consumption
import sensor as sensor_module
import graph as graph_module
from log import log_llm_cycle_event
from sensor import PIRSensorAdapter, SwitchSensorAdapter, WeightSensorAdapter
from sim import _append_unique_sample, _consumption_to_bin_state, append_unique_binary
from timer import TimerApp


def make_sensor(name, sensor_type, state=0):
    return Sensor(name, 0, 0, sensor_type, 0, 1, 1, state)


class HouseStateTests(unittest.TestCase):
    def test_runtime_alias_and_clear_are_consistent(self):
        state = HouseState(initial_context={"devices": ["old"]})
        self.assertIs(state.context, state.runtime())

        state.sensor_states()["pir"] = {"state": [1]}
        state.clear_runtime_state()

        self.assertEqual(state.sensor_states(), {})
        self.assertEqual(state.runtime(), {})
        self.assertIs(state.context, state.runtime())

    def test_runtime_view_does_not_mutate_stored_runtime(self):
        state = HouseState(initial_context={"delta_seconds": 1})
        view = state.runtime_view(delta_seconds=5)

        self.assertEqual(view["delta_seconds"], 5)
        self.assertEqual(state.runtime()["delta_seconds"], 1)


class SensorAdapterTests(unittest.TestCase):
    def test_pir_honors_explicit_state_and_toggles_when_omitted(self):
        adapter = PIRSensorAdapter()
        state = HouseState()
        sensor = make_sensor("pir", "PIR", state=0)

        self.assertEqual(adapter.update(state, sensor, 0)[1], 0.0)
        self.assertEqual(adapter.update(state, sensor, 1)[1], 1.0)
        self.assertEqual(adapter.update(state, sensor)[1], 1.0)
        sensor.state = 1
        self.assertEqual(adapter.update(state, sensor)[1], 0.0)

    def test_switch_and_weight_normalization(self):
        state = HouseState()
        switch = make_sensor("door", "Switch")
        weight = make_sensor("chair", "Weight")

        adapter = SwitchSensorAdapter()
        self.assertEqual(adapter.update(state, switch, "open")[1], 1.0)
        self.assertEqual(adapter.update(state, switch, "close")[1], 0.0)
        self.assertEqual(adapter.update(state, switch, None)[1], 0.0)
        self.assertEqual(WeightSensorAdapter().update(state, weight, 1)[1], 1.0)


class LLMProfileResolutionTests(unittest.TestCase):
    def test_fridge_alias_selects_historical_refrigerator_profile(self):
        catalog = {
            "by_source": {
                "refrigerator:smartmeter_sm_re": {
                    "appliance_key": "refrigerator",
                    "source_name": "smartmeter_sm_re",
                },
            },
        }

        self.assertEqual(
            sensor_module._device_type_to_appliance_key("Fridge"),
            "refrigerator",
        )
        profile = sensor_module._profile_for_sensor(
            catalog,
            "refrigerator",
            "sm_fr",
            "fr",
        )
        self.assertIsNotNone(profile)
        self.assertEqual(profile["source_name"], "smartmeter_sm_re")

    def test_output_folder_resolution_uses_best_k_variant(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            selected_output = base / "Refrigerator" / "smartmeter_sm_re_output_best_k"
            selected_output.mkdir(parents=True)

            with patch.object(
                sensor_module,
                "LLM_PROFILE_CATALOG_PATH",
                base / "llm_smartmeter_profiles.json",
            ), patch.object(
                sensor_module,
                "LLM_PROFILE_CATALOG_LEGACY_PATH",
                base / "legacy_profiles.json",
            ):
                resolved = sensor_module._resolve_llm_catalog_path(
                    "Refrigerator/smartmeter_sm_re_output"
                )

            self.assertEqual(resolved, selected_output)

    def test_graph_recognizes_fridge_device_type(self):
        self.assertEqual(
            graph_module._appliance_key_from_device_type("Fridge"),
            "refrigerator",
        )


class SensorBufferTests(unittest.TestCase):
    def test_binary_buffer_preserves_edges_with_same_timestamp(self):
        buffer = {}
        append_unique_binary(buffer, "12:00", 0, "PIR")
        append_unique_binary(buffer, "12:00", 1, "PIR")
        append_unique_binary(buffer, "12:00", 1, "PIR")

        self.assertEqual(buffer["time"], ["12:00", "12:00"])
        self.assertEqual(buffer["state"], [0, 1])
        self.assertEqual(buffer["type"], "PIR")

    def test_sample_buffer_overwrites_same_timestamp(self):
        buffer = {"time": [], "state": [], "consumption": []}
        _append_unique_sample(buffer, "12:00", 0, 0)
        _append_unique_sample(buffer, "12:00", 1, 25.125)

        self.assertEqual(buffer["time"], ["12:00"])
        self.assertEqual(buffer["state"], [1.0])
        self.assertEqual(buffer["consumption"], [25.12])
        self.assertEqual(_consumption_to_bin_state(1.0), 0)
        self.assertEqual(_consumption_to_bin_state(1.01), 1)


class TimerLifecycleTests(unittest.TestCase):
    class FakeButton:
        def __init__(self):
            self.text = ""

        def config(self, **kwargs):
            self.text = kwargs.get("text", self.text)

    class FakeEntry:
        def get(self):
            return "12:00"

        def delete(self, *_args):
            pass

        def insert(self, *_args):
            pass

    class FakeLabel(FakeButton):
        pass

    def make_timer(self, events):
        timer = TimerApp.__new__(TimerApp)
        timer.start_callback = lambda: events.append("start")
        timer.stop_callback = None
        timer.pause_callback = lambda: events.append("pause")
        timer.reset_callback = lambda: events.append("reset")
        timer.is_running = False
        timer.start_time = None
        timer.elapsed_time = pd.Timedelta(0).to_pytimedelta()
        timer.simulated_start_time = None
        timer.current_date = "2026-06-15"
        timer.last_update = None
        timer.start_stop_button = self.FakeButton()
        timer.start_hour_entry = self.FakeEntry()
        timer.label = self.FakeLabel()
        return timer

    def test_stop_pauses_and_start_resumes_without_reset_callback(self):
        events = []
        timer = self.make_timer(events)

        timer.start_stop()
        timer.start_stop()
        timer.start_stop()

        self.assertEqual(events, ["start", "pause", "start"])
        self.assertTrue(timer.is_running)
        self.assertIsNotNone(timer.start_time)

        timer.reset()
        self.assertEqual(events, ["start", "pause", "start", "reset"])
        self.assertIsNone(timer.start_time)


class ConsumptionAndPredictionTests(unittest.TestCase):
    def test_interpolation_and_device_states(self):
        self.assertEqual(interpolated_consumption({0: 0, 10: 100}, 5, 0), 50)
        now = datetime(2026, 1, 1, 12, 0)
        active = {"oven": {"start_time": now, "cycle_type": "Oven"}}

        self.assertEqual(
            get_device_consumption("oven", "Oven", now, active, 0, add_random_noise=False),
            0.0,
        )
        self.assertEqual(
            get_device_consumption("unknown", "Unknown", now, {}, 1, add_random_noise=False),
            0.0,
        )
        self.assertEqual(
            get_device_consumption("oven", "Oven", now, active, 1, add_random_noise=False),
            942.8,
        )

    def test_prediction_is_deterministic_and_includes_horizon_endpoint(self):
        now = datetime(2026, 1, 1, 12, 0)
        active = {"oven": {"start_time": now, "cycle_type": "Oven"}}
        samples = predict_device_consumption(
            "oven",
            "Oven",
            now,
            active,
            horizon_seconds=120,
            step_seconds=60,
        )

        self.assertEqual(len(samples), 3)
        self.assertEqual(samples[0].timestamp, now)
        self.assertEqual(samples[-1].timestamp, datetime(2026, 1, 1, 12, 2))
        self.assertEqual(samples[0].value, 942.8)

    def test_washing_machine_turns_off_when_llm_cycle_finishes(self):
        started = datetime(2026, 1, 1, 12, 0)
        now = datetime(2026, 1, 1, 12, 10)
        washer = Device(
            "washer",
            0,
            0,
            "Washing_Machine",
            500,
            1,
            300,
            500,
            current_consumption=200,
        )
        smart_meter = Sensor(
            "sm_washer",
            0,
            0,
            "Smart Meter",
            0,
            5000,
            1,
            0,
            associated_device="washer",
        )
        active_cycles = {
            "washer": {
                "start_time": started,
                "cycle_type": "Washing_Machine",
            }
        }
        sensor_module.reset_llm_runtime_state()
        sensor_module.LLM_SENSOR_ON_START[smart_meter.name] = started
        sensor_module.LLM_SENSOR_ACTIVE_CYCLE_ID[smart_meter.name] = 7

        with (
            patch.object(sensor_module, "_load_llm_profile_catalog", return_value={"washing_machine": {}}),
            patch.object(sensor_module, "_profile_for_sensor", return_value={}),
            patch.object(sensor_module, "_duration_minutes_for_cycle_id", return_value=10.0),
        ):
            consumption = sensor_module.compute_smartmeter_consumption(
                smart_meter,
                [washer],
                1,
                now,
                active_cycles,
            )

        self.assertEqual(consumption, 0.0)
        self.assertEqual(washer.state, 0)
        self.assertEqual(washer.current_consumption, 0.0)
        self.assertNotIn("washer", active_cycles)
        self.assertNotIn(smart_meter.name, sensor_module.LLM_SENSOR_ON_START)

    def test_refrigerator_turns_off_when_llm_cycle_finishes(self):
        started = datetime(2026, 1, 1, 12, 0)
        now = datetime(2026, 1, 1, 12, 30)
        refrigerator = Device("fr", 0, 0, "Fridge", 150, 1, 50, 150)
        smart_meter = Sensor(
            "sm_fr",
            0,
            0,
            "Smart Meter",
            0,
            5000,
            1,
            0,
            associated_device="fr",
        )
        active_cycles = {
            "fr": {
                "start_time": started,
                "cycle_type": "Fridge",
            }
        }
        sensor_module.reset_llm_runtime_state()
        sensor_module.LLM_SENSOR_ON_START[smart_meter.name] = started
        sensor_module.LLM_SENSOR_ACTIVE_CYCLE_ID[smart_meter.name] = 3758

        with (
            patch.object(sensor_module, "_load_llm_profile_catalog", return_value={"refrigerator": {}}),
            patch.object(sensor_module, "_profile_for_sensor", return_value={}),
            patch.object(sensor_module, "_duration_minutes_for_cycle_id", return_value=30.0),
        ):
            consumption = sensor_module.compute_smartmeter_consumption(
                smart_meter,
                [refrigerator],
                1,
                now,
                active_cycles,
            )

        self.assertEqual(consumption, 0.0)
        self.assertEqual(refrigerator.state, 0)
        self.assertEqual(refrigerator.current_consumption, 0.0)
        self.assertNotIn("fr", active_cycles)
        self.assertNotIn(smart_meter.name, sensor_module.LLM_SENSOR_ON_START)

    def test_computer_moves_to_next_case_while_it_remains_on(self):
        started = datetime(2026, 1, 1, 12, 0)
        now = datetime(2026, 1, 1, 12, 11)
        sensor_name = "sm_pc"
        sensor_module.reset_llm_runtime_state()
        sensor_module.LLM_SENSOR_ON_START[sensor_name] = started
        sensor_module.LLM_SENSOR_ACTIVE_CYCLE_ID[sensor_name] = 10

        def prime_next(sensor_name_arg, _dev_type, start_dt, **_kwargs):
            sensor_module.LLM_SENSOR_ON_START[sensor_name_arg] = start_dt
            sensor_module.LLM_SENSOR_ACTIVE_CYCLE_ID[sensor_name_arg] = 11
            return {"cycle_id": 11}

        def cycle_duration(_profile, cycle_id):
            return 10.0 if int(cycle_id) == 10 else 20.0

        with (
            patch.object(sensor_module, "_load_llm_profile_catalog", return_value={"computer": {}}),
            patch.object(sensor_module, "_profile_for_sensor", return_value={}),
            patch.object(sensor_module, "_duration_minutes_for_cycle_id", side_effect=cycle_duration),
            patch.object(sensor_module, "prime_llm_cycle_for_sensor", side_effect=prime_next) as prime_mock,
            patch.object(
                sensor_module,
                "_load_llm_cycle_curve_for_cycle_id",
                return_value=([0.0, 20.0], [200.0, 400.0]),
            ),
        ):
            consumption = sensor_module._get_llm_smartmeter_consumption(
                sensor_name,
                "Computer",
                1,
                now,
                associated_device="pc",
            )

        self.assertAlmostEqual(consumption, 210.0)
        self.assertEqual(sensor_module.LLM_SENSOR_ACTIVE_CYCLE_ID[sensor_name], 11)
        self.assertEqual(sensor_module.LLM_SENSOR_ON_START[sensor_name], datetime(2026, 1, 1, 12, 10))
        prime_mock.assert_called_once()

    def test_washing_machine_uses_distinct_cases_when_weekday_has_one_case(self):
        cases = pd.DataFrame(
            [
                {
                    "cycle_id": 108,
                    "cluster": 1,
                    "start_time": pd.Timestamp("2026-06-15 08:00"),
                    "end_time": pd.Timestamp("2026-06-15 09:00"),
                    "weekday": 0,
                },
                {
                    "cycle_id": 7,
                    "cluster": 2,
                    "start_time": pd.Timestamp("2026-06-09 08:00"),
                    "end_time": pd.Timestamp("2026-06-09 09:00"),
                    "weekday": 1,
                },
                {
                    "cycle_id": 23,
                    "cluster": 2,
                    "start_time": pd.Timestamp("2026-06-16 08:00"),
                    "end_time": pd.Timestamp("2026-06-16 09:00"),
                    "weekday": 1,
                },
            ]
        )
        profile = {"selected_cluster": 2, "dominant_cluster": 2}
        sensor_module.reset_llm_runtime_state()

        with patch.object(sensor_module, "_load_llm_cluster_cases", return_value=cases):
            first = sensor_module._select_washing_machine_cycle_id(
                profile, "sm_washer", datetime(2026, 6, 15, 20, 0)
            )
            second = sensor_module._select_washing_machine_cycle_id(
                profile, "sm_washer", datetime(2026, 6, 15, 22, 0)
            )

        self.assertEqual(first, 108)
        self.assertEqual(second, 7)

    def test_selection_policy_replaces_long_coffee_machine_case(self):
        cases = pd.DataFrame(
            [
                {
                    "cycle_id": 160,
                    "cluster": 2,
                    "start_time": pd.Timestamp("2026-01-01 08:00"),
                    "end_time": pd.Timestamp("2026-01-01 12:00"),
                    "duration_minutes": 240.0,
                    "max_power": 1500.0,
                    "mean_power": 220.0,
                    "energy_kwh": 0.9,
                    "time_of_peak_norm": 0.1,
                },
                {
                    "cycle_id": 10,
                    "cluster": 8,
                    "start_time": pd.Timestamp("2026-01-02 08:00"),
                    "end_time": pd.Timestamp("2026-01-02 08:04"),
                    "duration_minutes": 4.0,
                    "max_power": 1400.0,
                    "mean_power": 1200.0,
                    "energy_kwh": 0.08,
                    "time_of_peak_norm": 0.4,
                },
                {
                    "cycle_id": 11,
                    "cluster": 8,
                    "start_time": pd.Timestamp("2026-01-02 09:00"),
                    "end_time": pd.Timestamp("2026-01-02 09:05"),
                    "duration_minutes": 5.0,
                    "max_power": 1420.0,
                    "mean_power": 1210.0,
                    "energy_kwh": 0.09,
                    "time_of_peak_norm": 0.45,
                },
                {
                    "cycle_id": 12,
                    "cluster": 8,
                    "start_time": pd.Timestamp("2026-01-02 10:00"),
                    "end_time": pd.Timestamp("2026-01-02 10:06"),
                    "duration_minutes": 6.0,
                    "max_power": 1440.0,
                    "mean_power": 1220.0,
                    "energy_kwh": 0.10,
                    "time_of_peak_norm": 0.5,
                },
            ]
        )
        params = {
            "selection": {
                "max_duration_minutes": 15,
                "min_peak_watts": 500,
                "min_mean_watts": 400,
            }
        }

        with (
            patch.object(sensor_module, "_load_llm_default_params", return_value=params),
            patch.object(sensor_module, "_load_llm_cluster_cases", return_value=cases),
        ):
            sensor_module.reset_llm_runtime_state()
            selected = sensor_module._select_llm_profile_cycle_id(
                {"selected_cycle_id": 160},
                "coffee_machine",
                "sm_cf_invalid",
            )
            already_valid = sensor_module._select_llm_profile_cycle_id(
                {"selected_cycle_id": 11},
                "coffee_machine",
                "sm_cf_valid",
            )
            rotated_first = sensor_module._select_llm_profile_cycle_id(
                {"selected_cycle_id": 11},
                "coffee_machine",
                "sm_cf_rotation",
            )
            rotated_second = sensor_module._select_llm_profile_cycle_id(
                {"selected_cycle_id": 11},
                "coffee_machine",
                "sm_cf_rotation",
            )
            rotated_third = sensor_module._select_llm_profile_cycle_id(
                {"selected_cycle_id": 11},
                "coffee_machine",
                "sm_cf_rotation",
            )

        self.assertEqual(selected, 11)
        self.assertEqual(already_valid, 11)
        self.assertEqual([rotated_first, rotated_second, rotated_third], [11, 12, 10])

    def test_realtime_mode_without_binding_uses_generated_completion(self):
        profile = {
            "source_name": "washer",
            "selected_cluster": 2,
            "completion_reference_cycle_id": 14,
            "completion_reference_cluster": 1,
        }
        sensor_module.reset_llm_runtime_state()
        sensor_module.set_llm_smartmeter_mode("realtime_dt")

        with (
            patch.object(
                sensor_module,
                "_load_llm_profile_catalog",
                return_value={"washing_machine": profile},
            ),
            patch.object(sensor_module, "_profile_for_sensor", return_value=profile),
            patch.object(sensor_module, "is_smartmeter_bound_to_real_device", return_value=False),
            patch.object(
                sensor_module,
                "_load_llm_completion_curve",
                return_value=([0.0, 4.0, 10.0], [100.0, 200.0, 0.0]),
            ),
            patch.object(sensor_module, "_cluster_for_cycle_id", return_value=1),
        ):
            selected = sensor_module.prime_llm_cycle_for_sensor(
                "sm_washer",
                "Washing_Machine",
                datetime(2026, 6, 15, 20, 0),
                associated_device="washer",
                force_new_prediction=True,
            )

        self.assertEqual(selected["cycle_id"], 14)
        self.assertIn("sm_washer", sensor_module.LLM_SENSOR_ACTIVE_COMPLETION)
        self.assertEqual(
            sensor_module.get_llm_generation_events("sm_washer")[0]["event_type"],
            "completion_match",
        )
        self.assertEqual(sensor_module.get_llm_used_cases("sm_washer"), [])
        sensor_module.set_llm_smartmeter_mode("simulation")

    def test_llm_case_history_is_reconstructed_from_interaction_csv(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "interactions.csv"
            house_state = HouseState()
            interaction_state = house_state.interaction_log_state()
            interaction_state["interaction_file"] = path.open(
                "w", newline="", encoding="utf-8"
            )
            writer = csv.writer(interaction_state["interaction_file"])
            writer.writerow(
                [
                    "timestamp_sim",
                    "event_type",
                    "subject",
                    "name",
                    "x",
                    "y",
                    "value",
                    "extra",
                ]
            )
            for index, cycle_id in enumerate((108, 1, 2, 4, 7)):
                log_llm_cycle_event(
                    house_state,
                    f"2026-06-15 {20 + index:02d}:00",
                    "sm_washer",
                    {
                        "cycle_id": cycle_id,
                        "cluster": 2,
                        "duration_minutes": 60,
                        "case_type": "normal",
                        "event_type": "normal",
                    },
                )
            interaction_state["interaction_file"].close()
            interaction_state["interaction_file"] = None

            with patch.object(
                graph_module, "_latest_interactions_csv", return_value=str(path)
            ):
                normal, completed = graph_module._logged_llm_cases("sm_washer")

        self.assertEqual([item["cycle_id"] for item in normal], [108, 1, 2, 4, 7])
        self.assertEqual(completed, [])


if __name__ == "__main__":
    unittest.main()
