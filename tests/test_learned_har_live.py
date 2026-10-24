"""Live simulator contracts for learned recognition and recording lifecycle."""

from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import activity
import sim
from app.context import AppContext
from app.controllers import simulation
from har import storage
from house_state import HouseState
from models import Device, Sensor
from timer import TimerApp


class LearnedActivityFlowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(storage, "DATA_DIR", Path(temporary)))
        self.house = HouseState()
        self.label = Mock()
        self.timer = SimpleNamespace(
            elapsed_time=timedelta(seconds=3), is_running=True,
            get_simulated_time=Mock(return_value="12:03"),
        )
        self.oven = Device("oven", 10, 0, "Oven", 2000, 1, 1500, 2000)
        self.oven.categories = ["DeviceCooking"]
        self.sources = {
            "devices": [self.oven], "sensors": [], "points": [],
            "rooms": [], "walls": [], "doors": [],
            "har_config": storage.new_house_config(),
        }

    def process(self):
        activity.process_activities(
            self.label, self.timer, self.house.sensor_states(), self.house, self.sources,
        )

    def test_cooking_category_and_active_device_do_not_recognize_without_a_model(self):
        self.process()
        self.assertEqual(self.house.activity_state()["current_activities"], {})
        self.assertEqual(self.house.activity_state()["har_prediction"]["status"], "no_model")
        self.assertEqual(self.house.activity_log_state()["active_activities"], {})
        self.assertIn("no trained model", self.label.config.call_args.kwargs["text"])

    def test_callers_without_house_configuration_still_use_learned_recognition(self):
        self.sources.pop("har_config")
        self.sources["sensors"] = [Sensor("kitchen", 0, 0, "PIR", 0, 1, 1, 1, direction=0)]
        self.house.sensor_states()["kitchen"] = {"state": [1]}
        self.process()
        self.assertEqual(self.house.activity_state()["har_prediction"]["status"], "no_model")
        self.assertEqual(self.house.activity_log_state()["active_activities"], {})

    def test_only_model_predictions_start_and_end_logged_activities(self):
        result = {
            "activities": ["cooking"], "probabilities": {"cooking": .84},
            "confidence": .84, "status": "ready", "abstained": False,
        }
        with patch("har.runtime.process_frame", return_value=result) as recognizer:
            self.process()
            recognizer.assert_called_once_with(
                self.house.sensor_states(), self.sources, self.house.activity_state(),
                180, house_state=self.house,
            )
            self.assertEqual(self.house.activity_log_state()["active_activities"], {"cooking": "12:03"})
            self.assertIn("84% (uncalibrated)", self.label.config.call_args.kwargs["text"])
            recognizer.return_value = {
                "activities": [], "probabilities": {"cooking": .1},
                "confidence": .9, "status": "ready", "abstained": False,
            }
            self.timer.get_simulated_time.return_value = "12:04"
            self.process()
        self.assertEqual(self.house.activity_log_state()["activity_log"], [
            {"activity": "cooking", "start": "12:03", "end": "12:04"},
        ])
        self.assertIn("Activities (ML): None", self.label.config.call_args.kwargs["text"])

    def test_abstention_and_model_errors_never_create_rule_based_activities(self):
        with patch("har.runtime.process_frame", return_value={
            "activities": ["cooking"], "probabilities": {"cooking": .52},
            "confidence": .52, "status": "uncertain", "abstained": True,
        }) as recognizer:
            self.process()
            self.assertEqual(self.house.activity_state()["current_activities"], {})
            self.assertIn("uncertain", self.label.config.call_args.kwargs["text"])
            recognizer.side_effect = ValueError("Model schema is incompatible")
            self.process()
        self.assertEqual(self.house.activity_log_state()["activity_log"], [])
        self.assertEqual(self.house.activity_state()["har_prediction"]["status"], "error")
        self.assertIn("Model schema is incompatible", self.label.config.call_args.kwargs["text"])

    def test_pausing_does_not_add_observations_or_predictions(self):
        self.timer.is_running = False
        with patch("har.runtime.process_frame") as recognizer:
            self.process()
        recognizer.assert_not_called()

    def test_training_labels_are_recorded_without_becoming_predictions(self):
        from har.runtime import finish_recording, set_recording_labels, start_recording

        ctx = SimpleNamespace(house_state=self.house, har_config=self.sources["har_config"])
        session = start_recording(ctx)
        set_recording_labels(ctx, ["cooking"])
        self.process()
        self.timer.is_running = False
        self.process()
        self.timer.is_running = True
        self.timer.elapsed_time = timedelta(seconds=4)
        self.process()
        self.assertEqual(len(session["frames"]), 2)
        self.assertEqual([frame["time_seconds"] for frame in session["frames"]], [180, 240])
        self.assertEqual([frame["labels"] for frame in session["frames"]], [["cooking"], ["cooking"]])
        self.assertEqual(self.house.activity_log_state()["active_activities"], {})
        path = finish_recording(self.house)
        self.assertEqual(storage.read_json(path)["frames"], session["frames"])

    def test_real_source_trained_model_recognizes_target_devices_and_updates_log(self):
        from har.catalogue import save_catalogue
        from har.demo import demo_sessions
        from har.learning import train_source
        from har.runtime import set_recording_labels, start_recording

        sessions = demo_sessions(sessions_per_house=3)
        model = train_source(sessions, "demo-source")
        storage.save_model(model)
        target = next(session for session in sessions if session["partition"] == "target")
        save_catalogue(target["catalogue"])
        self.sources["har_config"].update(house_id=target["house_id"], house_label=target["house_label"])

        def apply_frame(frame):
            self.sources["devices"] = [SimpleNamespace(**obj) for obj in frame["devices"]]
            self.sources["sensors"] = [SimpleNamespace(**obj) for obj in frame["sensors"]]
            # Observed readings take precedence over stale model-object values.
            for sensor in self.sources["sensors"]:
                sensor.state, sensor.consumption = 0, 0
            self.sources["rooms"] = [SimpleNamespace(**room) for room in frame["rooms"]]
            self.house.sensor_states().clear()
            self.house.sensor_states().update(frame["sensor_states"])
            self.timer.elapsed_time = timedelta(seconds=frame["time_seconds"] / 60)
            self.timer.get_simulated_time.return_value = (
                f"{int(frame['time_seconds'] // 3600):02}:{int(frame['time_seconds'] // 60) % 60:02}"
            )

        apply_frame(target["frames"][0])
        self.process()
        self.assertEqual(self.house.activity_state()["har_prediction"]["status"], "no_model")
        self.sources["har_config"]["model_id"] = model["model_id"]
        ctx = SimpleNamespace(house_state=self.house, har_config=self.sources["har_config"])
        start_recording(ctx, partition="target")
        set_recording_labels(ctx, ["deliberately_wrong_label"])
        matches = []
        for frame in target["frames"]:
            apply_frame(frame)
            self.process()
            if frame["labels"] is not None:
                matches.append(set(self.house.activity_state()["current_activities"]) == set(frame["labels"]))
        activity.close_current_activity(self.timer, house_state=self.house)
        logged = {entry["activity"] for entry in self.house.activity_log_state()["activity_log"]}
        self.assertEqual(logged, {"cooking", "relaxing"})
        self.assertNotIn("deliberately_wrong_label", logged)
        self.assertGreater(sum(matches) / len(matches), .75)
        self.assertIn("uncalibrated", self.label.config.call_args.kwargs["text"])


class LearnedRecordingLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(storage, "DATA_DIR", Path(temporary)))
        self.ctx = AppContext(window=Mock(), canvas=Mock(), timer_frame=Mock(), activity_label=Mock())
        self.ctx.scenario_menu = Mock()
        self.ctx.simulation_menu = Mock()
        self.ctx.timer_frame.winfo_children.return_value = []
        self.sensor = Sensor("pir", 0, 0, "PIR", 0, 1, 1, 0)
        self.sources = {"devices": [], "sensors": [self.sensor], "points": [], "walls": [], "doors": []}
        self.timer = SimpleNamespace(is_running=False, get_simulated_time=Mock(return_value="12:00"))
        self.stack.enter_context(patch.object(simulation, "_resolve_runtime_sources", return_value=self.sources))
        self.stack.enter_context(patch("app.ui.rooms.refresh_rooms", return_value=[]))
        self.stack.enter_context(patch.object(simulation, "set_canvas_cursor"))
        self.timer_factory = self.stack.enter_context(patch.object(simulation, "TimerApp", return_value=self.timer))
        from har.runtime import finish_recording
        self.finish_original = finish_recording
        self.finish = self.stack.enter_context(patch("har.runtime.finish_recording"))
        self.error = self.stack.enter_context(patch("tkinter.messagebox.showerror"))
        for name in ("close_current_activity", "stop_interaction_log_session", "start_interaction_log_session",
                     "start_simulation", "monitor_activities"):
            self.stack.enter_context(patch.object(simulation, name))

    def create_session(self):
        simulation.start_sim(self.ctx)
        return self.timer_factory.call_args.kwargs

    def test_live_runtime_uses_mutable_house_configuration_and_pause_keeps_recording(self):
        self.ctx.har_config = storage.new_house_config()
        callbacks = self.create_session()
        self.assertIs(self.sources["har_config"], self.ctx.har_config)
        self.ctx.har_config["model_id"] = "new_model"
        self.assertEqual(self.sources["har_config"]["model_id"], "new_model")
        self.finish.reset_mock()
        marker = {"session_id": "recording"}
        self.ctx.house_state.values["har_recording"] = marker
        callbacks["pause_callback"]()
        self.assertIs(self.ctx.house_state.values["har_recording"], marker)
        self.finish.assert_not_called()
        callbacks["reset_callback"]()
        self.finish.assert_called_once_with(self.ctx.house_state)

    def test_new_session_does_not_discard_recording_if_saving_fails(self):
        marker = {"session_id": "unsaved"}
        self.ctx.house_state.values["har_recording"] = marker
        self.finish.side_effect = OSError("Disk full")
        simulation.start_sim(self.ctx)
        self.assertIs(self.ctx.house_state.values["har_recording"], marker)
        self.timer_factory.assert_not_called()
        self.error.assert_called_once()

    def test_device_state_only_house_can_start_without_a_separate_sensor(self):
        self.sources["sensors"] = []
        self.sources["devices"] = [Device("oven", 10, 0, "Oven", 2000, 0, 1500, 2000)]
        self.create_session()
        self.timer_factory.assert_called_once()

    def test_device_only_interaction_can_toggle_the_observed_state(self):
        oven = Device("oven", 10, 0, "Oven", 2000, 0, 1500, 2000)
        self.sources.update(sensors=[], devices=[oven], rooms=[])
        self.ctx.house_state.sim_state()["avatar_image"] = object()
        self.timer.is_running = True
        self.timer.current_date = "2026-10-23"
        with (
            patch.object(sim, "event_to_logical", return_value=(10, 0)),
            patch.object(sim, "to_canvas", return_value=(10, 0)),
            patch.object(sim, "interaction_with_door"),
            patch.object(sim, "_render_interaction_scene"),
            patch.object(sim, "log_move"),
            patch.object(sim, "log_device_event"),
        ):
            sim.interaction(self.ctx.canvas, self.timer, object(), None, self.ctx.house_state, self.sources)
        self.assertEqual(oven.state, 1)

    def test_failed_reset_keeps_session_and_does_not_schedule_cleanup(self):
        callbacks = self.create_session()
        self.finish.side_effect = OSError("Disk full")
        self.assertIs(callbacks["reset_callback"](), False)
        self.assertIs(self.ctx.timer_app_instance, self.timer)
        self.ctx.window.after.assert_not_called()

    def test_reset_saves_observations_before_cleanup(self):
        from har.runtime import set_recording_labels, start_recording

        callbacks = self.create_session()
        self.finish.side_effect = self.finish_original
        start_recording(self.ctx)
        set_recording_labels(self.ctx, ["cooking"])
        self.timer.is_running = True
        self.timer.elapsed_time = timedelta(seconds=5)
        activity.process_activities(
            self.ctx.activity_label, self.timer, self.ctx.house_state.sensor_states(),
            self.ctx.house_state, self.sources,
        )
        callbacks["reset_callback"]()
        self.assertNotIn("har_recording", self.ctx.house_state.values)
        sessions = storage.list_sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["frames"][0]["time_seconds"], 300)
        self.assertEqual(sessions[0]["frames"][0]["labels"], ["cooking"])

    def test_ending_house_session_saves_and_detaches_its_runtime_synchronously(self):
        from har.runtime import set_recording_labels, start_recording

        self.create_session()
        self.finish.side_effect = self.finish_original
        start_recording(self.ctx)
        set_recording_labels(self.ctx, ["cooking"])
        self.timer.is_running = True
        self.timer.elapsed_time = timedelta(seconds=2)
        self.timer.close = Mock()
        activity.process_activities(
            None, self.timer, self.ctx.house_state.sensor_states(), self.ctx.house_state, self.sources,
        )
        self.ctx.house_state.sim_state().update(
            sensor_update_job="old_poll", sensor_update_canvas=self.ctx.canvas, sensor_update_token=object(),
        )
        self.assertTrue(simulation.end_manual_session(self.ctx))
        self.assertIsNone(self.ctx.timer_app_instance)
        self.assertIsNone(self.ctx._manual_runtime_sources)
        self.assertIsNone(self.ctx._restore_manual_canvas_binding)
        self.assertFalse(self.timer.is_running)
        self.timer.close.assert_called_once_with()
        self.ctx.canvas.after_cancel.assert_called_once_with("old_poll")
        self.assertEqual(storage.list_sessions()[0]["frames"][0]["time_seconds"], 120)

    def test_old_reset_cleanup_cannot_clear_a_new_house_timer_or_recording(self):
        callbacks = self.create_session()
        callbacks["reset_callback"]()
        deferred_old_cleanup = self.ctx.window.after.call_args.args[1]
        self.assertTrue(simulation.end_manual_session(self.ctx))
        self.ctx.har_config = storage.new_house_config()
        next_timer = SimpleNamespace(is_running=False, get_simulated_time=Mock(return_value="13:00"))
        self.timer_factory.return_value = next_timer
        self.create_session()
        target_recording = {"session_id": "target_recording"}
        self.ctx.house_state.values["har_recording"] = target_recording
        self.finish.reset_mock()
        deferred_old_cleanup()
        self.assertIs(self.ctx.timer_app_instance, next_timer)
        self.assertIs(self.ctx._manual_runtime_sources["har_config"], self.ctx.har_config)
        self.assertIs(self.ctx.house_state.values["har_recording"], target_recording)
        self.finish.assert_not_called()

    def test_exit_is_cancelled_when_recording_cannot_be_saved(self):
        self.finish.side_effect = OSError("Disk full")
        with patch("app.confirmations.ask_confirmation", return_value=True):
            simulation.exit_app(self.ctx)
        self.ctx.window.quit.assert_not_called()
        self.error.assert_called_once()

    def test_timer_reset_can_preserve_unsaved_recording_clock_for_retry(self):
        timer = TimerApp.__new__(TimerApp)
        timer.start_time = datetime(2026, 10, 23, 12)
        timer.simulated_start_time = timer.start_time
        timer.elapsed_time = timedelta(seconds=25)
        timer.is_running = True
        timer.reset_callback = Mock(return_value=False)
        timer.start_stop_button = Mock()
        timer.last_update = datetime(2026, 10, 23, 12)
        timer.reset_flag = Mock()
        timer.reset()
        self.assertFalse(timer.is_running)
        self.assertEqual(timer.elapsed_time, timedelta(seconds=25))
        self.assertEqual(timer.simulated_start_time, datetime(2026, 10, 23, 12))
        timer.reset_flag.assert_not_called()
        timer.start_stop_button.config.assert_called_once_with(text="Start")

    def test_closed_timer_cancels_clock_poll_and_cannot_reschedule_it(self):
        from tests.test_simulation_runtime import make_timer

        timer = make_timer(start=datetime(2026, 10, 23, 12))
        timer.timer_frame.destroy = Mock()
        timer.update_timer()
        stale_poll = next(iter(timer.timer_frame.pending.values()))[1]
        timer.close()
        self.assertFalse(timer.is_running)
        self.assertEqual(timer.timer_frame.pending, {})
        stale_poll()
        self.assertEqual(timer.timer_frame.pending, {})
        timer.timer_frame.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
