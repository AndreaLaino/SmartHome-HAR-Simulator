from __future__ import annotations

import copy
import math
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox

from app.confirmations import ask_confirmation

from canvas_zoom import (
    event_to_logical,
    get_zoom,
    snap_logical_position,
    set_canvas_cursor,
    to_canvas,
    to_canvas_length,
)


SELECTION_TAG = "object_selection"
MARQUEE_TAG = "selection_marquee"
DRAG_PREVIEW_TAG = "drag_preview"
SEGMENT_PREVIEW_TAG = "segment_placement_preview"


@dataclass(frozen=True)
class MapSelection:
    kind: str
    value: object


def current_points(ctx) -> list:
    if ctx.load_active:
        return getattr(ctx, "r_points", [])
    from point import points

    return points


def current_devices(ctx) -> list:
    runtime_sources = getattr(ctx, "_manual_runtime_sources", None)
    if isinstance(runtime_sources, dict):
        return runtime_sources.get("devices", [])
    if ctx.load_active:
        return getattr(ctx, "read_devices", [])
    from device import devices

    return devices


def current_sensors(ctx) -> list:
    runtime_sources = getattr(ctx, "_manual_runtime_sources", None)
    if isinstance(runtime_sources, dict):
        return runtime_sources.get("sensors", [])
    if ctx.load_active:
        return getattr(ctx, "read_sensors", [])
    from sensor import sensors

    return sensors


def current_walls(ctx) -> list:
    if ctx.load_active:
        return getattr(ctx, "read_walls", [])
    from wall import walls_coordinates

    return walls_coordinates


def current_doors(ctx) -> list:
    runtime_sources = getattr(ctx, "_manual_runtime_sources", None)
    if isinstance(runtime_sources, dict):
        return runtime_sources.get("doors", [])
    if ctx.load_active:
        return getattr(ctx, "read_doors", [])
    from door import doors

    return doors


def associated_sensor_names(ctx, device) -> list[str]:
    return [
        sensor.name
        for sensor in current_sensors(ctx)
        if getattr(sensor, "associated_device", None) == device.name
    ]


def clear_selection(ctx) -> None:
    if ctx.canvas is not None:
        ctx.canvas.delete(SELECTION_TAG)
        ctx.canvas.delete(MARQUEE_TAG)
        ctx.canvas.delete(DRAG_PREVIEW_TAG)
        ctx.canvas.delete(SEGMENT_PREVIEW_TAG)
    ctx._selected_object = None
    ctx._selected_device = None
    ctx._selected_objects = []


def selected_objects(ctx) -> list[MapSelection]:
    selections = list(getattr(ctx, "_selected_objects", []) or [])
    primary = getattr(ctx, "_selected_object", None)
    if not selections and primary is not None:
        selections = [primary]
    return selections


def _set_selected_objects(ctx, selections: list[MapSelection]) -> None:
    selections = [selection for selection in selections if selection.value is not None]
    ctx._selected_objects = selections
    ctx._selected_object = selections[0] if selections else None
    ctx._selected_device = next(
        (
            selection.value
            for selection in selections
            if selection.kind == "device"
        ),
        None,
    )


def clear_device_selection(ctx) -> None:
    """Backward-compatible name used by older UI code/tests."""
    clear_selection(ctx)


def _distance_to_segment(px, py, x1, y1, x2, y2) -> float:
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    amount = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    amount = max(0.0, min(1.0, amount))
    nearest_x = x1 + amount * dx
    nearest_y = y1 + amount * dy
    return math.hypot(px - nearest_x, py - nearest_y)


def _nearest_marker(objects, x, y):
    if not objects:
        return None
    return min(objects, key=lambda item: math.hypot(item.x - x, item.y - y))


def _nearest_segment(objects, x, y):
    if not objects:
        return None
    return min(
        objects,
        key=lambda item: _distance_to_segment(
            x, y, item.x1, item.y1, item.x2, item.y2
        ),
    )


def _selection_at_event(ctx, event):
    canvas = ctx.canvas
    canvas_x = canvas.canvasx(event.x)
    canvas_y = canvas.canvasy(event.y)
    logical_x, logical_y = event_to_logical(canvas, event)
    item_ids = canvas.find_overlapping(
        canvas_x - 7,
        canvas_y - 7,
        canvas_x + 7,
        canvas_y + 7,
    )

    for item_id in reversed(item_ids):
        tags = set(canvas.gettags(item_id))
        if "device" in tags:
            value = _nearest_marker(current_devices(ctx), logical_x, logical_y)
            return MapSelection("device", value) if value is not None else None
        if "sensor" in tags:
            value = _nearest_marker(current_sensors(ctx), logical_x, logical_y)
            return MapSelection("sensor", value) if value is not None else None
        if "point" in tags:
            value = _nearest_marker(current_points(ctx), logical_x, logical_y)
            return MapSelection("point", value) if value is not None else None
        if "door" in tags:
            value = _nearest_segment(current_doors(ctx), logical_x, logical_y)
            return MapSelection("door", value) if value is not None else None
        if "wall" in tags:
            value = _nearest_segment(current_walls(ctx), logical_x, logical_y)
            return MapSelection("wall", value) if value is not None else None
    return None


def _selection_is_in(selection: MapSelection, selections: list[MapSelection]) -> bool:
    return any(
        candidate.kind == selection.kind and candidate.value is selection.value
        for candidate in selections
    )


def _selections_in_rectangle(ctx, x1: float, y1: float, x2: float, y2: float):
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    selections = []
    for kind, source in (
        ("point", current_points(ctx)),
        ("sensor", current_sensors(ctx)),
        ("device", current_devices(ctx)),
    ):
        selections.extend(
            MapSelection(kind, value)
            for value in source
            if left <= value.x <= right and top <= value.y <= bottom
        )
    for kind, source in (
        ("wall", current_walls(ctx)),
        ("door", current_doors(ctx)),
    ):
        selections.extend(
            MapSelection(kind, value)
            for value in source
            if max(min(value.x1, value.x2), left)
            <= min(max(value.x1, value.x2), right)
            and max(min(value.y1, value.y2), top)
            <= min(max(value.y1, value.y2), bottom)
        )
    return selections


