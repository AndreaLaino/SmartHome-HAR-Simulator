import tkinter as tk
from point import points
from read import read_walls_coordinates, coordinates, read_walls
from models import Point, Wall
from utils import raise_overlay_labels
from canvas_zoom import to_canvas

walls: list[Wall] = []
walls_coordinates: list[Wall] = []


def add_wall_between_points(canvas, point1, point2, load_active, on_changed=None):
    """Create a wall whose endpoints are attached to two existing points."""
    if point1 is point2 or (point1.x, point1.y) == (point2.x, point2.y):
        return None

    target = read_walls if load_active else walls_coordinates
    endpoint_pair = frozenset(((point1.x, point1.y), (point2.x, point2.y)))
    if any(
        frozenset(((wall.x1, wall.y1), (wall.x2, wall.y2))) == endpoint_pair
        for wall in target
    ):
        return None

    wall = Wall(x1=point1.x, y1=point1.y, x2=point2.x, y2=point2.y)
    target.append(wall)
    if load_active:
        read_walls_coordinates.append(wall)
    else:
        walls.extend([str(point1.name), str(point2.name)])

    x1, y1 = to_canvas(canvas, wall.x1, wall.y1)
    x2, y2 = to_canvas(canvas, wall.x2, wall.y2)
    canvas.create_line(x1, y1, x2, y2, fill="black", width=3, tags="wall")
    raise_overlay_labels(canvas)
    if callable(on_changed):
        on_changed()
    return wall

def draw_line_window(canvas, window, load_active, on_changed=None):
    global walls

    # Pick the correct list of points depending on whether a scenario is loaded
    point_source = coordinates if load_active else points

    def draw_line():
        point1 = point1_entry.get()
        point2 = point2_entry.get()

        # Look up coordinates for the given point names
        coord_point1 = None
        coord_point2 = None
        for point in point_source:
            point_name, point_x, point_y = point.name, point.x, point.y
            
            if point_name == point1:
                coord_point1 = (point_x, point_y)
            elif point_name == point2:
                coord_point2 = (point_x, point_y)

        # If both points exist, draw the line
        if coord_point1 and coord_point2:
            first = next(p for p in point_source if p.name == point1)
            second = next(p for p in point_source if p.name == point2)
            if add_wall_between_points(
                canvas,
                first,
                second,
                load_active,
                on_changed=on_changed,
            ) is not None:
                window_line.destroy()

    # Dialog to input point names
    window_line = tk.Toplevel(window)
    window_line.title("Add wall")

    tk.Label(window_line, text="Wall").pack()


    # Label and input for first point
    tk.Label(window_line, text="Point 1:").pack()
    point1_entry = tk.Entry(window_line)
    point1_entry.pack()

    # Label and input for second point
    tk.Label(window_line, text="Point 2:").pack()
    point2_entry = tk.Entry(window_line)
    point2_entry.pack()

    # Button to draw the line
    tk.Button(window_line, text="Draw Line", command=draw_line).pack()
    return walls

