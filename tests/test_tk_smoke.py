"""Opt-in real Tk smoke checks; run with SIMULATOR_GUI_TESTS=1.

The root stays withdrawn. No credential, hardware, file-picker, or message-box
interaction is allowed, and all mutable files live in a temporary directory.
On Linux, run the opted-in suite under xvfb-run when no display is available.
"""

import os
import tempfile
import time
import tkinter as tk
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import device
import door
import log
import point
import read
import sensor
import wall
from app import preferences, save_paths
from app.context import AppContext
from app.io import scenario
from app.ui.main_ui import build_home_ui


GUI_ENABLED = os.environ.get("SIMULATOR_GUI_TESTS") == "1"

SMOKE_SCENARIO = """Positions
a,0,0
b,200,0
c,200,200
d,0,200

Walls
a,b
b,c
c,d
d,a

Sensors
motion,50,50,PIR,0,1,1,0,90,None,None

Devices
oven,100,100,Oven,1000,0,100,1000,0,1

Doors
50,0,100,0,close
"""


@unittest.skipUnless(GUI_ENABLED, "Set SIMULATOR_GUI_TESTS=1 to run real Tk smoke tests")
class TkSmokeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.window = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk display unavailable: {error}")
        self.window.withdraw()
        self.addCleanup(self.close_window)
        self.callback_errors = []
        self.window.report_callback_exception = lambda _type, error, _traceback: self.callback_errors.append(error)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(preferences, "PREFERENCES_PATH", self.root / "settings.json"))
        self.stack.enter_context(patch.object(save_paths, "SAVES_DIR", self.root / "saves"))
        self.stack.enter_context(patch.object(save_paths, "HOUSES_DIR", self.root / "houses"))
        self.stack.enter_context(patch.object(save_paths, "DEVICES_DIR", self.root / "devices"))
        self.stack.enter_context(patch.object(save_paths, "_current_session_dir", None))
        self.stack.enter_context(patch("app.ui.bindings.autostart_bound_ip_loggers"))
        self.stack.enter_context(patch("app.ui.bindings.autostart_bound_gpio_loggers"))
        self.stack.enter_context(patch.object(sensor, "_load_sensor_map", return_value={}))
        # Fail immediately if a callback accidentally attempts an external call
        # or a dialog. Catch callback exceptions below because Tk reports them
        # instead of propagating them through Button.invoke().
        self.stack.enter_context(patch("requests.sessions.Session.request", side_effect=AssertionError("Smoke tests cannot access the network")))
        for name in ("showinfo", "showwarning", "showerror", "askyesno", "askyesnocancel"):
            self.stack.enter_context(patch(f"tkinter.messagebox.{name}", side_effect=AssertionError("Smoke tests cannot open dialogs")))

        stores = [point.points, wall.walls, wall.walls_coordinates, sensor.sensors, device.devices, door.doors,
                  read.coordinates, read.read_walls, read.read_walls_coordinates, read.read_sensors, read.read_devices, read.read_doors]
        seen = set()
        for store in stores:
            if id(store) not in seen:
                seen.add(id(store))
                original = store[:]
                store.clear()
                self.addCleanup(lambda target=store, previous=original: target.__setitem__(slice(None), previous))
        old_rooms = read.read_room_overrides.copy()
        read.read_room_overrides.clear()
        self.addCleanup(lambda: read.read_room_overrides.update(old_rooms))
        settings = preferences.DEFAULT_PREFERENCES.copy()
        settings.update({"start_time": "midnight", "show_grid": False, "confirm_save_scenario": False})
        self.ctx = AppContext(window=self.window, preferences=settings)
        self.addCleanup(lambda: log.stop_interaction_log_session(self.ctx.house_state))
        build_home_ui(self.ctx)
        self.pump()

    def close_window(self):
        if not self.window.winfo_exists():
            return
        # Cancel callbacks explicitly so destroying multiple Tk interpreters
        # in one test process cannot leave stale Tcl command names behind.
        for job in self.window.tk.splitlist(self.window.tk.call("after", "info")):
            # Keep callback commands registered until their owning widgets are
            # destroyed. Root.after_cancel would remove a child-owned command
            # while leaving it in the child's cleanup list.
            self.window.tk.call("after", "cancel", job)
        self.window.destroy()

    def pump(self):
        self.window.update_idletasks()
        self.window.update()
        self.assertEqual(self.callback_errors, [], f"Tk callback errors: {self.callback_errors}")

    def load_home(self):
        path = self.root / "smoke-home.csv"
        path.write_text(SMOKE_SCENARIO, encoding="utf-8")
        self.assertTrue(scenario.load_scenario_from_path(self.ctx, self.ctx.canvas, path))
        self.pump()
        return path

    def test_main_ui_builds_real_widgets_and_settings_save_to_temporary_file(self):
        self.assertIsInstance(self.ctx.canvas, tk.Canvas)
        self.assertIsInstance(self.ctx.timer_frame, tk.Frame)
        self.assertIsInstance(self.ctx.scenario_menu, tk.Menu)
        self.assertEqual(self.ctx.simulation_menu.entrycget("Manual", "state"), "normal")
        self.assertTrue(self.ctx.canvas.bind("<Button-1>"))
        original_frame, original_canvas = self.ctx.home_frame, self.ctx.canvas
        build_home_ui(self.ctx)
        self.assertIs(self.ctx.home_frame, original_frame)
        self.assertIs(self.ctx.canvas, original_canvas)
        menubar = self.window.nametowidget(self.window.cget("menu"))
        settings = self.window.nametowidget(menubar.entrycget("Settings", "menu"))
        settings.invoke("Show grid")
        self.pump()
        self.assertTrue(self.ctx.canvas._show_grid)
        self.assertTrue(preferences.load_preferences(self.root / "settings.json")["show_grid"])

    def test_scenario_load_renders_real_canvas_items_and_survives_save_reload(self):
        path = self.load_home()
        self.assertEqual(len(self.ctx.canvas.find_withtag("point")), 4)
        self.assertEqual(len(self.ctx.canvas.find_withtag("wall")), 4)
        self.assertTrue(self.ctx.canvas.find_withtag("sensor"))
        self.assertTrue(self.ctx.canvas.find_withtag("device"))
        self.assertEqual(len(self.ctx.canvas.find_withtag("door")), 1)
        self.assertEqual(len(self.ctx.rooms), 1)
        saved = self.root / "round-trip.csv"
        # Only the informational message is suppressed for an authorized save.
        with patch("tkinter.messagebox.showinfo"):
            self.assertTrue(scenario._write_scenario(self.ctx, saved))
        self.assertTrue(scenario.load_scenario_from_path(self.ctx, self.ctx.canvas, saved))
        self.pump()
        self.assertEqual(len(self.ctx.canvas.find_withtag("wall")), 4)
        self.assertEqual(len(self.ctx.read_sensors), 1)
        self.assertTrue(os.path.isabs(self.ctx.current_file))
        self.assertTrue(os.path.samefile(self.ctx.current_file, saved))
        self.assertTrue(path.exists())

    def test_manual_buttons_start_pause_skip_resume_and_reset_real_timer(self):
        self.load_home()
        self.ctx.simulation_menu.invoke("Manual")
        self.pump()
        timer = self.ctx.timer_app_instance
        self.assertFalse(timer.is_running)
        self.assertEqual(timer.start_hour_entry.get(), "00:00")
        timer.start_stop_button.invoke()
        self.pump()
        self.assertTrue(timer.is_running)
        timer.advance_button.invoke()
        self.pump()
        self.assertEqual(timer.get_simulated_time(), "00:15")
        self.assertIn("motion", self.ctx.house_state.sensor_states())
        timer.start_stop_button.invoke()
        self.pump()
        self.assertFalse(timer.is_running)
        elapsed = timer.elapsed_time
        timer.advance_hour_button.invoke()
        self.assertEqual(timer.elapsed_time, elapsed)
        timer.start_stop_button.invoke()
        timer.advance_hour_button.invoke()
        self.pump()
        self.assertTrue(timer.is_running)
        self.assertEqual(timer.get_simulated_time(), "01:15")
        log_path = Path(self.ctx.house_state.interaction_log_state()["interaction_file_path"])
        self.assertTrue(log_path.is_relative_to(self.root))
        timer.reset_button.invoke()
        deadline = time.monotonic() + 2
        while self.ctx.timer_app_instance is not None and time.monotonic() < deadline:
            self.pump()
            time.sleep(0.01)
        self.assertIsNone(self.ctx.timer_app_instance)
        self.assertFalse(timer.is_running)
        self.assertEqual(timer.get_simulated_time(), "00:00")
        self.assertEqual(self.ctx._canvas_mode, "select")
        self.assertIsNone(self.ctx.house_state.interaction_log_state()["interaction_file"])
        self.assertTrue(log_path.is_file())


if __name__ == "__main__":
    unittest.main()