def begin_marquee_selection(ctx, event):
    canvas_x = ctx.canvas.canvasx(event.x)
    canvas_y = ctx.canvas.canvasy(event.y)
    logical_x, logical_y = event_to_logical(ctx.canvas, event)
    ctx._marquee_state = {
        "canvas_start": (canvas_x, canvas_y),
        "logical_start": (logical_x, logical_y),
        "dragged": False,
    }
    ctx.canvas.focus_set()
    return "break"


def update_marquee_selection(ctx, event):
    state = getattr(ctx, "_marquee_state", None)
    if not state:
        return None
    canvas_x = ctx.canvas.canvasx(event.x)
    canvas_y = ctx.canvas.canvasy(event.y)
    start_x, start_y = state["canvas_start"]
    if math.hypot(canvas_x - start_x, canvas_y - start_y) < 4:
        return "break"
    state["dragged"] = True
    ctx.canvas.delete(MARQUEE_TAG)
    ctx.canvas.create_rectangle(
        start_x,
        start_y,
        canvas_x,
        canvas_y,
        outline="#1689ff",
        fill="#dceeff",
        stipple="gray25",
        width=1,
        tags=(MARQUEE_TAG,),
    )
    return "break"


def finish_marquee_selection(ctx, event):
    state = getattr(ctx, "_marquee_state", None)
    ctx._marquee_state = None
    ctx.canvas.delete(MARQUEE_TAG)
    if not state:
        return "break"
    if not state["dragged"]:
        return open_selection_menu_at_event(ctx, event)

    start_x, start_y = state["logical_start"]
    end_x, end_y = event_to_logical(ctx.canvas, event)
    selections = _selections_in_rectangle(ctx, start_x, start_y, end_x, end_y)
    _set_selected_objects(ctx, selections)
    _draw_selections(ctx, selections)
    return "break"


def _draw_selection_shape(ctx, selection: MapSelection) -> None:
    canvas = ctx.canvas
    value = selection.value
    if selection.kind in {"point", "sensor", "device"}:
        x, y = to_canvas(canvas, value.x, value.y)
        radius = max(9, to_canvas_length(canvas, 10))
        canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            outline="#1689ff",
            width=3,
            dash=(4, 2),
            tags=(SELECTION_TAG,),
        )
        return

    x1, y1 = to_canvas(canvas, value.x1, value.y1)
    x2, y2 = to_canvas(canvas, value.x2, value.y2)
    canvas.create_line(
        x1,
        y1,
        x2,
        y2,
        fill="#1689ff",
        width=7,
        stipple="gray50",
        tags=(SELECTION_TAG,),
    )
    handle_radius = max(5, to_canvas_length(canvas, 6))
    for x, y in ((x1, y1), (x2, y2)):
        canvas.create_oval(
            x - handle_radius,
            y - handle_radius,
            x + handle_radius,
            y + handle_radius,
            fill="white",
            outline="#1689ff",
            width=2,
            tags=(SELECTION_TAG,),
        )


def _draw_selections(ctx, selections: list[MapSelection]) -> None:
    ctx.canvas.delete(SELECTION_TAG)
    for selection in selections:
        _draw_selection_shape(ctx, selection)


def _draw_selection(ctx, selection: MapSelection) -> None:
    """Backward-compatible single-selection drawing helper."""
    _draw_selections(ctx, [selection])


def _sensor_data_names(ctx, names) -> list[str]:
    sensor_states = ctx.house_state.sensor_states()
    return [name for name in names if name in sensor_states]


def _show_graphs(ctx, names, title="Graphs") -> None:
    selected = _sensor_data_names(ctx, names)
    if not selected:
        messagebox.showwarning(title, "No recorded sensor data is available yet.")
        return
    from graph import show_graphs

    show_graphs(ctx.canvas, ctx.house_state.sensor_states(), selected)


def _show_csv(ctx, names, title="Sensor CSV/log") -> None:
    selected = _sensor_data_names(ctx, names)
    if not selected:
        messagebox.showwarning(title, "No recorded sensor data is available yet.")
        return
    from log import show_log

    log_state = ctx.house_state.values.setdefault(
        "activity_log_state",
        {"activity_log": [], "active_activities": {}},
    )
    show_log(
        ctx.canvas,
        ctx.house_state.sensor_states(),
        ctx.load_active,
        log_state,
        selected,
    )


def show_device_details(ctx, device) -> None:
    sensor_names = associated_sensor_names(ctx, device)
    messagebox.showinfo(
        f"Device: {device.name}",
        "\n".join(
            [
                f"Name: {device.name}",
                f"Type: {device.type}",
                f"State: {'ON' if device.state else 'OFF'}",
                f"Position: ({device.x}, {device.y})",
                f"Power: {device.power} W",
                f"Consumption range: {device.min_consumption} - {device.max_consumption} W",
                f"Current consumption: {device.current_consumption} W",
                f"Associated sensors: {', '.join(sensor_names) if sensor_names else 'None'}",
            ]
        ),
    )


def show_device_graphs(ctx, device) -> None:
    _show_graphs(ctx, associated_sensor_names(ctx, device), "Device graphs")


def show_device_csv(ctx, device) -> None:
    _show_csv(ctx, associated_sensor_names(ctx, device), "Device CSV/log")


def _show_details(ctx, selection: MapSelection) -> None:
    value = selection.value
    if selection.kind == "device":
        show_device_details(ctx, value)
    elif selection.kind == "sensor":
        messagebox.showinfo(
            f"Sensor: {value.name}",
            "\n".join(
                [
                    f"Name: {value.name}",
                    f"Type: {value.type}",
                    f"Position: ({value.x}, {value.y})",
                    f"State: {value.state}",
                    f"Range: {value.min_val} - {value.max_val}",
                    f"Associated device: {value.associated_device or 'None'}",
                ]
            ),
        )
    elif selection.kind == "point":
        messagebox.showinfo(
            f"Point: {value.name}",
            f"Name: {value.name}\nPosition: ({value.x}, {value.y})",
        )
    elif selection.kind == "door":
        messagebox.showinfo(
            "Door",
            f"From: ({value.x1}, {value.y1})\nTo: ({value.x2}, {value.y2})\nState: {value.state}",
        )
    else:
        messagebox.showinfo(
            "Wall",
            f"From: ({value.x1}, {value.y1})\nTo: ({value.x2}, {value.y2})",
        )


