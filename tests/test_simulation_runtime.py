"""Headless contracts for clock progression, polling, and user interactions.

These tests use the real simulator models and sensor adapters. Hardware, replay
files, random noise, and Tk widgets are isolated so no display or device is needed.
"""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import consumption_profiles
import activity
import sensor as sensor_module
import sim
from house_state import HouseState
from models import Device, Door, Sensor, Wall
from timer import TimerApp
from utils import update_devices_consumption


class FakeWidget:
    """The small part of Tk used by timer and simulation runtime methods."""

    def __init__(self, value="12:00"):
        self.value = value
        self.options = {}
        self.pending = {}
        self.cancelled = []
        self.draws = []
        self._next_id = 0

    def config(self, **kwargs):
        self.options.update(kwargs)

    def cget(self, key):
        return "lightgrey" if key == "bg" else self.options[key]

    def get(self):
        return self.value

    def delete(self, *args):
        if args and isinstance(args[0], int):
            self.value = ""
        self.draws.append(("delete", args))

    def insert(self, _index, value):
        self.value = value

    def after(self, delay, callback, *args):
        self._next_id += 1
        job = f"after#{self._next_id}"
        self.pending[job] = (delay, (lambda: callback(*args)) if args else callback)
        return job

    def after_cancel(self, job):
        self.cancelled.append(job)
        self.pending.pop(job, None)

    def fire(self, job):
        _delay, callback = self.pending.pop(job)
        callback()

    def itemconfig(self, *args, **kwargs):
        self.draws.append(("itemconfig", args, kwargs))

    def create_image(self, *args, **kwargs):
        self.draws.append(("image", args, kwargs))
        return len(self.draws)

    def create_line(self, *args, **kwargs):
        self.draws.append(("line", args, kwargs))

    def canvasx(self, value):
        return value

    def canvasy(self, value):
        return value


class FrozenDateTime(datetime):
    current = datetime(2026, 2, 28, 12)

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)

    @classmethod
    def today(cls):
        return cls.current


def make_timer(*, running=True, start=None):
    timer = TimerApp.__new__(TimerApp)
    timer.start_callback = None
    timer.stop_callback = None
    timer.pause_callback = None
    timer.reset_callback = None
    timer.start_time_mode = "midnight"
    timer.on_advance_step = None
    timer.on_advance_complete = None
    timer.is_running = running
    timer.start_time = datetime(2026, 2, 28, 12) if start is not None else None
    timer.elapsed_time = timedelta()
    timer.simulated_start_time = start
    timer.current_date = "2026-02-28"
    timer.last_update = None
    timer.advance_speed = 1
    timer.advance_remaining = 0
    timer.advanced = False
    for name in (
        "timer_frame", "label", "start_stop_button", "start_hour_entry",
        "speed_1x_button", "speed_2x_button", "speed_5x_button",
    ):
        setattr(timer, name, FakeWidget())
    return timer


def make_sensor(name, sensor_type, *, state=0, x=0, y=0, device=None):
    return Sensor(
        name, x, y, sensor_type, 0, 50 if sensor_type == "Temperature" else 5000,
        1, state, direction=0, associated_device=device,
    )


def make_device(name="oven", device_type="Oven", *, state=1, x=0, y=0):
    return Device(name, x, y, device_type, 2000, state, 100, 2000)


def sources(*, sensors=(), devices=(), doors=(), walls=()):
    return {
        "sensors": list(sensors), "devices": list(devices),
        "doors": list(doors), "walls": list(walls), "points": [],
    }


