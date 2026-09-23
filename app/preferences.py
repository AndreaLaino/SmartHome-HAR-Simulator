from __future__ import annotations

import json
from pathlib import Path

from canvas_zoom import MAX_ZOOM, MIN_ZOOM, get_zoom, set_zoom


PREFERENCES_PATH = Path(__file__).resolve().parents[1] / "settings.json"
CONFIRMATION_PREFERENCES = {
    "confirm_exit_app": "Close application",
    "confirm_delete_scenario": "Clear the current home",
    "confirm_delete_point": "Delete points",
    "confirm_delete_sensor": "Delete sensors",
    "confirm_delete_device": "Delete devices",
    "confirm_delete_wall": "Delete walls",
    "confirm_delete_door": "Delete doors",
    "confirm_save_scenario": "Save a scenario",
    "confirm_large_import": "Import more than 100 files",
}
DEFAULT_PREFERENCES = {
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
    **{key: True for key in CONFIRMATION_PREFERENCES},
}
PREFERENCE_CHOICES = {
    "start_time": {"midnight", "computer"},
    "starting_home": {"empty", "default", "custom"},
    "window_state": {"normal", "maximized", "fullscreen"},
}


def _is_valid_preference(key: str, value: object) -> bool:
    default = DEFAULT_PREFERENCES[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if key == "pir_fov_transparency":
        return type(value) is int and 0 <= value <= 100
    if key == "starting_home_path":
        return isinstance(value, str)
    if key == "canvas_zoom":
        return type(value) in (int, float) and MIN_ZOOM <= value <= MAX_ZOOM
    if key in {"canvas_view_x", "canvas_view_y"}:
        return type(value) in (int, float) and 0.0 <= value <= 1.0
    return (
        type(value) is type(default)
        and value in PREFERENCE_CHOICES.get(key, set())
    )


def load_preferences(path: str | Path | None = None) -> dict[str, object]:
    """Load personal UI preferences, falling back safely to defaults."""
    target = Path(path) if path is not None else PREFERENCES_PATH
    preferences = DEFAULT_PREFERENCES.copy()

    try:
        with target.open("r", encoding="utf-8") as stream:
            saved = json.load(stream)
    except FileNotFoundError:
        try:
            save_preferences(preferences, target)
        except OSError:
            pass
        return preferences
    except (OSError, json.JSONDecodeError, UnicodeError):
        return preferences

    if isinstance(saved, dict):
        for key, default in DEFAULT_PREFERENCES.items():
            value = saved.get(key, default)
            if _is_valid_preference(key, value):
                preferences[key] = value
    return preferences


def save_preferences(
    preferences: dict,
    path: str | Path | None = None,
) -> None:
    """Persist known personal preferences as a small JSON file."""
    target = Path(path) if path is not None else PREFERENCES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    saved = {}
    for key, default in DEFAULT_PREFERENCES.items():
        value = preferences.get(key, default)
        saved[key] = value if _is_valid_preference(key, value) else default
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(saved, stream, indent=2)
        stream.write("\n")
    temporary.replace(target)


def capture_view_preferences(ctx) -> None:
    """Capture the current window and canvas view into the preference store."""
    window_state = "normal"
    try:
        if bool(ctx.window.attributes("-fullscreen")):
            window_state = "fullscreen"
        elif str(ctx.window.state()).lower() == "zoomed":
            window_state = "maximized"
    except Exception:
        pass

    ctx.preferences["window_state"] = window_state
    if ctx.canvas is None:
        return
    ctx.preferences["canvas_zoom"] = get_zoom(ctx.canvas)
    try:
        ctx.preferences["canvas_view_x"] = float(ctx.canvas.xview()[0])
        ctx.preferences["canvas_view_y"] = float(ctx.canvas.yview()[0])
    except (IndexError, TypeError, ValueError):
        ctx.preferences["canvas_view_x"] = 0.0
        ctx.preferences["canvas_view_y"] = 0.0


def restore_window_preference(window, preferences: dict) -> None:
    """Restore normal, maximized, or fullscreen window state."""
    state = preferences.get("window_state", "normal")
    try:
        if state == "fullscreen":
            window.attributes("-fullscreen", True)
        elif state == "maximized":
            window.state("zoomed")
    except Exception:
        # Some Tk/window-manager combinations do not expose maximized state.
        pass


def restore_canvas_preferences(ctx) -> None:
    """Restore zoom and scroll position after startup content is available."""
    canvas = ctx.canvas
    if canvas is None:
        return
    try:
        zoom = float(ctx.preferences.get("canvas_zoom", 1.0))
        view_x = float(ctx.preferences.get("canvas_view_x", 0.0))
        view_y = float(ctx.preferences.get("canvas_view_y", 0.0))
    except (TypeError, ValueError):
        zoom, view_x, view_y = 1.0, 0.0, 0.0

    set_zoom(canvas, zoom, screen_x=0, screen_y=0)
    canvas.xview_moveto(max(0.0, min(1.0, view_x)))
    canvas.yview_moveto(max(0.0, min(1.0, view_y)))
    zoom_text = getattr(canvas, "_zoom_text", None)
    if zoom_text is not None:
        zoom_text.set(f"{round(get_zoom(canvas) * 100):.0f}%")
