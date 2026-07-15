import tkinter as tk
from tkinter import simpledialog, messagebox
from read import coordinates
from models import Point
from label_layout import create_map_label, layout_map_labels
from canvas_zoom import event_to_logical, to_canvas, to_canvas_length

points = []
add_point_enabled = True


def add_point(canvas, event, load_active):
    global add_point_enabled
    if not add_point_enabled:
        return
    # Coordinate of the added point
    logical_x, logical_y = event_to_logical(canvas, event)
    x = int(logical_x)
    y = int(logical_y)

    # Dialog window to set the point name
    point_name = simpledialog.askstring("Point name", "Insert point name:")

    if point_name is None:
        return

    if not point_name.strip():
        messagebox.showwarning("Input not valid", "Point name cannot be empty.")
        return
    # Prevent duplicate names (case-insensitive) across runtime and file points
    if point_name_exists(point_name):
        messagebox.showwarning("Input not valid", "Point name already exists.")
        return

    point = Point(name=point_name, x=x, y=y)

    if load_active:
        coordinates.append(point)
    else:
        points.append(point)
    # Update canvas with the point
    canvas_x, canvas_y = to_canvas(canvas, x, y)
    radius = to_canvas_length(canvas, 5)
    marker_id = canvas.create_oval(
        canvas_x - radius,
        canvas_y - radius,
        canvas_x + radius,
        canvas_y + radius,
        fill="blue",
        tags='point',
    )
    create_map_label(
        canvas,
        x,
        y,
        text=point_name,
        fill="#506080",
        tags=('point',),
        kind="point",
        font=("Helvetica", 8),
        hover_target=marker_id,
    )
    layout_map_labels(canvas)

def point_name_exists(name: str) -> bool:
    target = name.strip().lower()
    if not target:
        return False
    # Names in runtime list
    runtime_names = set()
    for p in points:
        runtime_names.add(p.name.strip().lower())
    
    # Names in loaded-from-file list
    file_names = {p.name.strip().lower() for p in coordinates}
    return target in runtime_names or target in file_names
