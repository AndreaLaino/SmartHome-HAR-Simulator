"""Startup and controller contracts, using Tk doubles and real HouseState."""

import unittest
from contextlib import ExitStack
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import main
import app.controllers.simulation as controller
from app.controllers.simulation import _resolve_runtime_sources
from app.context import AppContext
from models import Sensor


class ApplicationStartupTests(unittest.TestCase):
    def test_custom_home_load_finishes_before_manual_mode_starts(self):
        ctx = AppContext(window=Mock(), canvas=Mock(), preferences={
            "starting_home": "custom", "starting_home_path": "home.csv", "start_manual_mode": True,
        })
        actions = Mock()
        with (
            patch("app.io.scenario.load_scenario_from_path", actions.load),
            patch.object(controller, "start_sim", actions.start),
        ):
            main.apply_startup_preferences(ctx)
        self.assertEqual(actions.mock_calls, [call.load(ctx, ctx.canvas, "home.csv"), call.start(ctx)])

    def test_empty_preferences_do_not_load_a_home_or_start_a_timer(self):
        ctx = AppContext(window=Mock(), canvas=Mock())
        with (
            patch("app.io.scenario.load_scenario_from_file") as load_default,
            patch("app.io.scenario.load_scenario_from_path") as load_custom,
            patch.object(controller, "start_sim") as start,
        ):
            main.apply_startup_preferences(ctx)
        load_default.assert_not_called()
        load_custom.assert_not_called()
        start.assert_not_called()

    def test_main_wires_context_window_close_and_deferred_canvas_restore(self):
        window = Mock()
        events = []
        preferences = {"starting_home": "empty", "canvas_zoom": 2}
        with (
            patch.object(main.tk, "Tk", return_value=window),
            patch.object(main, "setup_logging"),
            patch.object(main, "load_preferences", return_value=preferences),
            patch.object(main, "restore_window_preference", side_effect=lambda *_: events.append("window")) as restore_window,
            patch.object(main, "build_home_ui", side_effect=lambda ctx: events.append("ui")) as build,
            patch.object(main, "apply_startup_preferences", side_effect=lambda *_: events.append("startup")) as startup,
            patch.object(main, "restore_canvas_preferences", side_effect=lambda *_: events.append("canvas")) as restore_canvas,
            patch.object(main, "exit_app") as exit_app,
        ):
            main.main()
            ctx = build.call_args.args[0]
            self.assertIsInstance(ctx, AppContext)
            self.assertIs(ctx.window, window)
            self.assertIs(ctx.preferences, preferences)
            self.assertEqual(events, ["window", "ui", "startup"])
            restore_window.assert_called_once_with(window, preferences)
            startup.assert_called_once_with(ctx)
            restore_canvas.assert_not_called()
            deferred = window.after_idle.call_args.args[0]
            deferred()
            restore_canvas.assert_called_once_with(ctx)
            close_event, close_callback = window.protocol.call_args.args
            self.assertEqual(close_event, "WM_DELETE_WINDOW")
            exit_app.assert_not_called()
            close_callback()
            exit_app.assert_called_once_with(ctx)
        self.assertEqual(events, ["window", "ui", "startup", "canvas"])
        window.mainloop.assert_called_once_with()

    def test_rebuilding_main_interface_uses_the_existing_window_and_context(self):
        ctx = AppContext(window=Mock())
        with patch.object(main, "build_home_ui") as build:
            main.rebuild_main_interface(ctx)
        ctx.window.title.assert_called_once_with("Simulator")
        build.assert_called_once_with(ctx)


class ManualControllerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.ctx = AppContext(window=Mock(), canvas=Mock(), timer_frame=Mock(), activity_label=Mock())
        self.ctx.scenario_menu = Mock()
        self.ctx.simulation_menu = Mock()
        self.ctx._canvas_mode = "manual"
        self.ctx._cancel_canvas_action = Mock()
        self.ctx._set_canvas_mode = Mock()
        self.ctx._activate_canvas_manual = Mock()
        self.ctx._activate_canvas_select = Mock()
        self.ctx._apply_theme = Mock()
        self.child = Mock()
        self.ctx.timer_frame.winfo_children.return_value = [self.child]
        self.ctx.preferences = {"start_time": "midnight"}
        self.ctx.smartmeter_mode = "llm"
        self.sensor = Sensor("pir", 0, 0, "PIR", 0, 1, 1, 0)
        self.sources = {"points": [], "sensors": [self.sensor], "walls": [], "devices": [], "doors": []}
        self.rooms = [object()]
        self.resolve = self.stack.enter_context(patch.object(controller, "_resolve_runtime_sources", return_value=self.sources))
        self.stack.enter_context(patch("app.ui.rooms.refresh_rooms", return_value=self.rooms))
        self.stack.enter_context(patch.object(controller, "set_canvas_cursor"))
        self.warning = self.stack.enter_context(patch("tkinter.messagebox.showwarning"))
        self.timer = SimpleNamespace(is_running=False, get_simulated_time=Mock(return_value=datetime(2026, 1, 1)))
        self.timer_factory = self.stack.enter_context(patch.object(controller, "TimerApp", return_value=self.timer))
        self.start_simulation = self.stack.enter_context(patch.object(controller, "start_simulation"))
        self.monitor = self.stack.enter_context(patch.object(controller, "monitor_activities"))
        self.update = self.stack.enter_context(patch.object(controller, "update_sensors"))
        self.process = self.stack.enter_context(patch.object(controller, "process_activities"))
        self.interaction = self.stack.enter_context(patch.object(controller, "interaction"))
        self.close_activity = self.stack.enter_context(patch.object(controller, "close_current_activity"))
        self.start_log = self.stack.enter_context(patch.object(controller, "start_interaction_log_session"))
        self.stop_log = self.stack.enter_context(patch.object(controller, "stop_interaction_log_session"))
        self.reset_llm = self.stack.enter_context(patch("sensor.reset_llm_runtime_state"))
        self.reset_temperature = self.stack.enter_context(patch("sensor.reset_temperature_runtime_state"))
        self.set_meter_mode = self.stack.enter_context(patch("sensor.set_llm_smartmeter_mode"))

    def create_session(self):
        controller.start_sim(self.ctx)
        return self.timer_factory.call_args.kwargs

    def test_session_creation_resets_previous_run_and_waits_for_start_button(self):
        self.ctx.house_state.sensor_states()["old"] = {"state": [1]}
        self.ctx.house_state.runtime()["old"] = True
        callbacks = self.create_session()
        self.assertIs(self.ctx.timer_app_instance, self.timer)
        self.assertFalse(self.ctx._manual_reset_pending)
        self.assertIs(self.ctx._manual_runtime_sources, self.sources)
        self.assertIs(self.sources["rooms"], self.rooms)
        self.assertEqual(self.ctx.house_state.sensor_states(), {})
        self.assertEqual(self.ctx.house_state.runtime(), {})
        self.assertEqual(callbacks["start_time_mode"], "midnight")
        self.child.destroy.assert_called_once_with()
        self.start_simulation.assert_not_called()
        self.start_log.assert_not_called()
        self.reset_llm.assert_not_called()
        self.ctx._apply_theme.assert_called_once_with()

    def test_second_session_request_does_not_replace_timer_or_canvas_tool(self):
        self.create_session()
        self.ctx._cancel_canvas_action.reset_mock()
        self.resolve.reset_mock()
        controller.start_sim(self.ctx)
        self.assertEqual(self.timer_factory.call_count, 1)
        self.ctx._cancel_canvas_action.assert_not_called()
        self.resolve.assert_not_called()
        self.warning.assert_called_once()
        self.assertIs(self.ctx.timer_app_instance, self.timer)

    def test_home_without_sensors_does_not_create_a_timer_or_erase_runtime(self):
        self.sources["sensors"] = []
        self.ctx.house_state.runtime()["keep"] = True
        controller.start_sim(self.ctx)
        self.timer_factory.assert_not_called()
        self.start_simulation.assert_not_called()
        self.warning.assert_called_once()
        self.assertEqual(self.ctx.house_state.runtime(), {"keep": True})

    def test_start_binds_interaction_and_initializes_sensor_runtime(self):
        callbacks = self.create_session()
        callbacks["start_callback"]()
        self.reset_llm.assert_called_once_with()
        self.reset_temperature.assert_called_once_with()
        self.set_meter_mode.assert_called_once_with("llm")
        self.start_simulation.assert_called_once_with(self.ctx.canvas, self.timer, self.ctx.activity_label, self.ctx.house_state, self.sources)
        self.monitor.assert_called_once_with(self.ctx.canvas, self.ctx.activity_label, self.timer, self.ctx.house_state.sensor_states(), self.ctx.house_state, self.sources)
        self.start_log.assert_called_once_with(self.ctx.house_state, datetime(2026, 1, 1))
        binding = self.ctx.canvas.bind.call_args
        self.assertEqual(binding.args[0], "<Button-1>")
        event = object()
        binding.args[1](event)
        self.interaction.assert_called_once_with(self.ctx.canvas, self.timer, event, self.ctx.activity_label, self.ctx.house_state, self.sources)

    def test_resume_preserves_runtime_and_existing_interaction_log(self):
        callbacks = self.create_session()
        callbacks["start_callback"]()
        self.ctx.house_state.interaction_log_state()["interaction_file"] = "existing.csv"
        self.ctx.house_state.sensor_states()["pir"] = {"state": [1]}
        callbacks["pause_callback"]()
        callbacks["start_callback"]()
        self.assertEqual(self.reset_llm.call_count, 1)
        self.assertEqual(self.reset_temperature.call_count, 1)
        self.assertEqual(self.start_log.call_count, 1)
        self.assertEqual(self.ctx.house_state.sensor_states()["pir"], {"state": [1]})
        self.assertEqual(self.start_simulation.call_count, 2)

    def test_pause_unbinds_manual_interaction(self):
        callbacks = self.create_session()
        self.ctx.canvas.unbind.reset_mock()
        callbacks["pause_callback"]()
        self.ctx.canvas.unbind.assert_called_once_with("<Button-1>")
        self.assertEqual(self.ctx._set_canvas_mode.call_args.args[0], "manual")

    def test_pause_keeps_another_active_canvas_tool_bound(self):
        callbacks = self.create_session()
        self.ctx._canvas_mode = "select"
        self.ctx.canvas.unbind.reset_mock()
        self.ctx._set_canvas_mode.reset_mock()
        callbacks["pause_callback"]()
        self.ctx.canvas.unbind.assert_not_called()
        self.ctx._set_canvas_mode.assert_not_called()

    def test_advance_step_updates_sensors_and_activities_without_scheduling(self):
        self.create_session()
        self.timer.on_advance_step(30)
        self.update.assert_called_once_with(self.ctx.canvas, self.timer, self.ctx.activity_label, self.ctx.house_state, self.sources, schedule_next=False, force=True, delta_override=30, fast=True)
        self.process.assert_called_once_with(self.ctx.activity_label, self.timer, self.ctx.house_state.sensor_states(), self.ctx.house_state, self.sources)

    def test_skip_completion_renders_current_models_without_an_extra_simulation_tick(self):
        with patch.object(controller, "render_simulation_state") as render:
            self.create_session()
            self.timer.on_advance_complete()
        render.assert_called_once_with(self.ctx.canvas, self.sources)
        self.update.assert_not_called()
        self.process.assert_not_called()

    def test_reset_closes_activity_log_restores_selection_and_cleans_context(self):
        callbacks = self.create_session()
        callbacks["reset_callback"]()
        self.close_activity.assert_called_once_with(self.timer, self.ctx.activity_label, self.ctx.house_state)
        self.stop_log.assert_called_once_with(self.ctx.house_state)
        self.ctx._activate_canvas_select.assert_called_once_with()
        self.assertIs(self.ctx.timer_app_instance, self.timer)
        self.assertTrue(self.ctx._manual_reset_pending)
        delay, deferred = self.ctx.window.after.call_args.args
        self.assertEqual(delay, 100)
        deferred()
        self.assertIsNone(self.ctx.timer_app_instance)
        self.assertFalse(self.ctx._manual_reset_pending)
        self.assertIsNone(self.ctx._manual_runtime_sources)
        self.assertIsNone(self.ctx._restore_manual_canvas_binding)
        self.ctx.simulation_menu.entryconfig.assert_any_call("Manual", state="normal")
        self.ctx.activity_label.config.assert_called_with(text="Activity: None")

    def test_failed_recording_save_does_not_mark_reset_pending(self):
        callbacks = self.create_session()
        self.ctx.window.after.reset_mock()
        with patch.object(controller, "_finish_har_recording", return_value=False):
            self.assertFalse(callbacks["reset_callback"]())
        self.assertFalse(self.ctx._manual_reset_pending)
        self.assertIs(self.ctx.timer_app_instance, self.timer)
        self.ctx.window.after.assert_not_called()
        self.close_activity.assert_not_called()

    def test_stale_cleanup_cannot_clear_another_timers_pending_reset(self):
        self.create_session()
        old_timer = self.timer
        replacement = SimpleNamespace(is_running=False)
        self.ctx.timer_app_instance = replacement
        self.ctx._manual_reset_pending = True
        with patch.object(controller, "_finish_har_recording") as finish:
            controller._cleanup_manual_sim(self.ctx, expected_timer=old_timer)
        finish.assert_not_called()
        self.assertTrue(self.ctx._manual_reset_pending)
        self.assertIs(self.ctx.timer_app_instance, replacement)

    def test_restoring_running_manual_mode_rebinds_click_without_starting_timer(self):
        self.create_session()
        self.timer.is_running = True
        self.ctx.canvas.bind.reset_mock()
        self.ctx._restore_manual_canvas_binding()
        self.ctx.canvas.bind.assert_called_once()
        self.start_simulation.assert_not_called()

    def test_restoring_paused_manual_mode_waits_for_start(self):
        self.create_session()
        self.ctx.canvas.bind.reset_mock()
        self.ctx._restore_manual_canvas_binding()
        self.ctx.canvas.bind.assert_not_called()
        self.start_simulation.assert_not_called()
        self.ctx._activate_canvas_manual.assert_called()

    def test_runtime_sources_select_matching_editor_or_loaded_objects(self):
        with ExitStack() as local:
            expected = {}
            for is_loaded, module_names in (
                (False, (("point.points", "points"), ("sensor.sensors", "sensors"), ("wall.walls_coordinates", "walls"), ("device.devices", "devices"), ("door.doors", "doors"))),
                (True, (("read.coordinates", "points"), ("read.read_sensors", "sensors"), ("read.read_walls_coordinates", "walls"), ("read.read_devices", "devices"), ("read.read_doors", "doors"))),
            ):
                for target, key in module_names:
                    expected[key] = local.enter_context(patch(target, [object()]))
                actual = _resolve_runtime_sources(is_loaded)
                for key in expected:
                    self.assertIs(actual[key], expected[key])