def _remove_wall_model(ctx, wall) -> None:
    if ctx.load_active:
        sources = [getattr(ctx, "read_walls", [])]
        from read import read_walls_coordinates

        sources.append(read_walls_coordinates)
        for source in sources:
            while wall in source:
                source.remove(wall)
        return

    from wall import walls, walls_coordinates

    while wall in walls_coordinates:
        walls_coordinates.remove(wall)
    if wall in walls:
        walls.remove(wall)
        return

    point_a = next(
        (p.name for p in current_points(ctx) if (p.x, p.y) == (wall.x1, wall.y1)),
        None,
    )
    point_b = next(
        (p.name for p in current_points(ctx) if (p.x, p.y) == (wall.x2, wall.y2)),
        None,
    )
    if point_a is None or point_b is None:
        return
    for index in range(0, len(walls) - 1, 2):
        if {walls[index], walls[index + 1]} == {point_a, point_b}:
            del walls[index:index + 2]
            break


def _remove_door_model(ctx, door) -> None:
    source = current_doors(ctx)
    while door in source:
        source.remove(door)


def _redraw_editable_objects(ctx, *, finalize: bool = True) -> None:
    canvas = ctx.canvas
    from device import draw_device
    from door import draw_all_doors
    from label_layout import forget_canvas_labels
    from read import draw_points
    from utils import draw_sensor, refresh_pir_fov, set_pir_fov_sources

    forget_canvas_labels(canvas)
    for tag in (
        "point",
        "wall",
        "sensor",
        "device",
        "door",
        SELECTION_TAG,
        DRAG_PREVIEW_TAG,
    ):
        canvas.delete(tag)

    draw_points(current_points(ctx), canvas)
    for wall in current_walls(ctx):
        x1, y1 = to_canvas(canvas, wall.x1, wall.y1)
        x2, y2 = to_canvas(canvas, wall.x2, wall.y2)
        canvas.create_line(x1, y1, x2, y2, fill="black", width=3, tags="wall")
    for sensor in current_sensors(ctx):
        draw_sensor(canvas, sensor)
    for device in current_devices(ctx):
        draw_device(canvas, device)
    draw_all_doors(canvas, current_doors(ctx))

    if not finalize:
        return

    set_pir_fov_sources(
        canvas,
        current_sensors(ctx),
        current_walls(ctx),
        current_doors(ctx),
    )
    refresh_pir_fov(canvas)

    from app.ui.rooms import refresh_rooms

    rooms = refresh_rooms(ctx, draw=True)
    runtime_sources = getattr(ctx, "_manual_runtime_sources", None)
    if isinstance(runtime_sources, dict):
        runtime_sources["rooms"] = rooms


def _unique_name(source: list, base_name: str) -> str:
    existing = {
        str(getattr(item, "name", "")).strip().lower()
        for item in source
    }
    root = f"{base_name}_copy"
    candidate = root
    number = 2
    while candidate.lower() in existing:
        candidate = f"{root}_{number}"
        number += 1
    return candidate


def _ensure_reference_point(ctx, x: int, y: int):
    from models import Point

    source = current_points(ctx)
    existing = next(
        (point for point in source if (point.x, point.y) == (x, y)),
        None,
    )
    if existing is not None:
        return existing
    point = Point(_unique_name(source, "point"), x, y)
    source.append(point)
    return point


def copy_selected_object(ctx, _event=None):
    selection = getattr(ctx, "_selected_object", None)
    if selection is None or selection.value is None:
        return None
    ctx._map_clipboard = MapSelection(
        selection.kind,
        copy.deepcopy(selection.value),
    )
    ctx._map_clipboard_paste_count = 0
    ctx._map_clipboard_is_cut = False
    return "break"


def _clipboard_name(source: list, name: str, preserve_name: bool) -> str:
    if preserve_name and all(
        str(getattr(item, "name", "")).lower() != name.lower()
        for item in source
    ):
        return name
    return _unique_name(source, name)


def _append_pasted_object(
    ctx,
    selection: MapSelection,
    *,
    preserve_name: bool = False,
) -> None:
    value = selection.value
    if selection.kind == "point":
        source = current_points(ctx)
        value.name = _clipboard_name(source, value.name, preserve_name)
        source.append(value)
    elif selection.kind == "sensor":
        source = current_sensors(ctx)
        value.name = _clipboard_name(source, value.name, preserve_name)
        source.append(value)
    elif selection.kind == "device":
        source = current_devices(ctx)
        value.name = _clipboard_name(source, value.name, preserve_name)
        source.append(value)
    elif selection.kind == "door":
        current_doors(ctx).append(value)
    elif selection.kind == "wall":
        point_a = _ensure_reference_point(ctx, value.x1, value.y1)
        point_b = _ensure_reference_point(ctx, value.x2, value.y2)
        current_walls(ctx).append(value)
        if ctx.load_active:
            from read import read_walls_coordinates

            if value not in read_walls_coordinates:
                read_walls_coordinates.append(value)
        else:
            from wall import walls

            walls.extend([point_a.name, point_b.name])


