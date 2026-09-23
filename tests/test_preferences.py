import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.preferences import (
    capture_view_preferences,
    load_preferences,
    restore_canvas_preferences,
    restore_window_preference,
    save_preferences,
)
from main import apply_startup_preferences


DEFAULTS = {
    "start_time": "computer",
    "starting_home": "empty",
    "starting_home_path": "",
    "start_manual_mode": False,
    "show_pir_fov": False,
    "pir_fov_transparency": 50,
    "window_state": "normal",
    "canvas_zoom": 1.0,
    "canvas_view_x": 0.0,
    "canvas_view_y": 0.0,
    "snap_to_grid": True,
    "show_grid": True,
    "show_room_types": True,
    "confirm_exit_app": True,
    "confirm_delete_scenario": True,
    "confirm_delete_point": True,
    "confirm_delete_sensor": True,
    "confirm_delete_device": True,
    "confirm_delete_wall": True,
    "confirm_delete_door": True,
    "confirm_save_scenario": True,
    "confirm_large_import": True,
}


class PreferenceTests(unittest.TestCase):
    def test_missing_file_is_created_with_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"

            preferences = load_preferences(path)

            self.assertEqual(preferences, DEFAULTS)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                preferences,
            )

    def test_saved_preference_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            save_preferences({"show_pir_fov": True}, path)

            preferences = load_preferences(path)
            self.assertTrue(preferences["show_pir_fov"])
            self.assertEqual(preferences["start_time"], "computer")
            self.assertEqual(preferences["starting_home"], "empty")

    def test_obsolete_theme_preference_is_ignored_and_removed_on_save(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            path.write_text(
                json.dumps({"theme_mode": "dark", "show_grid": False}),
                encoding="utf-8",
            )

            preferences = load_preferences(path)
            save_preferences(preferences, path)
            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertNotIn("theme_mode", preferences)
            self.assertNotIn("theme_mode", saved)
            self.assertFalse(saved["show_grid"])

    def test_startup_choices_are_saved_and_loaded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            save_preferences(
                {
                    "start_time": "midnight",
                    "starting_home": "default",
                    "starting_home_path": "C:/homes/example.csv",
                    "start_manual_mode": True,
                    "show_pir_fov": False,
                    "pir_fov_transparency": 75,
                    "window_state": "maximized",
                    "canvas_zoom": 1.5,
                    "canvas_view_x": 0.25,
                    "canvas_view_y": 0.4,
                    "snap_to_grid": False,
                    "show_grid": False,
                    "show_room_types": False,
                    "confirm_exit_app": False,
                    "confirm_delete_wall": False,
                },
                path,
            )

            preferences = load_preferences(path)
            self.assertEqual(preferences["start_time"], "midnight")
            self.assertEqual(preferences["starting_home"], "default")
            self.assertEqual(
                preferences["starting_home_path"],
                "C:/homes/example.csv",
            )
            self.assertTrue(preferences["start_manual_mode"])
            self.assertEqual(preferences["pir_fov_transparency"], 75)
            self.assertEqual(preferences["window_state"], "maximized")
            self.assertEqual(preferences["canvas_zoom"], 1.5)
            self.assertEqual(preferences["canvas_view_x"], 0.25)
            self.assertEqual(preferences["canvas_view_y"], 0.4)
            self.assertFalse(preferences["snap_to_grid"])
            self.assertFalse(preferences["show_grid"])
            self.assertFalse(preferences["show_room_types"])
            self.assertFalse(preferences["confirm_exit_app"])
            self.assertFalse(preferences["confirm_delete_wall"])

    def test_unknown_choices_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "start_time": "sunrise",
                        "starting_home": "last-opened",
                        "starting_home_path": ["not", "a", "path"],
                        "start_manual_mode": "yes",
                        "show_pir_fov": "yes",
                        "pir_fov_transparency": 142,
                        "window_state": "minimized",
                        "canvas_zoom": 8.0,
                        "canvas_view_x": -1.0,
                        "canvas_view_y": 2.0,
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(load_preferences(path), DEFAULTS)

    def test_invalid_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "settings.json"
            path.write_text("not json", encoding="utf-8")

            self.assertEqual(load_preferences(path), DEFAULTS)

    def test_default_home_is_loaded_only_when_selected(self):
        class Context:
            canvas = object()

        ctx = Context()
        with patch("app.io.scenario.load_scenario_from_file") as load_default:
            ctx.preferences = {"starting_home": "empty"}
            apply_startup_preferences(ctx)
            load_default.assert_not_called()

            ctx.preferences = {"starting_home": "default"}
            apply_startup_preferences(ctx)
            load_default.assert_called_once_with(ctx, ctx.canvas)

    def test_manual_mode_is_activated_on_startup_when_selected(self):
        class Context:
            canvas = object()
            preferences = {
                "starting_home": "empty",
                "start_manual_mode": True,
            }

        ctx = Context()
        with patch("app.controllers.simulation.start_sim") as start_manual:
            apply_startup_preferences(ctx)

        start_manual.assert_called_once_with(ctx)

    def test_custom_home_path_is_loaded_on_startup(self):
        class Context:
            canvas = object()
            preferences = {
                "starting_home": "custom",
                "starting_home_path": "C:/homes/my-home.csv",
                "start_manual_mode": False,
            }

        ctx = Context()
        with patch("app.io.scenario.load_scenario_from_path") as load_custom:
            apply_startup_preferences(ctx)

        load_custom.assert_called_once_with(
            ctx,
            ctx.canvas,
            "C:/homes/my-home.csv",
        )

    def test_view_state_is_captured_for_next_launch(self):
        class Window:
            def attributes(self, name):
                self.requested_attribute = name
                return False

            def state(self):
                return "zoomed"

        class Canvas:
            _zoom_factor = 1.5

            def xview(self):
                return (0.25, 0.75)

            def yview(self):
                return (0.4, 0.9)

        class Context:
            window = Window()
            canvas = Canvas()
            preferences = {}

        ctx = Context()
        capture_view_preferences(ctx)

        self.assertEqual(ctx.preferences["window_state"], "maximized")
        self.assertEqual(ctx.preferences["canvas_zoom"], 1.5)
        self.assertEqual(ctx.preferences["canvas_view_x"], 0.25)
        self.assertEqual(ctx.preferences["canvas_view_y"], 0.4)

    def test_true_fullscreen_state_is_captured(self):
        class Window:
            def attributes(self, _name):
                return True

            def state(self):
                return "normal"

        class Context:
            window = Window()
            canvas = None
            preferences = {}

        ctx = Context()
        capture_view_preferences(ctx)

        self.assertEqual(ctx.preferences["window_state"], "fullscreen")

    def test_window_and_canvas_view_are_restored(self):
        class Window:
            def __init__(self):
                self.states = []

            def state(self, value):
                self.states.append(value)

        window = Window()
        restore_window_preference(window, {"window_state": "maximized"})
        self.assertEqual(window.states, ["zoomed"])

        class ZoomText:
            def __init__(self):
                self.value = None

            def set(self, value):
                self.value = value

        class Canvas:
            _zoom_factor = 1.5

            def __init__(self):
                self._zoom_text = ZoomText()
                self.x_moves = []
                self.y_moves = []

            def xview_moveto(self, value):
                self.x_moves.append(value)

            def yview_moveto(self, value):
                self.y_moves.append(value)

        class Context:
            canvas = Canvas()
            preferences = {
                "canvas_zoom": 1.5,
                "canvas_view_x": 0.25,
                "canvas_view_y": 0.4,
            }

        ctx = Context()
        with patch("app.preferences.set_zoom") as set_canvas_zoom:
            restore_canvas_preferences(ctx)

        set_canvas_zoom.assert_called_once_with(
            ctx.canvas,
            1.5,
            screen_x=0,
            screen_y=0,
        )
        self.assertEqual(ctx.canvas.x_moves, [0.25])
        self.assertEqual(ctx.canvas.y_moves, [0.4])
        self.assertEqual(ctx.canvas._zoom_text.value, "150%")


if __name__ == "__main__":
    unittest.main()