class ApplicationShutdownTests(unittest.TestCase):
    def test_declined_exit_keeps_window_and_loggers_running(self):
        ctx = AppContext(window=Mock(), smart_logger=Mock())
        with (
            patch("app.confirmations.ask_confirmation", return_value=False),
            patch("app.preferences.capture_view_preferences") as capture,
            patch("app.preferences.save_preferences") as save,
        ):
            controller.exit_app(ctx)
        capture.assert_not_called()
        save.assert_not_called()
        ctx.smart_logger.stop.assert_not_called()
        ctx.window.quit.assert_not_called()

    def test_exit_persists_preferences_stops_all_loggers_and_quits(self):
        ctx = AppContext(window=Mock(), smart_logger=Mock(), preferences={"window_state": "normal"})
        ip_logger, gpio_logger = SimpleNamespace(stop_all=Mock()), SimpleNamespace(stop_all=Mock())
        with (
            patch("app.confirmations.ask_confirmation", return_value=True),
            patch("app.preferences.capture_view_preferences") as capture,
            patch("app.preferences.save_preferences") as save,
            patch.dict("sys.modules", {"app.hardware.smartmeter": ip_logger, "app.hardware.real_sensors": gpio_logger}),
        ):
            controller.exit_app(ctx)
        capture.assert_called_once_with(ctx)
        save.assert_called_once_with(ctx.preferences)
        ctx.smart_logger.stop.assert_called_once_with()
        ip_logger.stop_all.assert_called_once_with()
        gpio_logger.stop_all.assert_called_once_with()
        ctx.window.quit.assert_called_once_with()

    def test_exit_continues_when_preferences_and_one_logger_fail(self):
        ctx = AppContext(window=Mock(), smart_logger=Mock())
        ctx.smart_logger.stop.side_effect = RuntimeError("logger stopped unexpectedly")
        ip_logger = SimpleNamespace(stop_all=Mock(side_effect=RuntimeError("offline")))
        gpio_logger = SimpleNamespace(stop_all=Mock())
        with (
            patch("app.confirmations.ask_confirmation", return_value=True),
            patch("app.preferences.capture_view_preferences", side_effect=OSError("read-only settings")),
            patch.dict("sys.modules", {"app.hardware.smartmeter": ip_logger, "app.hardware.real_sensors": gpio_logger}),
        ):
            controller.exit_app(ctx)
        gpio_logger.stop_all.assert_called_once_with()
        ctx.window.quit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
