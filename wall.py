from read import read_walls_coordinates, read_walls
from models import Wall
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