def paste_map_object(ctx, _event=None):
    clipboard = getattr(ctx, "_map_clipboard", None)
    if clipboard is None:
        return None

    is_cut = bool(getattr(ctx, "_map_clipboard_is_cut", False))
    count = int(getattr(ctx, "_map_clipboard_paste_count", 0)) + 1
    ctx._map_clipboard_paste_count = count
    offset = 0 if is_cut else 20 * count
    value = copy.deepcopy(clipboard.value)
    if clipboard.kind in {"point", "sensor", "device"}:
        value.x = int(round(value.x + offset))
        value.y = int(round(value.y + offset))
        value.x, value.y = snap_logical_position(ctx.canvas, value.x, value.y)
    else:
        value.x1 = int(round(value.x1 + offset))
        value.y1 = int(round(value.y1 + offset))
        value.x2 = int(round(value.x2 + offset))
        value.y2 = int(round(value.y2 + offset))
        value.x1, value.y1 = snap_logical_position(
            ctx.canvas,
            value.x1,
            value.y1,
        )
        value.x2, value.y2 = snap_logical_position(
            ctx.canvas,
            value.x2,
            value.y2,
        )

    selection = MapSelection(clipboard.kind, value)
    _append_pasted_object(ctx, selection, preserve_name=is_cut)
    if is_cut:
        ctx._map_clipboard_is_cut = False
        ctx._map_clipboard_paste_count = 0
    _set_selected_objects(ctx, [selection])
    _redraw_editable_objects(ctx)
    _draw_selection(ctx, selection)
    return "break"


def cut_selected_object(ctx, _event=None):
    selection = getattr(ctx, "_selected_object", None)
    if selection is None:
        return None
    if selection.kind == "point":
        value = selection.value
        is_connected = any(
            (value.x, value.y) in {
                (segment.x1, segment.y1),
                (segment.x2, segment.y2),
            }
            for segment in [*current_walls(ctx), *current_doors(ctx)]
        )
        if is_connected:
            messagebox.showwarning(
                "Cut point",
                "This point is connected to a wall or door. Drag the point "
                "to move the connected corner, or cut the segments first.",
            )
            return "break"
    copy_selected_object(ctx)
    ctx._map_clipboard_is_cut = True
    delete_map_selection(
        ctx,
        selection,
        confirm=False,
        preserve_runtime=True,
    )
    return "break"


def duplicate_selected_object(ctx, _event=None):
    if copy_selected_object(ctx) is None:
        return None
    return paste_map_object(ctx)


def _capture_geometry(ctx) -> list[tuple[object, tuple]]:
    snapshot = []
    for value in [*current_points(ctx), *current_sensors(ctx), *current_devices(ctx)]:
        snapshot.append((value, (value.x, value.y)))
    for value in [*current_walls(ctx), *current_doors(ctx)]:
        snapshot.append((value, (value.x1, value.y1, value.x2, value.y2)))
    return snapshot


def _restore_geometry(snapshot) -> None:
    for value, coordinates in snapshot:
        if len(coordinates) == 2:
            value.x, value.y = coordinates
        else:
            value.x1, value.y1, value.x2, value.y2 = coordinates


def _push_undo_action(ctx, action) -> None:
    history = getattr(ctx, "_movement_undo_stack", None)
    if history is None:
        history = []
        ctx._movement_undo_stack = history
    history.append(action)
    if len(history) > 100:
        del history[:-100]


def record_wall_addition(ctx, wall) -> None:
    """Make a newly created wall removable by the next Ctrl+Z."""
    _push_undo_action(ctx, {"kind": "wall_added", "wall": wall})


def undo_last_movement(ctx, _event=None):
    """Undo the most recent movement, wall creation, or wall deletion."""
    history = getattr(ctx, "_movement_undo_stack", None)
    if not history:
        return None
    action = history.pop()
    if isinstance(action, dict):
        kind = action.get("kind")
        if kind == "geometry":
            _restore_geometry(action["snapshot"])
        elif kind == "wall_added":
            _remove_wall_model(ctx, action["wall"])
            clear_selection(ctx)
        elif kind == "wall_removed":
            wall = action["wall"]
            _append_pasted_object(
                ctx,
                MapSelection("wall", wall),
                preserve_name=True,
            )
            _set_selected_objects(ctx, [MapSelection("wall", wall)])
        else:
            return None
    else:
        # Backward-compatible geometry snapshots created before action records.
        _restore_geometry(action)
    _redraw_editable_objects(ctx)
    selections = selected_objects(ctx)
    if selections:
        _draw_selections(ctx, selections)
    return "break"


def _translate_vertices(ctx, mapping: dict[tuple, tuple]) -> None:
    for point in current_points(ctx):
        target = mapping.get((point.x, point.y))
        if target is not None:
            point.x, point.y = target
    for segment in [*current_walls(ctx), *current_doors(ctx)]:
        target = mapping.get((segment.x1, segment.y1))
        if target is not None:
            segment.x1, segment.y1 = target
        target = mapping.get((segment.x2, segment.y2))
        if target is not None:
            segment.x2, segment.y2 = target


def _segment_drag_endpoint(ctx, selection: MapSelection, x: float, y: float):
    value = selection.value
    tolerance = max(5.0, 10.0 / max(get_zoom(ctx.canvas), 0.01))
    first = math.hypot(x - value.x1, y - value.y1)
    second = math.hypot(x - value.x2, y - value.y2)
    if min(first, second) > tolerance:
        return None
    return 1 if first <= second else 2


def begin_selection_drag(ctx, event):
    selection = _selection_at_event(ctx, event)
    if selection is None:
        clear_selection(ctx)
        ctx._map_drag_state = None
        return "break"

    selections = selected_objects(ctx)
    if not _selection_is_in(selection, selections) or len(selections) <= 1:
        selections = [selection]
        _set_selected_objects(ctx, selections)
    _draw_selections(ctx, selections)
    ctx.canvas.focus_set()
    x, y = event_to_logical(ctx.canvas, event)
    endpoint = None
    if len(selections) == 1 and selection.kind in {"wall", "door"}:
        endpoint = _segment_drag_endpoint(ctx, selection, x, y)
    ctx._map_drag_state = {
        "selection": selection,
        "selections": selections,
        "start": (x, y),
        "endpoint": endpoint,
        "snapshot": _capture_geometry(ctx),
        "delta": (0, 0),
        "moved": False,
    }
    return "break"


def drag_selected_object(ctx, event):
    state = getattr(ctx, "_map_drag_state", None)
    if not state:
        return None
    x, y = event_to_logical(ctx.canvas, event)
    start_x, start_y = state["start"]
    dx = int(round(x - start_x))
    dy = int(round(y - start_y))
    state["delta"] = (dx, dy)
    state["moved"] = dx != 0 or dy != 0
    if state["moved"]:
        _draw_drag_preview(ctx, state, dx, dy)
    else:
        ctx.canvas.delete(DRAG_PREVIEW_TAG)
        _draw_selections(ctx, state.get("selections", [state["selection"]]))
    return "break"


