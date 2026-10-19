"""Opt-in real Tk smoke checks; run with SIMULATOR_GUI_TESTS=1.

Most checks keep the root withdrawn; mouse and keyboard checks briefly map it.
No credential, hardware, file-picker, or message-box
interaction is allowed, and all mutable files live in a temporary directory.
On Linux, run the opted-in suite under xvfb-run when no display is available.
"""

import copy
import os
import sys
import tempfile
import time
import tkinter as tk
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import device
import door
import log
import point
import read
import recognition_library
import sensor
import wall
from app import preferences, save_paths
from app.context import AppContext
from app.io import scenario
from app.io import safe_dialog
from app.io.save_dialog import SavePathDialog
from app.ui.main_ui import build_home_ui
from app.ui.rooms import open_rooms_ui
from app.ui import device_inspector as inspector
from app.ui.device_inspector import (
    MapSelection, set_light_control, _append_pasted_object, _redraw_editable_objects,
    _set_selected_objects,
)
from models import Device, Sensor
from sim import interaction, render_simulation_state
from canvas_zoom import to_canvas
from lighting import light_switch_position


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
        self.stack.enter_context(patch.object(recognition_library, "LIBRARY_DIR", self.root / "recognition_library"))
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

    def test_save_as_tk_chooser_confirmation_and_reload_leave_application_running(self):
        self.load_home()
        self.window.geometry("1000x700")
        self.window.deiconify()
        self.pump()
        self.ctx.preferences["confirm_save_scenario"] = True
        light = Device("lamp", 100, 100, "Smart Light", 7.5, 0, 7.5, 7.5)
        self.ctx.read_devices.append(light)
        _redraw_editable_objects(self.ctx)
        light.light_switches[0].x, light.light_switches[0].y = 25, 150
        destination = self.root / "saved home.csv"
        original_choose = SavePathDialog.choose
        confirmed = []

        def find_button(parent, label):
            for child in parent.winfo_children():
                if isinstance(child, tk.Button) and child.cget("text") == label:
                    return child
                result = find_button(child, label)
                if result is not None:
                    return result
            return None

        def confirm_save():
            dialog = next((child for child in self.window.winfo_children()
                           if isinstance(child, tk.Toplevel) and child.title() == "Save"), None)
            if dialog is None:
                self.window.after(10, confirm_save)
                return
            confirmed.append(True)
            find_button(dialog, "Yes").invoke()

        def complete_chooser(dialog, *args, **kwargs):
            def save():
                dialog.set_selection("saved home")
                self.window.after_idle(confirm_save)
                dialog.ok_button.invoke()

            self.window.after(100, save)
            return original_choose(dialog, *args, **kwargs)

        with patch.object(safe_dialog.sys, "platform", "darwin"), \
                patch.object(SavePathDialog, "choose", new=complete_chooser), \
                patch.object(scenario, "ensure_houses_dir", return_value=self.root), \
                patch("tkinter.filedialog.asksaveasfilename", side_effect=AssertionError("Native save panel must not be used")), \
                patch.object(safe_dialog, "_run_helper", side_effect=AssertionError("Native helper must not be used")), \
                patch.object(self.window, "quit", side_effect=AssertionError("Saving must not quit the application")), \
                patch.object(scenario.messagebox, "showinfo") as info:
            self.ctx.file_menu.invoke("Save As")
            self.pump()
            self.assertEqual(confirmed, [True])
            self.assertEqual(self.ctx.current_file, str(destination))
            self.assertTrue(destination.is_file())
            self.assertTrue(self.window.winfo_exists())
            info.assert_called_once()
        self.assertTrue(scenario.load_scenario_from_path(self.ctx, self.ctx.canvas, destination))
        self.pump()
        loaded = next(device for device in self.ctx.read_devices if device.name == "lamp")
        self.assertEqual((loaded.light_switches[0].x, loaded.light_switches[0].y), (25, 150))

        def cancel_chooser(dialog, *args, **kwargs):
            self.window.after(100, dialog.cancel_command)
            return original_choose(dialog, *args, **kwargs)

        with patch.object(safe_dialog.sys, "platform", "darwin"), \
                patch.object(SavePathDialog, "choose", new=cancel_chooser), \
                patch.object(scenario, "ensure_houses_dir", return_value=self.root), \
                patch.object(self.window, "quit", side_effect=AssertionError("Cancel must not quit the application")):
            self.ctx.file_menu.invoke("Save As")
            self.pump()
        self.assertEqual(self.ctx.current_file, str(destination))
        alive = []
        self.window.after_idle(lambda: alive.append(True))
        self.pump()
        self.assertEqual(alive, [True])
        self.window.withdraw()

    def test_save_menu_actions_return_before_opening_modal_windows(self):
        # A direct Menu.invoke() alone misses the macOS menu-tracking loop.
        # No save dialog/confirmation may start on the menu callback's stack.
        for label, action in (("Save", "save_scenario"), ("Save As", "save_scenario_as")):
            with self.subTest(label=label), patch(f"app.ui.main_ui.{action}") as save:
                self.ctx.file_menu.invoke(label)
                save.assert_not_called()
                self.pump()
                save.assert_called_once_with(self.ctx)

    def test_delete_undo_redo_buttons_and_native_shortcuts_restore_selected_device(self):
        self.load_home()
        # Mouse/key events need a mapped target; this check briefly shows the window.
        self.window.geometry("1000x700")
        self.window.deiconify()
        self.pump()
        device = self.ctx.read_devices[0]
        _set_selected_objects(self.ctx, [MapSelection("device", device)])
        self.ctx.preferences["confirm_delete_device"] = False
        palette = self.ctx._tool_palette
        for key in ("delete", "undo", "redo"):
            self.assertTrue(palette._buttons[key].find_all(), f"{key} must have a visible icon")
        palette._buttons["delete"].event_generate("<Button-1>", x=17, y=17)
        self.pump()
        self.assertEqual(self.ctx.read_devices, [])
        palette._buttons["undo"].event_generate("<Button-1>", x=17, y=17)
        self.pump()
        self.assertIs(self.ctx.read_devices[0], device)
        palette._buttons["redo"].event_generate("<Button-1>", x=17, y=17)
        self.pump()
        self.assertEqual(self.ctx.read_devices, [])
        palette._buttons["undo"].event_generate("<Button-1>", x=17, y=17)
        self.pump()
        self.assertIs(self.ctx.read_devices[0], device)

        canvas = self.ctx.canvas
        delete_keys = ("<Command-BackSpace>", "<Command-Delete>", "<BackSpace>") if sys.platform == "darwin" else ("<Delete>",)
        undo_key = "<Command-z>" if sys.platform == "darwin" else "<Control-z>"
        redo_keys = (("<Command-Shift-z>", "<Command-Shift-Z>") if sys.platform == "darwin"
                     else ("<Control-Shift-z>", "<Control-Shift-Z>", "<Control-y>"))
        for delete_key in delete_keys:
            canvas.focus_force()
            self.pump()
            canvas.event_generate(delete_key)
            self.pump()
            self.assertEqual(self.ctx.read_devices, [], delete_key)
            canvas.event_generate(undo_key)
            self.pump()
            self.assertIs(self.ctx.read_devices[0], device)
        for redo_key in redo_keys:
            canvas.event_generate(redo_key)
            self.pump()
            self.assertEqual(self.ctx.read_devices, [], redo_key)
            canvas.event_generate(undo_key)
            self.pump()
            self.assertIs(self.ctx.read_devices[0], device)

        entry = tk.Entry(self.ctx.timer_frame)
        entry.pack()
        entry.insert(0, "editable text")
        entry.focus_force()
        self.pump()
        entry.event_generate(delete_keys[0])
        entry.event_generate(redo_keys[0])
        self.pump()
        self.assertIs(self.ctx.read_devices[0], device, "text editing must not delete map objects")
        self.assertTrue(self.ctx._movement_redo_stack)
        self.load_home()
        self.assertEqual(self.ctx._movement_undo_stack, [])
        self.assertEqual(self.ctx._movement_redo_stack, [])
        self.window.withdraw()

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

    def test_lighting_dialog_shows_configurable_watts(self):
        frame = tk.Frame(self.window)
        dialog = device.DeviceDialog.__new__(device.DeviceDialog)
        dialog.body(frame)
        self.assertIn("Normal Light", dialog.device_type["values"])
        self.assertIn("Smart Light", dialog.device_type["values"])
        self.assertEqual(dialog.lighting_power.get(), "10")
        self.assertEqual(dialog.lighting_power.winfo_manager(), "")
        dialog.device_type.set("Normal Light")
        dialog.device_type.event_generate("<<ComboboxSelected>>")
        self.pump()
        self.assertEqual(dialog.lighting_power.winfo_manager(), "grid")
        self.assertIn("separate Smart Meter", dialog.lighting_meter_label.cget("text"))
        dialog.device_type.set("Smart Light")
        dialog.device_type.event_generate("<<ComboboxSelected>>")
        self.pump()
        self.assertEqual(dialog.lighting_power.winfo_manager(), "grid")
        self.assertIn("integrated", dialog.lighting_meter_label.cget("text"))
        self.assertEqual(dialog.lighting_control.get(), "Manual")
        self.assertEqual(dialog.lighting_control.winfo_manager(), "grid")
        dialog.lighting_control.set("Automatic")
        dialog.device_name.insert(0, "automatic light")
        self.assertTrue(dialog.validate())
        dialog.apply()
        self.assertEqual(dialog.result[-1], "automatic")
        dialog.device_type.set("Oven")
        dialog.device_type.event_generate("<<ComboboxSelected>>")
        self.pump()
        self.assertEqual(dialog.lighting_power.winfo_manager(), "")
        self.assertEqual(dialog.lighting_control.winfo_manager(), "")
        frame.destroy()

    def test_properties_rules_and_new_device_sensor_dialogs_save_and_reload(self):
        from app.ui.activity_properties import open_properties_editor
        from activity_recognition import recognize_activities

        self.load_home()
        self.window.geometry("1000x700")
        self.window.deiconify()
        self.pump()
        oven = self.ctx.read_devices[0]

        def button(parent, text):
            for child in parent.winfo_children():
                if isinstance(child, tk.Button) and child.cget("text") == text:
                    return child
                result = button(child, text)
                if result is not None:
                    return result
            return None

        editor = open_properties_editor(self.ctx, oven, "device")
        editor._property_entry.delete(0, tk.END)
        editor._property_entry.insert(0, "cooking=-1")
        button(editor, "Apply").invoke()
        self.pump()
        self.assertTrue(editor.winfo_exists())
        self.assertEqual(oven.properties["cooking"], 1)
        editor._property_entry.delete(0, tk.END)
        editor._property_entry.insert(0, "cooking=0.9, working=0.2")
        button(editor, "Apply").invoke()
        self.pump()
        self.assertEqual(oven.properties, {"cooking": .9, "working": .2})

        self.ctx.scenario_menu.invoke("Activity recognition…")
        self.pump()
        window = self.ctx._recognition_window
        entries = window._recognition_entries
        entries["name"].set("cooking")
        entries["name"].event_generate("<<ComboboxSelected>>")
        self.pump()
        entries["threshold"].delete(0, tk.END)
        entries["threshold"].insert(0, ".7")
        button(window, "Apply rule and timing").invoke()
        self.pump()
        self.assertEqual(self.ctx.recognition_config["rules"]["cooking"]["threshold"], .7)
        self.assertIn("taking a shower", window._recognition_tree.get_children())

        runtime = {"devices": [oven], "sensors": [], "recognition_config": self.ctx.recognition_config}
        recognize_activities({"m": {"type": "Smart Meter", "associated_device": oven.name, "state": [1]}},
                             runtime, self.ctx.house_state.activity_state(), 0)
        deadline = time.monotonic() + 1
        while window._recognition_tree.set("cooking", "score") == "—" and time.monotonic() < deadline:
            self.pump()
            time.sleep(.01)
        self.assertEqual(window._recognition_tree.set("cooking", "status"), "Recognized")
        button(window, "Close").invoke()

        frame = tk.Frame(self.window)
        dialog = device.DeviceDialog.__new__(device.DeviceDialog)
        dialog.body(frame)
        for kind in ("Microwave", "TV", "PlayStation", "Bed", "Shower", "Toilet"):
            self.assertIn(kind, dialog.device_type["values"])
        dialog.device_type.set("Microwave")
        dialog.device_type.event_generate("<<ComboboxSelected>>")
        self.pump()
        self.assertIn("cooking=1", dialog.property_weights.get())
        frame.destroy()

        frame = tk.Frame(self.window)
        dialog = sensor.SensorDialog.__new__(sensor.SensorDialog)
        dialog._preloaded_device_names = [oven.name]
        dialog.body(frame)
        dialog.sensor_type.set("Water Flow")
        dialog.sensor_type.event_generate("<<ComboboxSelected>>")
        self.pump()
        self.assertEqual(dialog.property_weights.get(), "water_flow=1")
        dialog.associated_device_combobox.set(oven.name)
        dialog.sensor_name.insert(0, "fixture flow")
        self.assertTrue(dialog.validate())
        dialog.apply()
        self.assertEqual(dialog.result[-1], oven.name)
        frame.destroy()

        path = self.root / "properties.csv"
        with patch.object(scenario.messagebox, "showinfo"):
            self.assertTrue(scenario._write_scenario(self.ctx, path))
        self.assertTrue(scenario.load_scenario_from_path(self.ctx, self.ctx.canvas, path))
        self.pump()
        self.assertEqual(self.ctx.read_devices[0].properties, {"cooking": .9, "working": .2})
        self.assertEqual(self.ctx.recognition_config["rules"]["cooking"]["threshold"], .7)
        self.window.withdraw()

    def test_shared_library_profiles_transfer_ground_truth_and_scenario_round_trip(self):
        from app.ui.activity_properties import open_properties_editor
        from recognition_evaluation import capture_frame, record_trace

        self.load_home()
        self.window.deiconify()
        self.ctx.scenario_menu.invoke("Shared recognition library…")
        self.pump()
        window = self.ctx._recognition_library_window

        def button(parent, text):
            for child in parent.winfo_children():
                if isinstance(child, tk.Button) and child.cget("text") == text:
                    return child
                found = button(child, text)
                if found is not None:
                    return found
            return None

        def invoke(text, parent=window):
            widget = button(parent, text)
            self.assertIsNotNone(widget, text)
            widget.invoke()
            self.pump()

        def replace(entry, text):
            entry.delete(0, tk.END)
            entry.insert(0, text)

        replace(window._house_name, "Reference house")
        invoke("Connect shared model")
        invoke("Set as reference house")
        reference_id = self.ctx.recognition_library_link["house_id"]
        oven = self.ctx.read_devices[0]
        oven.state = 1
        state = self.ctx.house_state.activity_state()
        observations = {"motion": {"state": [1]}}
        record_trace(state, capture_frame(recognition_library.context_runtime(self.ctx), observations, 0))
        labels = window._ground_truth_labels
        labels.selection_set(list(labels.get(0, tk.END)).index("cooking"))
        window._library_tabs.select(2)
        invoke("Record human-labelled frame")
        invoke("Evaluate current shared model on held-out houses")
        self.assertIn("accuracy: N/A", window._metric_summary.get())
        self.assertIn("1 reference runs excluded", window._metric_summary.get())

        state.clear()
        replace(window._house_name, "Target house")
        invoke("Register as a new house")
        self.assertNotEqual(self.ctx.recognition_library_link["house_id"], reference_id)
        oven.type = "Microwave"
        editor = open_properties_editor(self.ctx, oven, "device")
        editor._property_profile_choice.set("device:Microwave")
        editor._property_profile_choice.event_generate("<<ComboboxSelected>>")
        invoke("Apply", parent=editor)
        self.assertEqual(oven.recognition_profile, "device:Microwave")
        window._library_tabs.select(1)
        invoke("Compare properties and sensor coverage")
        self.assertEqual(window._transfer_tree.set("cooking", "coverage"), "100%")
        self.assertEqual(window._transfer_tree.set("cooking", "ready"), "Yes")
        record_trace(state, capture_frame(recognition_library.context_runtime(self.ctx), observations, 0))
        window._library_tabs.select(2)
        invoke("Record human-labelled frame")
        invoke("Evaluate current shared model on held-out houses")
        self.assertIn("accuracy: 100.0%", window._metric_summary.get())
        self.assertIn("1 labelled frames, 1 held-out houses", window._metric_summary.get())
        evaluate_button = button(window, "Evaluate current shared model on held-out houses")
        self.assertTrue(evaluate_button.winfo_ismapped())
        self.assertLess(evaluate_button.winfo_rooty() + evaluate_button.winfo_height(), window.winfo_rooty() + window.winfo_height())

        window._library_tabs.select(0)
        replace(window._shared_profile_weights, "cooking=0.2, heating=0.8")
        invoke("Save shared profile")
        self.assertIn("stale", window._metric_summary.get())
        self.assertEqual(window._transfer_tree.get_children(), ())
        window._library_tabs.select(2)
        invoke("Evaluate current shared model on held-out houses")
        self.assertIn("accuracy: 0.0%", window._metric_summary.get())
        window._library_tabs.select(0)
        fields = window._shared_rule_fields
        fields["name"].set("custom relaxation")
        replace(fields["weights"], "relaxing=1")
        invoke("Save shared rule")
        self.assertIn("custom relaxation", window._ground_truth_labels.get(0, tk.END))

        destination = self.root / "shared-house.csv"
        with patch.object(scenario.messagebox, "showinfo"):
            self.assertTrue(scenario._write_scenario(self.ctx, destination))
        self.assertTrue(scenario.load_scenario_from_path(self.ctx, self.ctx.canvas, destination))
        self.pump()
        self.assertTrue(self.ctx.recognition_library_link["enabled"])
        self.assertEqual(self.ctx.read_devices[0].recognition_profile, "device:Microwave")
        self.assertEqual(self.ctx.read_devices[0].properties["cooking"], .2)
        invoke("Close")

    def check_light_only_manual_simulation(self, light_type):
        content = SMOKE_SCENARIO.replace("motion,50,50,PIR,0,1,1,0,90,None,None\n", "")
        content = content.replace("oven,100,100,Oven,1000,0,100,1000,0,1",
                                  f"lamp,100,100,{light_type},7.5,0,7.5,7.5,0,1")
        path = self.root / "lights-only.csv"
        path.write_text(content, encoding="utf-8")
        self.assertTrue(scenario.load_scenario_from_path(self.ctx, self.ctx.canvas, path))
        self.ctx.simulation_menu.invoke("Manual")
        self.pump()
        timer = self.ctx.timer_app_instance
        timer.start_stop_button.invoke()
        self.pump()
        light = self.ctx.read_devices[0]
        x, y = to_canvas(self.ctx.canvas, *light_switch_position(light))
        event = SimpleNamespace(x=x - self.ctx.canvas.canvasx(0), y=y - self.ctx.canvas.canvasy(0))
        interaction(self.ctx.canvas, timer, event, self.ctx.activity_label,
                    self.ctx.house_state, self.ctx._manual_runtime_sources)
        timer.advance_button.invoke()
        self.pump()
        self.assertEqual(self.ctx.read_devices[0].state, 1)
        self.assertEqual(self.ctx.read_devices[0].current_consumption, 7.5)
        self.assertEqual(self.ctx.canvas.itemcget("light_switch_label:lamp", "text"), "Switch: ON")
        self.assertEqual(self.ctx.read_sensors, [])
        if light_type == "Smart Light":
            self.assertEqual(self.ctx.rooms[0].total_power_w, 7.5)
            self.assertEqual(self.ctx.house_state.sensor_states()["smartmeter_lamp"]["consumption"][-1], 7.5)
        else:
            self.assertEqual(self.ctx.rooms[0].smartmeter_names, [])
            self.assertEqual(self.ctx.rooms[0].total_power_w, 0)
        interaction(self.ctx.canvas, timer, event, self.ctx.activity_label,
                    self.ctx.house_state, self.ctx._manual_runtime_sources)
        self.pump()
        self.assertEqual(light.state, 0)
        self.assertEqual(self.ctx.canvas.itemcget("light_switch_label:lamp", "text"), "Switch: OFF")

    def test_smart_light_operates_with_no_separate_sensors(self):
        self.check_light_only_manual_simulation("Smart Light")

    def test_normal_light_operates_without_a_meter(self):
        self.check_light_only_manual_simulation("Normal Light")

    def test_automatic_light_follows_room_presence_and_control_change_adds_switch(self):
        content = SMOKE_SCENARIO.replace("motion,50,50,PIR,0,1,1,0,90,None,None\n", "")
        content = content.replace("oven,100,100,Oven,1000,0,100,1000,0,1",
                                  "lamp,100,100,Smart Light,7.5,0,7.5,7.5,0,1,automatic")
        path = self.root / "automatic-light.csv"
        path.write_text(content, encoding="utf-8")
        self.assertTrue(scenario.load_scenario_from_path(self.ctx, self.ctx.canvas, path))
        self.assertFalse(self.ctx.canvas.find_withtag("light_switch"))
        self.ctx.simulation_menu.invoke("Manual")
        self.pump()
        timer = self.ctx.timer_app_instance
        timer.start_stop_button.invoke()
        self.pump()
        light = self.ctx.read_devices[0]
        runtime = self.ctx._manual_runtime_sources
        for position, expected in (((50, 150), 1), ((250, 150), 0)):
            x, y = to_canvas(self.ctx.canvas, *position)
            event = SimpleNamespace(x=x - self.ctx.canvas.canvasx(0), y=y - self.ctx.canvas.canvasy(0))
            interaction(self.ctx.canvas, timer, event, self.ctx.activity_label, self.ctx.house_state, runtime)
            timer.advance_button.invoke()
            self.pump()
            self.assertEqual(light.state, expected)
            self.assertEqual(self.ctx.rooms[0].total_power_w, expected * 7.5)
            self.assertFalse(self.ctx.canvas.find_withtag("light_switch"))

        set_light_control(self.ctx, light, "manual")
        self.pump()
        self.assertTrue(self.ctx.canvas.find_withtag("light_switch:lamp"))
        duplicate = copy.deepcopy(light)
        duplicate.x += 25
        _append_pasted_object(self.ctx, MapSelection("device", duplicate))
        _redraw_editable_objects(self.ctx)
        self.pump()
        self.assertTrue(self.ctx.canvas.find_withtag(f"light_switch:{duplicate.name}"))
        set_light_control(self.ctx, light, "automatic")
        self.pump()
        self.assertFalse(self.ctx.canvas.find_withtag("light_switch:lamp"))
        self.assertTrue(self.ctx.canvas.find_withtag(f"light_switch:{duplicate.name}"))

    def test_independent_switch_drag_double_click_details_and_switch_management(self):
        self.load_home()
        light = Device("lamp", 100, 100, "Smart Light", 7.5, 0, 7.5, 7.5)
        self.ctx.read_devices[:] = [light]
        self.ctx.preferences["confirm_delete_light_switch"] = False
        _redraw_editable_objects(self.ctx)
        self.window.geometry("1000x700")
        self.window.deiconify()
        self.pump()
        canvas = self.ctx.canvas

        def event_at(x, y):
            x, y = to_canvas(canvas, x, y)
            return SimpleNamespace(x=x - canvas.canvasx(0), y=y - canvas.canvasy(0))

        switch = light.light_switches[0]
        inspector.begin_selection_drag(self.ctx, event_at(switch.x, switch.y))
        self.assertEqual(self.ctx._selected_object.kind, "light_switch")
        inspector.finish_selection_drag(self.ctx, event_at(25, 150))
        self.pump()
        self.assertEqual((switch.x, switch.y, light.x, light.y), (25, 150, 100, 100))
        inspector.begin_selection_drag(self.ctx, event_at(light.x, light.y))
        inspector.finish_selection_drag(self.ctx, event_at(150, 100))
        self.pump()
        self.assertEqual((switch.x, switch.y, light.x, light.y), (25, 150, 150, 100))

        def double_click(timestamp):
            event = event_at(light.x, light.y)
            for sequence, delay in (("<ButtonPress-1>", 0), ("<ButtonRelease-1>", 10),
                                    ("<ButtonPress-1>", 100), ("<ButtonRelease-1>", 110)):
                canvas.event_generate(sequence, x=int(event.x), y=int(event.y), time=timestamp + delay)
                self.pump()

        def button(parent, text):
            for child in parent.winfo_children():
                if isinstance(child, tk.Button) and child.cget("text") == text:
                    return child
                found = button(child, text)
                if found is not None:
                    return found
            return None

        double_click(1000)
        details = self.ctx._light_details_windows[id(light)]
        self.assertEqual(details._light_info_values["Type"].get(), "Smart Light")
        self.assertEqual(details._light_info_values["Position"].get(), "(150, 100)")
        self.assertEqual(details._light_info_values["Metering"].get(), "Integrated Smart Meter")
        tree = details._light_switch_tree
        self.assertEqual(len(tree.get_children()), 1)
        button(details, "Add switch").invoke()
        self.pump()
        self.assertEqual(len(light.light_switches), 2)
        self.assertEqual(len(tree.get_children()), 2)
        button(details, "Remove selected switch").invoke()
        self.pump()
        self.assertEqual(light.light_switches, [switch])
        inspector.undo_last_movement(self.ctx)
        deadline = time.monotonic() + 1
        while len(tree.get_children()) != 2 and time.monotonic() < deadline:
            self.pump()
            time.sleep(0.01)
        self.assertEqual(len(tree.get_children()), 2)
        for switch_name in list(tree.get_children()):
            tree.selection_set(switch_name)
            self.pump()
            button(details, "Remove selected switch").invoke()
            self.pump()
        self.assertEqual(light.light_switches, [])
        self.assertEqual(self.ctx.read_devices, [light])
        details.destroy()
        self.ctx.simulation_menu.invoke("Manual")
        self.pump()
        self.ctx.timer_app_instance.start_stop_button.invoke()
        self.pump()
        double_click(2000)
        details = self.ctx._light_details_windows[id(light)]
        self.assertEqual(len(details._light_switch_tree.get_children()), 0)
        self.assertEqual(light.state, 0)
        details.destroy()
        self.window.withdraw()

    def test_room_map_and_window_show_live_meter_watts_and_cancel_refresh_on_close(self):
        self.load_home()
        self.ctx.read_devices.append(Device("lamp", 75, 75, "Lighting", 7.5, 1, 7.5, 7.5))
        meter = Sensor("light meter", 75, 75, "Smart Meter", 0, 5000, 1, 0,
                       consumption=7.5, associated_device="lamp")
        self.ctx.read_sensors.append(meter)
        open_rooms_ui(self.ctx)
        self.pump()
        room_window = next(child for child in self.window.winfo_children()
                           if isinstance(child, tk.Toplevel) and child.title() == "Room recognition")

        def labels(parent):
            result = []
            for child in parent.winfo_children():
                if isinstance(child, tk.Label):
                    result.append(child.cget("text"))
                result.extend(labels(child))
            return result

        self.assertIn("7.5 W", labels(room_window))
        meter.consumption = 12.5
        render_simulation_state(self.ctx.canvas, {
            "devices": self.ctx.read_devices, "sensors": self.ctx.read_sensors, "rooms": self.ctx.rooms,
        })
        room_text = self.ctx.canvas.itemcget(f"room_label:{self.ctx.rooms[0].room_id}", "text")
        self.assertIn("Power: 12.5 W", room_text)
        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            self.pump()
            time.sleep(0.01)
        self.assertIn("12.5 W", labels(room_window))
        jobs_before = set(self.window.tk.splitlist(self.window.tk.call("after", "info")))
        room_window.destroy()
        self.pump()
        jobs_after = set(self.window.tk.splitlist(self.window.tk.call("after", "info")))
        self.assertEqual(len(jobs_before - jobs_after), 1)


if __name__ == "__main__":
    unittest.main()
