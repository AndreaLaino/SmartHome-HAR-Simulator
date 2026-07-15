from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.context import AppContext
from label_layout import create_map_label, layout_map_labels
from canvas_zoom import to_canvas
from room_recognition import ROOM_TYPES, recognize_rooms


def _runtime_sources(ctx: AppContext):
    if ctx.load_active:
        return (
            ctx.r_points,
            ctx.read_walls,
            ctx.read_sensors,
            ctx.read_devices,
            ctx.read_doors,
        )

    from device import devices
    from door import doors
    from point import points
    from sensor import sensors
    from wall import walls_coordinates

    return points, walls_coordinates, sensors, devices, doors


def draw_rooms(ctx: AppContext) -> None:
    canvas = ctx.canvas
    if canvas is None:
        return
    canvas.delete("room_overlay")

    colors = {
        "Bathroom": "#2980b9",
        "Bedroom": "#8e44ad",
        "Dining room": "#a04000",
        "Hall": "#7f8c8d",
        "Kitchen": "#d35400",
        "Living room": "#16a085",
        "Office": "#2c3e50",
        "Laundry": "#2471a3",
        "Unknown": "#555555",
    }
    for index, room in enumerate(ctx.rooms, start=1):
        flat_polygon = [
            coordinate
            for point in room.polygon
            for coordinate in to_canvas(canvas, point[0], point[1])
        ]
        color = colors.get(room.room_type, "#555555")
        canvas.create_polygon(
            flat_polygon,
            fill="",
            outline=color,
            width=2,
            dash=(6, 4),
            tags="room_overlay",
        )
        center_x, center_y = room.centroid
        suffix = " (manual)" if room.manual_type else ""
        create_map_label(
            canvas,
            center_x,
            center_y,
            text=f"Room {index}: {room.room_type}{suffix}",
            fill=color,
            font=("Helvetica", 11, "bold"),
            tags="room_overlay",
            kind="room",
        )
    layout_map_labels(canvas)


def refresh_rooms(ctx: AppContext, *, draw: bool = True):
    points, walls, sensors, devices, doors = _runtime_sources(ctx)
    ctx.rooms = recognize_rooms(
        walls,
        doors,
        sensors,
        devices,
        points,
        overrides=ctx.room_overrides,
    )
    valid_ids = {room.room_id for room in ctx.rooms}
    ctx.room_overrides = {
        room_id: room_type
        for room_id, room_type in ctx.room_overrides.items()
        if room_id in valid_ids
    }
    if draw:
        draw_rooms(ctx)
    return ctx.rooms


def open_rooms_ui(ctx: AppContext) -> None:
    rooms = refresh_rooms(ctx, draw=True)
    window = tk.Toplevel(ctx.window)
    window.title("Room recognition")
    window.geometry("850x520")

    if not rooms:
        tk.Label(
            window,
            text="No closed rooms found. Check that walls and doors form closed boundaries.",
            padx=20,
            pady=20,
        ).pack()
        return

    container = tk.Frame(window)
    container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    canvas = tk.Canvas(container)
    scrollbar = tk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
    rows = tk.Frame(canvas)
    rows.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=rows, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    headers = ("Room", "Automatic", "Confidence", "Sensors", "Devices", "Final label")
    for column, header in enumerate(headers):
        tk.Label(rows, text=header, font=("Helvetica", 10, "bold")).grid(
            row=0, column=column, padx=6, pady=6, sticky="w"
        )

    selections = {}
    choices = ("Automatic",) + tuple(room_type for room_type in ROOM_TYPES if room_type != "Unknown")
    for row_index, room in enumerate(rooms, start=1):
        tk.Label(rows, text=f"Room {row_index}").grid(row=row_index, column=0, padx=6, pady=5, sticky="w")
        tk.Label(rows, text=room.inferred_type).grid(row=row_index, column=1, padx=6, pady=5, sticky="w")
        tk.Label(rows, text=f"{room.confidence:.0%}").grid(row=row_index, column=2, padx=6, pady=5, sticky="w")
        tk.Label(rows, text=", ".join(room.sensor_names) or "-").grid(
            row=row_index, column=3, padx=6, pady=5, sticky="w"
        )
        tk.Label(rows, text=", ".join(room.device_names) or "-").grid(
            row=row_index, column=4, padx=6, pady=5, sticky="w"
        )
        variable = tk.StringVar(value=room.manual_type or "Automatic")
        selections[room.room_id] = variable
        ttk.Combobox(
            rows,
            textvariable=variable,
            values=choices,
            state="readonly",
            width=16,
        ).grid(row=row_index, column=5, padx=6, pady=5, sticky="w")

    def apply_changes():
        ctx.room_overrides.clear()
        for room_id, variable in selections.items():
            selected = variable.get()
            if selected != "Automatic":
                ctx.room_overrides[room_id] = selected
        refresh_rooms(ctx, draw=True)
        window.destroy()

    button_frame = tk.Frame(window)
    button_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
    tk.Button(button_frame, text="Apply", command=apply_changes).pack(side=tk.RIGHT, padx=5)
    tk.Button(button_frame, text="Cancel", command=window.destroy).pack(side=tk.RIGHT, padx=5)