def _resolved_drag_delta(ctx, state, dx: int, dy: int) -> tuple[int, int]:
    selection = state["selection"]
    selections = state.get("selections", [selection])
    snap_selection = selections[0] if selections else None
    if bool(getattr(ctx.canvas, "_snap_to_grid", False)) and snap_selection is not None:
        snap_original = next(
            coordinates
            for item, coordinates in state["snapshot"]
            if item is snap_selection.value
        )
        if snap_selection.kind in {"point", "sensor", "device"}:
            anchor_x, anchor_y = snap_original
        elif len(selections) == 1 and state["endpoint"] == 2:
            anchor_x, anchor_y = snap_original[2], snap_original[3]
        else:
            anchor_x, anchor_y = snap_original[0], snap_original[1]
        snapped_x, snapped_y = snap_logical_position(
            ctx.canvas,
            anchor_x + dx,
            anchor_y + dy,
        )
        dx, dy = snapped_x - anchor_x, snapped_y - anchor_y
    return dx, dy


def _preview_coordinates(ctx, state, selection, dx: int, dy: int) -> tuple:
    selections = state.get("selections", [state["selection"]])
    original = next(
        coordinates
        for item, coordinates in state["snapshot"]
        if item is selection.value
    )
    if selection.kind in {"point", "sensor", "device"}:
        return snap_logical_position(
            ctx.canvas,
            original[0] + dx,
            original[1] + dy,
        )

    x1, y1, x2, y2 = original
    if len(selections) > 1:
        return x1 + dx, y1 + dy, x2 + dx, y2 + dy
    if state["endpoint"] == 1:
        target_x, target_y = snap_logical_position(ctx.canvas, x1 + dx, y1 + dy)
        return target_x, target_y, x2, y2
    if state["endpoint"] == 2:
        target_x, target_y = snap_logical_position(ctx.canvas, x2 + dx, y2 + dy)
        return x1, y1, target_x, target_y
    return (
        *snap_logical_position(ctx.canvas, x1 + dx, y1 + dy),
        *snap_logical_position(ctx.canvas, x2 + dx, y2 + dy),
    )


def _draw_drag_preview(ctx, state, dx: int, dy: int) -> None:
    dx, dy = _resolved_drag_delta(ctx, state, dx, dy)
    canvas = ctx.canvas
    canvas.delete(SELECTION_TAG)
    canvas.delete(DRAG_PREVIEW_TAG)
    for selection in state.get("selections", [state["selection"]]):
        coordinates = _preview_coordinates(ctx, state, selection, dx, dy)
        if len(coordinates) == 2:
            x, y = to_canvas(canvas, *coordinates)
            radius = max(9, to_canvas_length(canvas, 10))
            canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                outline="#ff8a00",
                width=3,
                dash=(4, 2),
                tags=(DRAG_PREVIEW_TAG,),
            )
            continue
        x1, y1 = to_canvas(canvas, coordinates[0], coordinates[1])
        x2, y2 = to_canvas(canvas, coordinates[2], coordinates[3])
        canvas.create_line(
            x1,
            y1,
            x2,
            y2,
            fill="#ff8a00",
            width=4,
            dash=(6, 3),
            tags=(DRAG_PREVIEW_TAG,),
        )


def _apply_drag_delta(ctx, state, dx: int, dy: int) -> None:
    _restore_geometry(state["snapshot"])
    dx, dy = _resolved_drag_delta(ctx, state, dx, dy)
    selection = state["selection"]
    selections = state.get("selections", [selection])

    if len(selections) > 1:
        originals = {
            id(item): coordinates
            for item, coordinates in state["snapshot"]
        }
        vertex_mapping = {}
        for candidate in selections:
            coordinates = originals[id(candidate.value)]
            if candidate.kind == "point":
                old_x, old_y = coordinates
                vertex_mapping[(old_x, old_y)] = (old_x + dx, old_y + dy)
            elif candidate.kind == "wall":
                x1, y1, x2, y2 = coordinates
                vertex_mapping[(x1, y1)] = (x1 + dx, y1 + dy)
                vertex_mapping[(x2, y2)] = (x2 + dx, y2 + dy)
        _translate_vertices(ctx, vertex_mapping)
        for candidate in selections:
            value = candidate.value
            coordinates = originals[id(value)]
            if candidate.kind in {"sensor", "device"}:
                value.x, value.y = coordinates[0] + dx, coordinates[1] + dy
            elif candidate.kind == "door":
                x1, y1, x2, y2 = coordinates
                value.x1, value.y1 = vertex_mapping.get(
                    (x1, y1),
                    (x1 + dx, y1 + dy),
                )
                value.x2, value.y2 = vertex_mapping.get(
                    (x2, y2),
                    (x2 + dx, y2 + dy),
                )
        return

    value = selection.value
    original = next(
        coordinates
        for item, coordinates in state["snapshot"]
        if item is value
    )
    if selection.kind in {"point", "sensor", "device"}:
        old_x, old_y = original
        target = snap_logical_position(ctx.canvas, old_x + dx, old_y + dy)
        if selection.kind == "point":
            _translate_vertices(ctx, {(old_x, old_y): target})
        else:
            value.x, value.y = target
    elif selection.kind == "wall":
        x1, y1, x2, y2 = original
        if state["endpoint"] == 1:
            target = snap_logical_position(ctx.canvas, x1 + dx, y1 + dy)
            mapping = {(x1, y1): target}
        elif state["endpoint"] == 2:
            target = snap_logical_position(ctx.canvas, x2 + dx, y2 + dy)
            mapping = {(x2, y2): target}
        else:
            mapping = {
                (x1, y1): snap_logical_position(ctx.canvas, x1 + dx, y1 + dy),
                (x2, y2): snap_logical_position(ctx.canvas, x2 + dx, y2 + dy),
            }
        _translate_vertices(ctx, mapping)
    else:
        x1, y1, x2, y2 = original
        if state["endpoint"] == 1:
            value.x1, value.y1 = snap_logical_position(
                ctx.canvas,
                x1 + dx,
                y1 + dy,
            )
        elif state["endpoint"] == 2:
            value.x2, value.y2 = snap_logical_position(
                ctx.canvas,
                x2 + dx,
                y2 + dy,
            )
        else:
            value.x1, value.y1 = snap_logical_position(
                ctx.canvas,
                x1 + dx,
                y1 + dy,
            )
            value.x2, value.y2 = snap_logical_position(
                ctx.canvas,
                x2 + dx,
                y2 + dy,
            )


