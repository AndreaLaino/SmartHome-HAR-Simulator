"""A complete local path from a saved home through HAR and replayable logs."""

import csv
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import activity
import automatic
import consumption_profiles
import log
import read
import sensor
import sim
from house_state import HouseState
from har import storage
from har.catalogue import default_catalogue
from har.learning import train_source
from timer import TimerApp


class SimulatorWorkflowTests(unittest.TestCase):
    @staticmethod
    def source_sessions():
        """Independent labelled washer cycles, distinct from the tested house."""
        sessions = []
        for index in range(5):
            frames = []
            for tick in range(40):
                active = tick % 4 in (1, 2)
                watts = 100 + 20 * ((tick + index) % 6) if active else 0
                frames.append({
                    "time_seconds": tick * (60 + index), "labels": ["laundry"] if active else [],
                    "devices": [{"name": "source_washer", "type": "Washing_Machine", "state": int(active),
                                 "power": 1000, "max_consumption": 1000, "current_consumption": watts,
                                 "categories": ["Device_Cleaning"]}],
                    "sensors": [{"name": "source_meter", "type": "Smart Meter", "min_val": 0, "max_val": 1000,
                                 "state": int(active), "consumption": watts, "associated_device": "source_washer",
                                 "categories": ["Sensor_Power"]}],
                    "sensor_states": {"source_meter": {"state": [int(active)], "consumption": [watts]}},
                    "rooms": [],
                })
            sessions.append({"schema_version": 1, "session_id": f"source-cycle-{index}",
                             "simulation_session_id": f"source-simulation-{index}", "house_id": "source-home",
                             "house_label": "Source washer", "partition": "source",
                             "catalogue": default_catalogue(), "frames": frames})
        return sessions

    def test_loaded_appliance_cycle_produces_matching_har_history_and_replay(self):
        with ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            stack.enter_context(patch.object(storage, "DATA_DIR", root / "har_data"))
            model = train_source(self.source_sessions(), "source-home", window_seconds=60)
            storage.save_model(model)
            for name in ("coordinates", "read_walls", "read_walls_coordinates", "read_sensors", "read_devices", "read_doors"):
                stack.enter_context(patch.object(read, name, []))
            stack.enter_context(patch.object(read, "read_room_overrides", {}))
            stack.enter_context(patch.object(log, "get_or_create_current_save_session", return_value=root))
            stack.enter_context(patch.object(sensor, "_load_llm_profile_catalog", return_value={}))
            stack.enter_context(patch.object(sim, "prime_llm_cycle_for_sensor", return_value=None))
            stack.enter_context(patch.object(consumption_profiles, "add_noise", side_effect=lambda value: value))
            stack.enter_context(patch.dict(consumption_profiles.consumption_profiles, {
                "Washing_Machine": {"standby": 0, "profile": {0: 100, 1: 200, 2: 100, 3: 0}},
            }))
            path = root / "home.csv"
            path.write_text(
                "Positions\na,0,0\nb,100,0\nWalls\na,b\n"
                "Sensors\nmeter,10,10,Smart Meter,0,1000,1,0,None,None,washer\n"
                "Devices\nwasher,10,10,Washing_Machine,1000,0,100,1000,0,1\nDoors\n",
                encoding="utf-8",
            )
            points, walls, sensors, devices, doors = read.read_coordinates_from_file(path)
            runtime = {"points": points, "walls": walls, "sensors": sensors, "devices": devices, "doors": doors,
                       "har_config": {"house_id": "target-home", "model_id": model["model_id"]}}
            state = HouseState()
            timer = TimerApp.__new__(TimerApp)
            timer.simulated_start_time = datetime(2026, 7, 14, 12)
            timer.current_date = "2026-07-14"
            timer.elapsed_time = timedelta()
            timer.is_running = True
            log.start_interaction_log_session(state)
            try:
                activity.process_activities(None, timer, state.sensor_states(), state, runtime)
                sim.toggle_device_state(None, None, state, timer, runtime, x=10, y=10, render=False)
                self.assertEqual(devices[0].state, 1)
                for minute in range(1, 4):
                    timer.elapsed_time = timedelta(seconds=minute)
                    sim.update_sensors(None, timer, None, state, runtime, schedule_next=False, delta_override=1)
                    activity.process_activities(None, timer, state.sensor_states(), state, runtime)
                activity.close_current_activity(timer, house_state=state)
                log.save_activity_log(root / "activities.csv", state.activity_log_state())
            finally:
                log.stop_interaction_log_session(state)

            self.assertEqual(devices[0].state, 0)
            self.assertEqual(state.active_cycles(), {})
            self.assertEqual(state.sensor_states()["meter"]["consumption"], [200, 100, 0])
            self.assertEqual(state.sensor_states()["meter"]["state"], [1, 1, 0])
            with (root / "activities.csv").open(encoding="utf-8", newline="") as stream:
                self.assertEqual(list(csv.DictReader(stream)), [
                    {"activity": "laundry", "start": "12:01", "end": "12:03"},
                ])

            ctx = SimpleNamespace(automatic_state={})
            interactions = root / "interactions" / "interactions.csv"
            with patch.object(automatic, "ask_open_file", return_value=str(interactions)), patch.object(automatic, "messagebox") as messages:
                automatic.select_path_csv(ctx)
            messages.showerror.assert_not_called()
            replay, selected = automatic.build_sensor_states_from_interactions(ctx, (0,))
            self.assertEqual(selected, ["Smart Meter:meter"])
            self.assertEqual(replay["Smart Meter:meter"]["state"], [200, 100, 0])
            self.assertEqual(replay["Smart Meter:meter"]["time"], [
                "2026-07-14 12:01:00", "2026-07-14 12:02:00", "2026-07-14 12:03:00",
            ])


if __name__ == "__main__":
    unittest.main()
