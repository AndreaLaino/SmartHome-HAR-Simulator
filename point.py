from read import coordinates
from models import Point
from canvas_zoom import event_to_logical, snap_logical_position, to_canvas, to_canvas_length

points = []
add_point_enabled = True


def add_point(canvas, event, load_active, on_finished=None):
    global add_point_enabled
    created = False
    try:
        if not add_point_enabled:
            return False
        logical_x, logical_y = event_to_logical(canvas, event)
        x, y = snap_logical_position(canvas, logical_x, logical_y)

        source = coordinates if load_active else points
        if any((point.x, point.y) == (x, y) for point in source):
            return False

        point = Point(name=next_point_name(source), x=x, y=y)

        if load_active:
            coordinates.append(point)
        else:
            points.append(point)
        draw_point(canvas, point)
        created = True
        return True
    finally:
        if callable(on_finished):
            on_finished(created)


def next_point_name(source=None) -> str:
    """Return a unique internal point ID without asking the user for a name."""
    existing = {
        str(point.name).strip().lower()
        for point in [*points, *coordinates, *(source or [])]
    }
    number = 1
    while f"point_{number}" in existing:
        number += 1
    return f"point_{number}"


def draw_point(canvas, point: Point) -> int:
    """Draw an anonymous point marker and return its canvas item ID."""
    canvas_x, canvas_y = to_canvas(canvas, point.x, point.y)
    radius = to_canvas_length(canvas, 5)
    return canvas.create_oval(
        canvas_x - radius,
        canvas_y - radius,
        canvas_x + radius,
        canvas_y + radius,
        fill="blue",
        tags="point",
    )