def finish_selection_drag(ctx, event=None):
    state = getattr(ctx, "_map_drag_state", None)
    ctx._map_drag_state = None
    if not state:
        return "break"

    if event is not None and hasattr(event, "x") and hasattr(event, "y"):
        x, y = event_to_logical(ctx.canvas, event)
        start_x, start_y = state["start"]
        state["delta"] = (
            int(round(x - start_x)),
            int(round(y - start_y)),
        )
        state["moved"] = state["delta"] != (0, 0)
    if not state["moved"]:
        ctx.canvas.delete(DRAG_PREVIEW_TAG)
        _draw_selections(ctx, state.get("selections", [state["selection"]]))
        return "break"

    dx, dy = state["delta"]
    _push_undo_action(
        ctx,
        {"kind": "geometry", "snapshot": state["snapshot"]},
    )
    _apply_drag_delta(ctx, state, dx, dy)
    selections = state.get("selections", [state["selection"]])
    ctx.canvas.delete(DRAG_PREVIEW_TAG)
    _redraw_editable_objects(ctx)
    _draw_selections(ctx, selections)
    return "break"


def delete_map_selection(
    ctx,
    selection: MapSelection,
    *,
    confirm: bool = True,
    preserve_runtime: bool = False,
) -> bool:
    if selection is None or selection.value is None:
        return False
    value = selection.value
    display_name = getattr(value, "name", selection.kind)
    connected_walls = []
    connected_doors = []
    if selection.kind == "point":
        connected_walls = [
            wall
            for wall in current_walls(ctx)
            if (value.x, value.y) in {(wall.x1, wall.y1), (wall.x2, wall.y2)}
        ]
        connected_doors = [
            door
            for door in current_doors(ctx)
            if (value.x, value.y) in {(door.x1, door.y1), (door.x2, door.y2)}
        ]

    prompt = f"Delete {selection.kind} '{display_name}'?"
    if connected_walls or connected_doors:
        prompt += (
            f"\n\nThis will also remove {len(connected_walls)} connected wall(s)"
            f" and {len(connected_doors)} connected door(s)."
        )
    if confirm and not ask_confirmation(
        ctx,
        f"confirm_delete_{selection.kind}",
        "Delete object",
        prompt,
    ):
        return False

    if selection.kind == "device":
        source = current_devices(ctx)
        if value not in source:
            return False
        source.remove(value)
        if not preserve_runtime:
            for sensor in current_sensors(ctx):
                if getattr(sensor, "associated_device", None) == value.name:
                    sensor.associated_device = None
            try:
                ctx.house_state.active_cycles().pop(value.name, None)
            except Exception:
                pass
    elif selection.kind == "sensor":
        source = current_sensors(ctx)
        if value not in source:
            return False
        source.remove(value)
        if not preserve_runtime:
            try:
                ctx.house_state.sensor_states().pop(value.name, None)
            except Exception:
                pass
    elif selection.kind == "wall":
        if value not in current_walls(ctx):
            return False
        _push_undo_action(ctx, {"kind": "wall_removed", "wall": value})
        _remove_wall_model(ctx, value)
    elif selection.kind == "door":
        _remove_door_model(ctx, value)
    elif selection.kind == "point":
        for wall in connected_walls:
            _remove_wall_model(ctx, wall)
        for door in connected_doors:
            _remove_door_model(ctx, door)
        source = current_points(ctx)
        if value not in source:
            return False
        source.remove(value)
    else:
        return False

    clear_selection(ctx)
    _redraw_editable_objects(ctx)
    return True


def delete_device(ctx, device, *, confirm: bool = True) -> bool:
    return delete_map_selection(
        ctx,
        MapSelection("device", device),
        confirm=confirm,
    )


def delete_selected_object(ctx, _event=None):
    selection = getattr(ctx, "_selected_object", None)
    if selection is None:
        return None
    deleted = delete_map_selection(ctx, selection)
    return "break" if deleted else None


def delete_selected_device(ctx, _event=None):
    """Backward-compatible alias; selection is no longer device-only."""
    return delete_selected_object(ctx, _event)


def _show_selection_actions(ctx, selection: MapSelection, event) -> None:
    value = selection.value
    name = getattr(value, "name", selection.kind.title())
    menu = tk.Menu(ctx.canvas, tearoff=0)
    menu.add_command(
        label=f"{selection.kind.title()}: {name}",
        state="disabled",
    )
    menu.add_separator()
    menu.add_command(
        label="Copy",
        accelerator="Ctrl+C",
        command=lambda: copy_selected_object(ctx),
    )
    menu.add_command(
        label="Cut",
        accelerator="Ctrl+X",
        command=lambda: cut_selected_object(ctx),
    )
    menu.add_command(
        label="Duplicate",
        accelerator="Ctrl+D",
        command=lambda: duplicate_selected_object(ctx),
    )
    menu.add_command(
        label="Paste",
        accelerator="Ctrl+V",
        state="normal" if getattr(ctx, "_map_clipboard", None) else "disabled",
        command=lambda: paste_map_object(ctx),
    )
    menu.add_separator()
    menu.add_command(label="Details...", command=lambda: _show_details(ctx, selection))
    if selection.kind == "device":
        sensor_names = associated_sensor_names(ctx, value)
        state = "normal" if sensor_names else "disabled"
        menu.add_command(
            label="Graphs...",
            state=state,
            command=lambda: show_device_graphs(ctx, value),
        )
        menu.add_command(
            label="Sensor CSV / log...",
            state=state,
            command=lambda: show_device_csv(ctx, value),
        )
    elif selection.kind == "sensor":
        menu.add_command(
            label="Graph...",
            command=lambda: _show_graphs(ctx, [value.name]),
        )
        menu.add_command(
            label="CSV / log...",
            command=lambda: _show_csv(ctx, [value.name]),
        )
    menu.add_separator()
    menu.add_command(
        label=f"Delete {selection.kind}",
        command=lambda: delete_map_selection(ctx, selection),
    )
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


