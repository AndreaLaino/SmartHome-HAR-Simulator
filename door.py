from read import read_doors
from models import Door
from canvas_zoom import event_to_logical, to_canvas

doors = []


def add_door_between_points(canvas, point1, point2, load_active, on_changed=None):
    """Create a door whose endpoints are attached to two existing points."""
    if point1 is point2 or (point1.x, point1.y) == (point2.x, point2.y):
        return None

    target = read_doors if load_active else doors
    endpoint_pair = frozenset(((point1.x, point1.y), (point2.x, point2.y)))
    if any(
        frozenset(((door.x1, door.y1), (door.x2, door.y2))) == endpoint_pair
        for door in target
    ):
        return None

    door = Door(
        x1=point1.x,
        y1=point1.y,
        x2=point2.x,
        y2=point2.y,
        state="close",
    )
    target.append(door)
    draw_door(canvas, door)
    if callable(on_changed):
        on_changed()
    return door


def draw_door(canvas, door):
    x1, y1 = to_canvas(canvas, door.x1, door.y1)
    x2, y2 = to_canvas(canvas, door.x2, door.y2)
    if door.state == 'close':
        canvas.create_line(x1, y1, x2, y2, fill="green", width=4, tags="door")
    else:
        canvas.create_line(x1, y1, x2, y2, fill="grey", width=4, dash=(4, 2), tags="door")


def draw_all_doors(canvas, doors):
    canvas.delete("door")
    for door in doors:
        draw_door(canvas, door)
    if getattr(canvas, "_show_pir_fov", False):
        from utils import refresh_pir_fov

        refresh_pir_fov(canvas)


def interaction_with_door(canvas, event, doors, *, render=True):
    x, y = event_to_logical(canvas, event)

    tolerance = 5  # defines the maximum click distance from the door that can be tolerated.

    for index, door in enumerate(doors):
        if point_in_line(x, y, door.x1, door.y1, door.x2, door.y2, tolerance):
            print(f"Interaction with door {index} at coordinates ({door.x1}, {door.y1}), ({door.x2}, {door.y2}) with state {door.state}")

            # change the door state
            toggle_door_state(index, doors)
            if render:
                draw_all_doors(canvas, doors)
            return True

    return False

def point_in_line(px, py, x1, y1, x2, y2, tolerance):
    line_mag = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    if line_mag < tolerance:
        return False
    u = ((px - x1) * (x2 - x1) + (py - y1) * (y2 - y1)) / (line_mag ** 2)
    if u < 0 or u > 1:
        return False
    ix = x1 + u * (x2 - x1)
    iy = y1 + u * (y2 - y1)
    dist = ((px - ix) ** 2 + (py - iy) ** 2) ** 0.5
    return dist < tolerance

def toggle_door_state(index, doors):
    if 0 <= index < len(doors):  # check if index is valid
        door = doors[index]
        new_state = 'open' if door.state == 'close' else 'close'
        print(f"Toggled door {index} state from {door.state} to {new_state}")
        door.state = new_state
    else:
        print(f"Door index not valid: {index}")