class TimerProgressionTests(unittest.TestCase):
    def setUp(self):
        FrozenDateTime.current = datetime(2026, 2, 28, 12)
        self.clock = patch("timer.datetime", FrozenDateTime)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_start_uses_entered_time_on_the_current_date(self):
        timer = make_timer(running=False)
        timer.start_hour_entry.value = "23:59"
        timer.start_callback = Mock()

        timer.start_stop()

        self.assertTrue(timer.is_running)
        self.assertEqual(timer.simulated_start_time, datetime(2026, 2, 28, 23, 59))
        self.assertEqual(timer.get_simulated_time(), "23:59")
        self.assertEqual(timer.start_stop_button.options["text"], "Stop")
        timer.start_callback.assert_called_once_with()

    def test_invalid_start_times_leave_the_session_unstarted(self):
        for invalid in ("", "24:00", "12:60", "noon", "2026-02-28 12:00"):
            with self.subTest(invalid=invalid):
                timer = make_timer(running=False)
                timer.start_hour_entry.value = invalid
                timer.start_callback = Mock()
                with patch("builtins.print"):
                    timer.start_stop()
                self.assertFalse(timer.is_running)
                self.assertIsNone(timer.start_time)
                self.assertIsNone(timer.simulated_start_time)
                timer.start_callback.assert_not_called()

    def test_resume_keeps_elapsed_time_and_excludes_time_spent_paused(self):
        timer = make_timer(start=datetime(2026, 2, 28, 10))
        timer.elapsed_time = timedelta(seconds=10)
        timer.start_stop()
        FrozenDateTime.current += timedelta(hours=2)
        timer.start_stop()
        FrozenDateTime.current += timedelta(seconds=2)
        timer.update_timer()

        self.assertEqual(timer.elapsed_time, timedelta(seconds=12))
        self.assertEqual(timer.get_simulated_time(), "10:12")

    def test_update_applies_speed_without_discarding_fractional_seconds(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.last_update = FrozenDateTime.current
        timer.advance_speed = 5
        FrozenDateTime.current += timedelta(milliseconds=250)

        timer.update_timer()

        self.assertEqual(timer.elapsed_time, timedelta(seconds=1.25))
        self.assertEqual(timer.get_simulated_time(), "12:01")
        self.assertEqual(next(iter(timer.timer_frame.pending.values()))[0], 100)

    def test_first_update_initializes_wall_clock_without_inventing_elapsed_time(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.update_timer()
        self.assertEqual(timer.elapsed_time, timedelta())
        self.assertEqual(timer.last_update, FrozenDateTime.current)

    def test_paused_timer_still_schedules_ui_poll_without_advancing(self):
        timer = make_timer(running=False, start=datetime(2026, 2, 28, 12))
        timer.elapsed_time = timedelta(seconds=5)
        timer.update_timer()
        self.assertEqual(timer.elapsed_time, timedelta(seconds=5))
        self.assertEqual(len(timer.timer_frame.pending), 1)

    def test_calendar_rollover_handles_month_year_and_leap_day(self):
        for start, seconds, expected_date, expected_time in (
            (datetime(2026, 2, 28, 23, 59), 1, "2026-03-01", "00:00"),
            (datetime(2024, 2, 28, 23, 59), 1, "2024-02-29", "00:00"),
            (datetime(2026, 12, 31, 23, 59), 1442, "2027-01-02", "00:01"),
        ):
            with self.subTest(start=start):
                timer = make_timer(start=start)
                timer.elapsed_time = timedelta(seconds=seconds)
                self.assertEqual(timer.get_simulated_time(), expected_time)
                self.assertEqual(timer.current_date, expected_date)
                self.assertEqual(
                    sim.get_simulation_datetime(timer),
                    datetime.strptime(f"{expected_date} {expected_time}", "%Y-%m-%d %H:%M"),
                )

    def test_advance_buttons_visit_every_simulated_minute(self):
        for method_name, minutes in (("advance_time", 15), ("advance_hour", 60)):
            with self.subTest(method=method_name):
                timer = make_timer(start=datetime(2026, 2, 28, 23, 55))
                visited = []
                timer.on_advance_step = lambda step: visited.append(
                    (step, sim.get_simulation_datetime(timer))
                )
                getattr(timer, method_name)()
                self.assertEqual([step for step, _ in visited], [1] * minutes)
                self.assertEqual(
                    [dt for _, dt in visited],
                    [datetime(2026, 2, 28, 23, 55) + timedelta(minutes=i)
                     for i in range(1, minutes + 1)],
                )
                self.assertEqual(timer.elapsed_time, timedelta(seconds=minutes))
                self.assertTrue(timer.advanced)
                timer.timer_frame.fire(next(iter(timer.timer_frame.pending)))
                self.assertFalse(timer.advanced)

    def test_each_skip_notifies_completion_once_after_all_minute_steps(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        events = []
        timer.on_advance_step = lambda _step: events.append(("step", timer.get_simulated_time()))
        timer.on_advance_complete = lambda: events.append(("complete", timer.get_simulated_time()))
        timer.advance_time()
        timer.advance_hour()
        self.assertEqual([item for item in events if item[0] == "complete"],
                         [("complete", "12:15"), ("complete", "13:15")])
        self.assertEqual(events[15], ("complete", "12:15"))
        self.assertEqual(events[-1], ("complete", "13:15"))

    def test_consecutive_skips_cancel_old_flag_resets_and_ignore_stale_callbacks(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.advance_time()
        old_callback = next(iter(timer.timer_frame.pending.values()))[1]
        timer.advance_hour()
        self.assertEqual(len(timer.timer_frame.pending), 1)
        old_callback()
        self.assertTrue(timer.advanced)
        timer.timer_frame.fire(next(iter(timer.timer_frame.pending)))
        self.assertFalse(timer.advanced)

    def test_reset_cancels_a_completed_skip_flag_callback(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.advance_time()
        stale = next(iter(timer.timer_frame.pending.values()))[1]
        with patch.object(sensor_module, "reset_llm_runtime_state"), patch.object(
            sensor_module, "reset_temperature_runtime_state"
        ):
            timer.reset()
        self.assertEqual(timer.timer_frame.pending, {})
        timer.advanced = True
        stale()
        self.assertTrue(timer.advanced)

    def test_advance_is_ignored_without_a_running_session_or_positive_jump(self):
        for running, started, jump in ((False, True, 15), (True, False, 15),
                                       (True, True, 0), (True, True, -1)):
            with self.subTest(running=running, started=started, jump=jump):
                timer = make_timer(running=running, start=datetime(2026, 2, 28) if started else None)
                timer.on_advance_step = Mock()
                timer._do_advance_instant(jump)
                self.assertEqual(timer.elapsed_time, timedelta())
                self.assertEqual(timer.timer_frame.pending, {})
                timer.on_advance_step.assert_not_called()

    def test_advance_stops_if_a_step_callback_pauses_the_simulation(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        visited = []

        def pause_on_second_step(_step):
            visited.append(timer.get_simulated_time())
            if len(visited) == 2:
                timer.start_stop()

        timer.on_advance_step = pause_on_second_step
        timer.advance_time()
        self.assertEqual(visited, ["12:01", "12:02"])
        self.assertEqual(timer.elapsed_time, timedelta(seconds=2))

    def test_reset_clears_skip_flags_and_sensor_runtime(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.elapsed_time = timedelta(seconds=20)
        timer.advanced = True
        timer.advance_remaining = 15
        timer.reset_callback = Mock()
        with patch.object(sensor_module, "reset_llm_runtime_state") as llm_reset, patch.object(
            sensor_module, "reset_temperature_runtime_state"
        ) as temperature_reset:
            timer.reset()
        self.assertFalse(timer.is_running)
        self.assertFalse(timer.advanced)
        self.assertEqual(timer.advance_remaining, 0)
        self.assertEqual(timer.elapsed_time, timedelta())
        self.assertIsNone(timer.last_update)
        self.assertIsNone(timer.simulated_start_time)
        self.assertEqual(timer.start_hour_entry.get(), "00:00")
        timer.reset_callback.assert_called_once_with()
        llm_reset.assert_called_once_with()
        temperature_reset.assert_called_once_with()

    def test_reset_during_advance_prevents_remaining_steps_and_stale_display_updates(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.start_time_mode = "computer"
        visited = []

        def reset_on_second_step(_step):
            visited.append(timer.get_simulated_time())
            if len(visited) == 2:
                timer.reset()

        timer.on_advance_step = reset_on_second_step
        timer.on_advance_complete = Mock()
        with patch.object(sensor_module, "reset_llm_runtime_state"), patch.object(
            sensor_module, "reset_temperature_runtime_state"
        ):
            timer.advance_time()
        self.assertEqual(visited, ["12:01", "12:02"])
        self.assertEqual(timer.elapsed_time, timedelta())
        self.assertEqual(timer.label.options["text"], "Time: 12:00 \n Date: 2026-02-28")
        self.assertEqual(timer.timer_frame.pending, {})
        self.assertFalse(timer.advanced)
        timer.on_advance_complete.assert_not_called()

    def test_legacy_stop_callback_is_used_for_pause_and_reset(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.stop_callback = Mock()
        timer.start_stop()
        with patch.object(sensor_module, "reset_llm_runtime_state"), patch.object(
            sensor_module, "reset_temperature_runtime_state"
        ):
            timer.reset()
            timer.reset()
        self.assertEqual(timer.stop_callback.call_count, 2)

    def test_speed_selection_updates_progression_and_button_highlight(self):
        timer = make_timer()
        for speed in (1, 2, 5):
            timer.set_speed(speed)
            self.assertEqual(timer.advance_speed, speed)
            for value in (1, 2, 5):
                self.assertEqual(
                    getattr(timer, f"speed_{value}x_button").options["bg"],
                    "#cfe8cf" if value == speed else "lightgrey",
                )

    def test_batch_advance_respects_remaining_steps_and_cancels_when_paused(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.advance_speed = 5
        timer.advance_remaining = 3
        timer.on_advance_step = Mock()
        timer._do_advance_batch()
        self.assertEqual(timer.elapsed_time, timedelta(seconds=3))
        self.assertEqual(timer.advance_remaining, 0)
        timer.on_advance_step.assert_called_once_with(3)
        timer.is_running = False
        timer.advance_remaining = 10
        timer.timer_frame.fire(next(iter(timer.timer_frame.pending)))
        self.assertEqual(timer.advance_remaining, 0)
        self.assertEqual(timer.timer_frame.pending, {})

    def test_multiple_batches_notify_once_after_the_final_minute(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.advance_speed = 2
        timer.advance_remaining = 5
        steps = []
        timer.on_advance_step = steps.append
        timer.on_advance_complete = Mock()
        timer._do_advance_batch()
        for _ in range(2):
            timer.timer_frame.fire(next(iter(timer.timer_frame.pending)))
        self.assertEqual(steps, [2, 2, 1])
        timer.on_advance_complete.assert_not_called()
        timer.timer_frame.fire(next(iter(timer.timer_frame.pending)))
        timer.on_advance_complete.assert_called_once_with()
        self.assertEqual(timer.get_simulated_time(), "12:05")

    def test_reset_cancels_queued_batch_and_old_callback_cannot_advance_resumed_session(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.advance_remaining = 10
        timer._do_advance_batch()
        stale = next(iter(timer.timer_frame.pending.values()))[1]
        with patch.object(sensor_module, "reset_llm_runtime_state"), patch.object(
            sensor_module, "reset_temperature_runtime_state"
        ):
            timer.reset()
        self.assertEqual(timer.timer_frame.pending, {})
        timer.start_stop()
        timer.advance_remaining = 5
        stale()
        self.assertEqual(timer.elapsed_time, timedelta())
        self.assertEqual(timer.advance_remaining, 5)
        self.assertEqual(timer.timer_frame.pending, {})

    def test_repeated_batch_requests_replace_the_pending_batch_chain(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.advance_remaining = 5
        timer._do_advance_batch()
        stale = next(iter(timer.timer_frame.pending.values()))[1]
        timer._do_advance_batch()
        self.assertEqual(len(timer.timer_frame.pending), 1)
        stale()
        self.assertEqual(timer.elapsed_time, timedelta(seconds=2))
        self.assertEqual(timer.advance_remaining, 3)
        self.assertEqual(len(timer.timer_frame.pending), 1)

    def test_batch_reset_callback_clears_remaining_work_and_scheduling(self):
        timer = make_timer(start=datetime(2026, 2, 28, 12))
        timer.advance_remaining = 10
        timer.on_advance_step = lambda _step: timer.reset()
        with patch.object(sensor_module, "reset_llm_runtime_state"), patch.object(
            sensor_module, "reset_temperature_runtime_state"
        ):
            timer._do_advance_batch()
        self.assertEqual(timer.advance_remaining, 0)
        self.assertEqual(timer.elapsed_time, timedelta())
        self.assertEqual(timer.timer_frame.pending, {})


class SimulationTickTests(unittest.TestCase):
    def setUp(self):
        self.state = HouseState()
        self.timer = make_timer(start=datetime(2026, 2, 28, 12))
        self.canvas = FakeWidget()
        sensor_module.reset_temperature_runtime_state()
        sensor_module.reset_llm_runtime_state()
        self.addCleanup(sensor_module.reset_temperature_runtime_state)
        self.addCleanup(sensor_module.reset_llm_runtime_state)
        for target, kwargs in (
            ("sensor._get_llm_smartmeter_consumption", {"return_value": None}),
            ("sensor.get_replay_temperature", {"return_value": None}),
            ("sim.get_recorded_temperature", {"return_value": None}),
            ("consumption_profiles.add_noise", {"side_effect": float}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tick(self, runtime_sources, **kwargs):
        sim.update_sensors(self.canvas, self.timer, None, self.state, runtime_sources, **kwargs)

    def test_tick_updates_real_adapters_and_stores_typed_series(self):
        oven = make_device(x=20)
        temperature = make_sensor("temperature", "Temperature", state=22, x=20)
        meter = make_sensor("meter", "Smart Meter", device=oven.name)
        binary = [make_sensor(name, kind, state=1) for name, kind in (
            ("motion", "PIR"), ("door", "Switch"), ("seat", "Weight")
        )]
        self.state.active_cycles()[oven.name] = {
            "start_time": datetime(2026, 2, 28, 12), "cycle_type": "Oven"
        }
        self.timer.elapsed_time = timedelta(seconds=3)

        self.tick(sources(sensors=[temperature, meter, *binary], devices=[oven]), schedule_next=False)

        self.assertEqual(oven.current_consumption, 995.3)
        self.assertEqual(meter.consumption, oven.current_consumption)
        stored = self.state.sensor_states()
        self.assertEqual(stored["meter"]["consumption"], [995.3])
        self.assertEqual(stored["meter"]["state"], [1.0])
        self.assertEqual(stored["meter"]["associated_device"], "oven")
        self.assertEqual(stored["temperature"]["state"], [temperature.state])
        self.assertEqual(stored["temperature"]["real_state"], [None])
        for item in (temperature, meter, *binary):
            self.assertEqual(stored[item.name]["type"], item.type)
            self.assertEqual(stored[item.name]["time"], ["2026-02-28 12:03"])
        self.assertIs(self.state.runtime()["devices"][0], oven)
        self.assertEqual(self.state.runtime()["current_datetime"], datetime(2026, 2, 28, 12, 3))
        self.assertEqual(self.canvas.pending, {})

    def test_device_auto_stop_is_reflected_by_meter_in_the_same_tick(self):
        washer = make_device("washer", "Washing_Machine")
        meter = make_sensor("meter", "Smart Meter", device="washer")
        self.state.active_cycles()["washer"] = {
            "start_time": datetime(2026, 2, 28, 12), "cycle_type": "Washing_Machine"
        }
        self.timer.elapsed_time = timedelta(seconds=92)
        self.tick(sources(sensors=[meter], devices=[washer]), schedule_next=False)
        self.assertEqual(washer.state, 0)
        self.assertEqual(washer.current_consumption, 0)
        self.assertEqual(meter.consumption, 0)
        self.assertEqual(self.state.sensor_states()["meter"]["state"], [0.0])
        self.assertEqual(self.state.active_cycles(), {})

    def test_target_profiles_complete_fifteen_minute_advance_and_record_every_step(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from device import get_device_params
        from har import storage
        from har.runtime import finish_recording, set_recording_labels, start_recording

        appliances = []
        for kind in ("Microwave", "Toaster", "TV", "PlayStation"):
            params = get_device_params(kind)
            appliance = Device(kind, 0, 0, kind, params["power"], 1,
                               params["min_consumption"], params["max_consumption"])
            appliances.append(appliance)
            self.state.active_cycles()[kind] = {"start_time": self.timer.simulated_start_time, "cycle_type": kind}
        meters = [make_sensor(f"meter_{item.name}", "Smart Meter", device=item.name) for item in appliances]
        runtime = sources(devices=appliances, sensors=meters)
        config = storage.new_house_config()
        runtime["har_config"] = config
        ctx = SimpleNamespace(house_state=self.state, har_config=config)

        def advance_step(delta):
            self.tick(runtime, schedule_next=False, force=True, delta_override=delta, fast=True)
            activity.process_activities(None, self.timer, self.state.sensor_states(), self.state, runtime)

        with TemporaryDirectory() as temp, patch.object(storage, "DATA_DIR", Path(temp)):
            session = start_recording(ctx, partition="target")
            set_recording_labels(ctx, ["cooking"])
            self.timer.on_advance_step = advance_step
            self.timer.advance_time()
            self.assertEqual(self.timer.get_simulated_time(), "12:15")
            self.assertEqual(len(session["frames"]), 15)
            self.assertEqual(session["frames"][-1]["time_seconds"], 900)
            self.assertEqual(session["frames"][-1]["sensor_states"]["meter_Microwave"]["consumption"], [1000])
            for appliance in appliances:
                self.assertEqual(appliance.state, 0 if appliance.type == "Toaster" else 1)
            path = finish_recording(self.state)
            self.assertEqual(len(storage.read_json(path)["frames"]), 15)
            for appliance in appliances:
                if appliance.state:
                    sim.toggle_device_state(None, None, self.state, self.timer, runtime,
                                            x=0, y=0, target_device=appliance, render=False)
            self.tick(runtime, schedule_next=False, force=True)
            self.assertTrue(all(item.current_consumption == 0 for item in appliances))
            self.assertTrue(all(item.consumption == 0 for item in meters))

    def test_paused_tick_leaves_sensor_and_device_state_untouched(self):
        self.timer.is_running = False
        oven = make_device()
        oven.current_consumption = 123
        with patch("builtins.print"):
            self.tick(sources(sensors=[make_sensor("pir", "PIR")], devices=[oven]))
        self.assertEqual(oven.current_consumption, 123)
        self.assertEqual(self.state.sensor_states(), {})
        self.assertEqual(self.canvas.draws, [])
        self.assertEqual(self.canvas.pending, {})

    def test_forced_tick_updates_a_paused_simulation_without_scheduling(self):
        self.timer.is_running = False
        self.tick(sources(sensors=[make_sensor("pir", "PIR")]), force=True, delta_override=5)
        self.assertEqual(self.state.runtime()["delta_seconds"], 5)
        self.assertEqual(self.state.sensor_states()["pir"]["state"], [0])
        self.assertEqual(self.canvas.pending, {})

    def test_fast_tick_updates_analogue_series_without_any_canvas_rendering(self):
        meter = make_sensor("meter", "Smart Meter")
        self.tick(sources(sensors=[meter, make_sensor("pir", "PIR")]),
                  fast=True, schedule_next=False, delta_override=1)
        self.assertIn("meter", self.state.sensor_states())
        self.assertNotIn("pir", self.state.sensor_states())
        self.assertEqual(self.canvas.draws, [])

    def test_tick_uses_elapsed_delta_and_override_resets_the_delta_baseline(self):
        self.tick(sources(), schedule_next=False)
        self.assertEqual(self.state.runtime()["delta_seconds"], 1)
        self.timer.elapsed_time = timedelta(seconds=4.5)
        self.tick(sources(), schedule_next=False)
        self.assertEqual(self.state.runtime()["delta_seconds"], 4.5)
        self.timer.elapsed_time = timedelta(seconds=20)
        self.tick(sources(), schedule_next=False, delta_override=2)
        self.assertEqual(self.state.runtime()["delta_seconds"], 2)
        self.timer.elapsed_time = timedelta(seconds=23)
        self.tick(sources(), schedule_next=False)
        self.assertEqual(self.state.runtime()["delta_seconds"], 3)

    def test_poll_chain_has_only_one_pending_callback_after_repeated_start(self):
        runtime = sources(sensors=[make_sensor("pir", "PIR")])
        self.tick(runtime)
        old_callback = next(iter(self.canvas.pending.values()))[1]
        self.tick(runtime)
        self.assertEqual(len(self.canvas.pending), 1)
        self.assertEqual(len(self.canvas.cancelled), 1)
        before = list(self.state.sensor_states()["pir"]["time"])
        old_callback()
        self.assertEqual(self.state.sensor_states()["pir"]["time"], before)
        self.assertEqual(len(self.canvas.pending), 1)

    def test_poll_callback_updates_time_and_reschedules_once(self):
        runtime = sources(sensors=[make_sensor("pir", "PIR")])
        self.tick(runtime)
        self.timer.elapsed_time = timedelta(seconds=1)
        self.canvas.fire(next(iter(self.canvas.pending)))
        self.assertEqual(self.state.sensor_states()["pir"]["time"],
                         ["2026-02-28 12:00", "2026-02-28 12:01"])
        self.assertEqual(len(self.canvas.pending), 1)

    def test_fast_forward_does_not_replace_the_pending_normal_poll(self):
        runtime = sources(sensors=[make_sensor("pir", "PIR")])
        self.tick(runtime)
        pending_job = next(iter(self.canvas.pending))
        self.timer.elapsed_time = timedelta(seconds=15)
        self.tick(runtime, fast=True, schedule_next=False, delta_override=15)
        self.assertEqual(list(self.canvas.pending), [pending_job])
        self.assertEqual(self.canvas.cancelled, [])

    def test_quick_pause_and_resume_cancels_the_old_poll(self):
        runtime = sources(sensors=[make_sensor("pir", "PIR")])
        self.tick(runtime)
        old_callback = next(iter(self.canvas.pending.values()))[1]
        self.timer.start_stop()
        self.timer.start_stop()
        self.timer.elapsed_time = timedelta(seconds=2)
        self.tick(runtime)
        old_callback()
        self.assertEqual(len(self.canvas.pending), 1)
        self.assertEqual(self.state.sensor_states()["pir"]["time"],
                         ["2026-02-28 12:00", "2026-02-28 12:02"])

    def test_poll_callback_stops_when_paused_and_cannot_revive_cleared_session(self):
        runtime = sources(sensors=[make_sensor("pir", "PIR")])
        self.tick(runtime)
        self.timer.is_running = False
        with patch("builtins.print"):
            self.canvas.fire(next(iter(self.canvas.pending)))
        self.assertEqual(self.canvas.pending, {})
        self.timer.is_running = True
        self.tick(runtime)
        stale_job = next(iter(self.canvas.pending))
        self.state.clear_runtime_state()
        self.canvas.fire(stale_job)
        self.assertEqual(self.state.sensor_states(), {})
        self.assertEqual(self.canvas.pending, {})

    def test_instant_advance_processes_sensor_minute_series_across_midnight(self):
        self.timer.simulated_start_time = datetime(2026, 2, 28, 23, 55)
        meter = make_sensor("meter", "Smart Meter")
        runtime = sources(sensors=[meter])
        self.timer.on_advance_step = lambda step: self.tick(
            runtime, schedule_next=False, force=True, delta_override=step, fast=True
        )
        self.timer.on_advance_complete = lambda: sim.render_simulation_state(self.canvas, runtime)
        self.timer.advance_time()
        series = self.state.sensor_states()["meter"]
        self.assertEqual(len(series["time"]), 15)
        self.assertEqual(series["time"][0], "2026-02-28 23:56")
        self.assertEqual(series["time"][-1], "2026-03-01 00:10")
        self.assertEqual(len(series["time"]), len(series["state"]))
        self.assertEqual(len(series["time"]), len(series["consumption"]))
        self.assertEqual(self.canvas.pending, {})
        self.assertEqual(len(self.canvas.draws), 2)


class ActivityPollingTests(unittest.TestCase):
    def setUp(self):
        self.state = HouseState()
        self.timer = make_timer(start=datetime(2026, 2, 28, 12))
        self.canvas = FakeWidget()
        self.runtime = sources()
        patcher = patch.object(activity, "process_activities")
        self.process = patcher.start()
        self.addCleanup(patcher.stop)

    def monitor(self):
        activity.monitor_activities(self.canvas, None, self.timer,
                                    self.state.sensor_states(), self.state, self.runtime)

    def test_repeated_start_has_one_activity_poll_and_old_callback_is_inert(self):
        self.monitor()
        old_callback = next(iter(self.canvas.pending.values()))[1]
        self.monitor()
        self.assertEqual(len(self.canvas.pending), 1)
        self.assertEqual(len(self.canvas.cancelled), 1)
        self.process.reset_mock()
        old_callback()
        self.process.assert_not_called()
        self.assertEqual(len(self.canvas.pending), 1)

    def test_poll_processes_activities_and_reschedules_once(self):
        self.monitor()
        self.process.reset_mock()
        self.canvas.fire(next(iter(self.canvas.pending)))
        self.process.assert_called_once_with(None, self.timer, self.state.sensor_states(), self.state, self.runtime)
        self.assertEqual(len(self.canvas.pending), 1)

    def test_quick_pause_resume_replaces_the_old_pending_activity_poll(self):
        self.monitor()
        old_callback = next(iter(self.canvas.pending.values()))[1]
        self.timer.start_stop()
        self.timer.start_stop()
        self.monitor()
        self.process.reset_mock()
        old_callback()
        self.process.assert_not_called()
        self.assertEqual(len(self.canvas.pending), 1)

    def test_poll_stops_scheduling_while_paused(self):
        self.monitor()
        self.timer.is_running = False
        self.canvas.fire(next(iter(self.canvas.pending)))
        self.assertEqual(self.canvas.pending, {})

    def test_closing_activity_on_reset_cancels_poll_and_invalidates_queued_callback(self):
        self.monitor()
        old_callback = next(iter(self.canvas.pending.values()))[1]
        activity.close_current_activity(self.timer, None, self.state)
        self.assertEqual(self.canvas.pending, {})
        self.process.reset_mock()
        old_callback()
        self.process.assert_not_called()

    def test_cleared_session_cannot_be_revived_by_an_activity_callback(self):
        self.monitor()
        self.state.clear_runtime_state()
        self.process.reset_mock()
        self.canvas.fire(next(iter(self.canvas.pending)))
        self.process.assert_not_called()
        self.assertEqual(self.canvas.pending, {})


class ManualInteractionTests(unittest.TestCase):
    def setUp(self):
        self.state = HouseState()
        self.timer = make_timer(start=datetime(2026, 2, 28, 12))
        self.canvas = FakeWidget()

    def click(self, x, y, runtime):
        with patch.object(sim, "initialize_avatar_image", return_value=object()):
            sim.interaction(self.canvas, self.timer, SimpleNamespace(x=x, y=y),
                            None, self.state, runtime)

    def test_motion_moves_between_visible_sensors_then_turns_off_outside_view(self):
        left = make_sensor("left", "PIR", x=0)
        right = make_sensor("right", "PIR", x=100)
        runtime = sources(sensors=[left, right])
        self.click(20, 0, runtime)
        self.assertEqual((left.state, right.state), (1, 0))
        self.click(120, 0, runtime)
        self.assertEqual((left.state, right.state), (0, 1))
        self.click(-100, 100, runtime)
        self.assertEqual((left.state, right.state), (0, 0))
        self.assertEqual(self.state.sensor_states()["left"]["state"], [1, 0])
        self.assertEqual(self.state.sensor_states()["right"]["state"], [1, 0])
        self.assertEqual(self.state.sim_state()["avatar_position"], (-100.0, 100.0))

    def test_weight_activates_nearby_and_releases_when_avatar_moves(self):
        weight = make_sensor("seat", "Weight", x=10, y=10)
        runtime = sources(sensors=[weight])
        self.click(10, 10, runtime)
        self.click(20, 10, runtime)
        self.assertEqual(weight.state, 0)
        self.assertEqual(self.state.sensor_states()["seat"]["state"], [1, 0])

    def test_clicking_device_requires_a_visible_motion_or_temperature_sensor(self):
        for sensor_type in ("PIR", "Temperature"):
            for blocked in (False, True):
                with self.subTest(sensor_type=sensor_type, blocked=blocked):
                    device = make_device(state=0, x=20)
                    observing_sensor = make_sensor("observer", sensor_type, state=22 if sensor_type == "Temperature" else 0)
                    runtime = sources(
                        sensors=[observing_sensor], devices=[device],
                        walls=[Wall(10, -50, 10, 50)] if blocked else [],
                    )
                    self.click(20, 0, runtime)
                    self.assertEqual(device.state, 0 if blocked else 1)
                    self.assertEqual("oven" in self.state.active_cycles(), not blocked)
                    self.state = HouseState()

    def test_clicking_door_synchronizes_switch_and_preserves_same_minute_edges(self):
        door = Door(0, 0, 20, 0, "close")
        switch = make_sensor("door", "Switch", x=10)
        runtime = sources(sensors=[switch], doors=[door])
        with patch("builtins.print"):
            self.click(10, 0, runtime)
            self.assertEqual((door.state, switch.state), ("open", 1))
            self.click(10, 0, runtime)
        self.assertEqual((door.state, switch.state), ("close", 0))
        self.assertEqual(self.state.sensor_states()["door"]["state"], [1, 0])
        self.assertEqual(self.state.sensor_states()["door"]["time"], ["2026-02-28 12:00"] * 2)

    def test_paused_interaction_does_not_move_avatar_or_change_sensors(self):
        self.timer.is_running = False
        weight = make_sensor("seat", "Weight")
        with patch("builtins.print"):
            self.click(0, 0, sources(sensors=[weight]))
        self.assertEqual(weight.state, 0)
        self.assertIsNone(self.state.sim_state()["avatar_position"])
        self.assertEqual(self.canvas.draws, [])

    def test_toggle_starts_a_cycle_with_llm_metadata_and_stops_immediately(self):
        oven = make_device(state=0)
        meter = make_sensor("meter", "Smart Meter", device="oven")
        runtime = sources(sensors=[meter], devices=[oven])
        metadata = {"cycle_id": 7, "duration_minutes": 10}
        with patch.object(sim, "prime_llm_cycle_for_sensor", return_value=metadata) as prime:
            sim.toggle_device_state(None, None, self.state, self.timer, runtime,
                                    0, 0, render=False)
        self.assertEqual(oven.state, 1)
        self.assertEqual(oven.current_consumption, 100)
        self.assertEqual(self.state.active_cycles()["oven"], {
            "start_time": datetime(2026, 2, 28, 12), "cycle_type": "Oven", "llm": metadata,
        })
        prime.assert_called_once_with("meter", "Oven", datetime(2026, 2, 28, 12),
                                      associated_device="oven", force_new_prediction=True)
        sim.toggle_device_state(None, None, self.state, self.timer, runtime,
                                0, 0, render=False)
        self.assertEqual(oven.state, 0)
        self.assertEqual(oven.current_consumption, 0)
        self.assertEqual(self.state.active_cycles(), {})

    def test_toggle_ignores_distant_devices_and_requires_click_coordinates(self):
        oven = make_device(state=0, x=20)
        runtime = sources(devices=[oven])
        sim.toggle_device_state(None, None, self.state, self.timer, runtime,
                                0, 0, render=False)
        self.assertEqual(oven.state, 0)
        self.assertEqual(self.state.active_cycles(), {})
        with self.assertRaisesRegex(ValueError, "coordinates or an event"):
            sim.toggle_device_state(None, None, self.state, self.timer, runtime)

    def test_simulation_start_and_stop_helpers_preserve_a_paused_session(self):
        self.timer.elapsed_time = timedelta(seconds=5)
        with patch("builtins.print"), patch.object(sim, "initialize_avatar_image", return_value=object()):
            sim.stop_simulation(self.timer)
            self.assertFalse(self.timer.is_running)
            self.assertEqual(self.timer.elapsed_time, timedelta(seconds=5))
            sim.start_simulation(self.canvas, self.timer, None, self.state, sources())
        self.assertTrue(self.timer.is_running)
        self.assertEqual(self.timer.elapsed_time, timedelta(seconds=5))


class DeviceCycleLifecycleTests(unittest.TestCase):
    def test_noncontinuous_devices_stop_at_the_profile_endpoint(self):
        for device_type in ("Washing_Machine", "Dishwasher", "Coffee_Machine", "Toaster"):
            with self.subTest(device_type=device_type):
                started = datetime(2026, 2, 28, 12)
                timer = make_timer(start=started)
                duration = max(consumption_profiles.consumption_profiles[device_type]["profile"])
                timer.elapsed_time = timedelta(seconds=duration)
                device = make_device("appliance", device_type)
                cycles = {"appliance": {"start_time": started, "cycle_type": device_type}}
                update_devices_consumption(None, [device], 1, timer, cycles)
                self.assertEqual(device.state, 0)
                self.assertEqual(device.current_consumption, 0)
                self.assertEqual(cycles, {})

    def test_llm_duration_controls_auto_stop_with_legacy_cycles_still_supported(self):
        started = datetime(2026, 2, 28, 12)
        for entry in (
            {"start_time": started, "cycle_type": "Coffee_Machine",
             "llm": {"duration_minutes": 10}},
            (started, "Coffee_Machine"),
        ):
            with self.subTest(entry=entry):
                timer = make_timer(start=started)
                timer.elapsed_time = timedelta(seconds=1)
                device = make_device("coffee", "Coffee_Machine")
                cycles = {"coffee": entry}
                with patch("consumption_profiles.add_noise", side_effect=float):
                    update_devices_consumption(None, [device], 1, timer, cycles)
                self.assertEqual(device.state, 1)
                timer.elapsed_time = timedelta(seconds=10 if isinstance(entry, dict) else 2)
                update_devices_consumption(None, [device], 1, timer, cycles)
                self.assertEqual(device.state, 0)
                self.assertEqual(cycles, {})

    def test_continuous_devices_remain_on_after_a_profile_finishes(self):
        started = datetime(2026, 2, 28, 12)
        for device_type in ("Fridge", "Computer", "Oven", "Microwave", "TV", "PlayStation"):
            with self.subTest(device_type=device_type):
                timer = make_timer(start=started)
                timer.elapsed_time = timedelta(seconds=400)
                device = make_device("continuous", device_type)
                cycles = {"continuous": {"start_time": started, "cycle_type": device_type}}
                with patch("consumption_profiles.add_noise", side_effect=float):
                    update_devices_consumption(None, [device], 1, timer, cycles)
                self.assertEqual(device.state, 1)
                self.assertGreater(device.current_consumption, 0)
                self.assertIn("continuous", cycles)

    def test_off_devices_are_zero_and_idle_devices_use_standby(self):
        off = make_device("off", "Computer", state=0)
        off.current_consumption = 100
        idle = make_device("idle", "Computer")
        update_devices_consumption(None, [off, idle], 1,
                                   make_timer(start=datetime(2026, 2, 28, 12)), {})
        self.assertEqual(off.current_consumption, 0)
        self.assertEqual(idle.current_consumption, 40)


class HouseStateIsolationTests(unittest.TestCase):
    def test_independent_runs_do_not_share_any_runtime_store(self):
        first, second = HouseState(), HouseState()
        stores = ("sensor_states", "active_cycles", "sim_state", "activity_state",
                  "activity_log_state", "interaction_log_state", "automatic_state")
        for name in stores:
            getattr(first, name)()["sentinel"] = name
            self.assertEqual(getattr(second, name)(), {})
        first.set_context(devices=["first"])
        self.assertEqual(second.runtime(), {})

    def test_replace_runtime_discards_stale_values_without_erasing_sensor_history(self):
        state = HouseState(initial_context={"old": 1})
        state.sensor_states()["pir"] = {"state": [1]}
        state.replace_runtime(delta_seconds=2)
        self.assertEqual(state.runtime(), {"delta_seconds": 2})
        self.assertIs(state.context, state.runtime())
        self.assertEqual(state.sensor_states()["pir"]["state"], [1])

    def test_invalid_stores_recover_to_mutable_independent_dictionaries(self):
        state = HouseState(initial_values={"runtime": None, "active_cycles": []})
        self.assertEqual(state.runtime(), {})
        self.assertEqual(state.active_cycles(), {})
        self.assertIs(state.context, state.runtime())
        state.values["runtime"] = None
        self.assertEqual(state.runtime(), {})
        self.assertIs(state.context, state.runtime())


if __name__ == "__main__":
    unittest.main()