def select_object_at_event(ctx, event):
    selection = _selection_at_event(ctx, event)
    if selection is None:
        clear_selection(ctx)
        return "break"

    _set_selected_objects(ctx, [selection])
    _draw_selection(ctx, selection)
    ctx.canvas.focus_set()
    return "break"


def open_selection_menu_at_event(ctx, event):
    """Select the object under the pointer and open its action menu."""
    selection = _selection_at_event(ctx, event)
    if selection is None:
        clear_selection(ctx)
        if getattr(ctx, "_map_clipboard", None) is not None:
            menu = tk.Menu(ctx.canvas, tearoff=0)
            menu.add_command(
                label="Paste",
                accelerator="Ctrl+V",
                command=lambda: paste_map_object(ctx),
            )
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        return "break"

    _set_selected_objects(ctx, [selection])
    _draw_selection(ctx, selection)
    ctx.canvas.focus_set()
    _show_selection_actions(ctx, selection, event)
    return "break"


def select_device_at_event(ctx, event):
    """Backward-compatible alias for the generic map selector."""
    return select_object_at_event(ctx, event)


def set_canvas_mode(ctx, mode: str, text: str) -> None:
    ctx._canvas_mode = mode
    mode_var = getattr(ctx, "_canvas_mode_var", None)
    if mode_var is not None:
        mode_var.set(text)
    palette = getattr(ctx, "_tool_palette", None)
    if palette is not None:
        active_key = (
            getattr(ctx, "_placement_tool_key", None)
            if mode == "placement"
            else mode
        )
        palette.set_active(active_key)


def _cancel_placement_if_needed(ctx) -> None:
    if getattr(ctx, "_canvas_mode", None) != "placement":
        return
    cancel = getattr(ctx, "_cancel_canvas_action", None)
    if callable(cancel):
        # Prevent a restoration callback from trying to cancel this same tool.
        ctx._canvas_mode = "cancelling"
        cancel()


def activate_select_mode(ctx) -> None:
    from app.controllers.simulation import enable_all_menus

    _cancel_placement_if_needed(ctx)
    enable_all_menus(ctx)
    ctx.canvas.unbind("<Button-1>")
    ctx.canvas.unbind("<Button-3>")
    ctx.canvas.unbind("<Motion>")
    ctx.canvas.bind("<ButtonPress-1>", lambda event: begin_selection_drag(ctx, event))
    ctx.canvas.bind("<B1-Motion>", lambda event: drag_selected_object(ctx, event))
    ctx.canvas.bind("<ButtonRelease-1>", lambda event: finish_selection_drag(ctx, event))
    ctx.canvas.bind("<ButtonPress-3>", lambda event: begin_marquee_selection(ctx, event))
    ctx.canvas.bind("<B3-Motion>", lambda event: update_marquee_selection(ctx, event))
    ctx.canvas.bind("<ButtonRelease-3>", lambda event: finish_marquee_selection(ctx, event))
    set_canvas_cursor(ctx.canvas, "arrow")
    ctx._placement_tool_key = None
    set_canvas_mode(ctx, "select", "Mode: Select / inspect")


def activate_manual_mode(ctx, text: str = "Manual: press Start") -> None:
    """Make Manual an exclusive canvas mode without starting its timer."""
    from app.controllers.simulation import enable_all_menus

    _cancel_placement_if_needed(ctx)
    enable_all_menus(ctx)
    clear_selection(ctx)
    ctx.canvas.unbind("<Button-1>")
    ctx.canvas.unbind("<ButtonPress-1>")
    ctx.canvas.unbind("<B1-Motion>")
    ctx.canvas.unbind("<ButtonRelease-1>")
    ctx.canvas.unbind("<Button-3>")
    ctx.canvas.unbind("<ButtonPress-3>")
    ctx.canvas.unbind("<B3-Motion>")
    ctx.canvas.unbind("<ButtonRelease-3>")
    ctx.canvas.unbind("<Motion>")
    set_canvas_cursor(ctx.canvas, "arrow")
    ctx._placement_tool_key = None
    set_canvas_mode(ctx, "manual", text)


def begin_placement_mode(ctx, label: str, callback) -> None:
    from app.controllers.simulation import enable_all_menus

    _cancel_placement_if_needed(ctx)
    enable_all_menus(ctx)
    clear_selection(ctx)
    ctx._placement_tool_key = {
        "Add points": "point",
        "Add devices": "device",
        "Add sensors": "sensor",
    }.get(label)
    ctx.canvas.unbind("<ButtonPress-1>")
    ctx.canvas.unbind("<B1-Motion>")
    ctx.canvas.unbind("<ButtonRelease-1>")
    ctx.canvas.unbind("<Button-3>")
    ctx.canvas.unbind("<ButtonPress-3>")
    ctx.canvas.unbind("<B3-Motion>")
    ctx.canvas.unbind("<ButtonRelease-3>")
    ctx.canvas.bind("<Button-1>", callback)
    set_canvas_cursor(ctx.canvas, "crosshair")
    ctx.scenario_menu.entryconfig(label, state="disabled")
    set_canvas_mode(ctx, "placement", f"Mode: {label} — click the grid")


def _point_at_event(ctx, event):
    """Return the point close enough to be intentionally clicked."""
    points = current_points(ctx)
    if not points:
        return None
    x, y = event_to_logical(ctx.canvas, event)
    point = _nearest_marker(points, x, y)
    tolerance = max(8.0, 12.0 / max(get_zoom(ctx.canvas), 0.01))
    if math.hypot(point.x - x, point.y - y) > tolerance:
        return None
    return point


def begin_point_segment_mode(
    ctx,
    label: str,
    callback,
    on_finished=None,
    *,
    continuous: bool = False,
) -> None:
    """Pick two existing points and preview a wall/door between them."""
    from app.controllers.simulation import enable_all_menus

    _cancel_placement_if_needed(ctx)

    enable_all_menus(ctx)
    clear_selection(ctx)
    canvas = ctx.canvas
    state = {"first": None, "done": False}
    ctx._segment_placement_state = state
    ctx._placement_tool_key = {
        "Add walls": "wall",
        "Add doors": "door",
    }.get(label)

    for sequence in (
        "<Button-1>",
        "<ButtonPress-1>",
        "<B1-Motion>",
        "<ButtonRelease-1>",
        "<Button-3>",
        "<ButtonPress-3>",
        "<B3-Motion>",
        "<ButtonRelease-3>",
        "<Motion>",
    ):
        canvas.unbind(sequence)

    def draw_preview(end_x, end_y):
        first = state["first"]
        canvas.delete(SEGMENT_PREVIEW_TAG)
        if first is None:
            return
        start_x, start_y = to_canvas(canvas, first.x, first.y)
        target_x, target_y = to_canvas(canvas, end_x, end_y)
        radius = max(5, to_canvas_length(canvas, 6))
        canvas.create_oval(
            start_x - radius,
            start_y - radius,
            start_x + radius,
            start_y + radius,
            outline="#ff8a00",
            width=3,
            tags=(SEGMENT_PREVIEW_TAG,),
        )
        canvas.create_line(
            start_x,
            start_y,
            target_x,
            target_y,
            fill="#ff8a00",
            width=3,
            dash=(6, 3),
            tags=(SEGMENT_PREVIEW_TAG,),
        )

    def finish(created=False):
        if state["done"]:
            return "break"
        state["done"] = True
        canvas.delete(SEGMENT_PREVIEW_TAG)
        for sequence in ("<Button-1>", "<Button-3>", "<Motion>"):
            canvas.unbind(sequence)
        canvas.bind("<Escape>", lambda _event: clear_selection(ctx))
        ctx._segment_placement_state = None
        ctx._cancel_canvas_action = getattr(ctx, "_activate_canvas_select", None)
        ctx._canvas_mode = "cancelling"
        enable_all_menus(ctx)
        if callable(on_finished):
            on_finished(bool(created))
        return "break"

    def reset_segment(_event=None):
        state["first"] = None
        canvas.delete(SEGMENT_PREVIEW_TAG)
        set_canvas_mode(
            ctx,
            "placement",
            f"Mode: {label} - click the first point",
        )
        return "break"

    def move(event):
        first = state["first"]
        if first is None:
            return None
        hovered = _point_at_event(ctx, event)
        if hovered is not None:
            end_x, end_y = hovered.x, hovered.y
        else:
            end_x, end_y = event_to_logical(canvas, event)
        draw_preview(end_x, end_y)
        return None

    def click(event):
        point = _point_at_event(ctx, event)
        if point is None:
            set_canvas_mode(
                ctx,
                "placement",
                f"Mode: {label} - click an existing point",
            )
            return "break"
        if state["first"] is None:
            state["first"] = point
            draw_preview(point.x, point.y)
            set_canvas_mode(
                ctx,
                "placement",
                f"Mode: {label} - click the second point",
            )
            return "break"
        if point is state["first"]:
            set_canvas_mode(
                ctx,
                "placement",
                f"Mode: {label} - choose a different point",
            )
            return "break"
        created = callback(state["first"], point)
        if created:
            if continuous:
                return reset_segment()
            return finish(True)
        state["first"] = None
        canvas.delete(SEGMENT_PREVIEW_TAG)
        set_canvas_mode(
            ctx,
            "placement",
            f"Mode: {label} - segment already exists; choose two points",
        )
        return "break"

    canvas.bind("<Button-1>", click)
    cancel_segment = reset_segment if continuous else lambda _event: finish(False)
    canvas.bind("<Button-3>", cancel_segment)
    canvas.bind("<Motion>", move)
    canvas.bind("<Escape>", cancel_segment)
    set_canvas_cursor(canvas, "crosshair")
    if getattr(ctx, "scenario_menu", None) is not None:
        ctx.scenario_menu.entryconfig(label, state="disabled")
    set_canvas_mode(ctx, "placement", f"Mode: {label} - click the first point")
    ctx._cancel_canvas_action = lambda: finish(False)


def install_device_inspector(ctx, mode_var) -> None:
    ctx._canvas_mode_var = mode_var
    ctx._placement_tool_key = None
    ctx._selected_object = None
    ctx._selected_device = None
    ctx._selected_objects = []
    ctx._map_clipboard = None
    ctx._map_clipboard_paste_count = 0
    ctx._map_clipboard_is_cut = False
    ctx._map_drag_state = None
    ctx._marquee_state = None
    ctx._movement_undo_stack = []
    ctx._activate_canvas_select = lambda: activate_select_mode(ctx)
    ctx._activate_canvas_manual = lambda text="Manual: press Start": (
        activate_manual_mode(ctx, text)
    )
    ctx._cancel_canvas_action = ctx._activate_canvas_select
    ctx._set_canvas_mode = lambda mode, text: set_canvas_mode(ctx, mode, text)
    ctx.canvas.bind("<Delete>", lambda event: delete_selected_object(ctx, event))
    ctx.canvas.bind("<Control-c>", lambda event: copy_selected_object(ctx, event))
    ctx.canvas.bind("<Control-x>", lambda event: cut_selected_object(ctx, event))
    ctx.canvas.bind("<Control-v>", lambda event: paste_map_object(ctx, event))
    ctx.canvas.bind("<Control-d>", lambda event: duplicate_selected_object(ctx, event))
    ctx.canvas.bind("<Control-z>", lambda event: undo_last_movement(ctx, event))
    ctx.canvas.bind("<Escape>", lambda _event: clear_selection(ctx))
    activate_select_mode(ctx)
