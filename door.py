import tkinter as tk
from point import points
from read import coordinates, read_doors
from models import Point, Door
from canvas_zoom import event_to_logical, to_canvas

doors = []

def draw_line_door(canvas, window, load_active):
    global doors
    if load_active:
        point = coordinates
    else:
        point = points

    def draw_line():
        point1 = point1_entry.get()
        point2 = point2_entry.get()

        # search for the coordinates of the points
        coord_point1 = None
        coord_point2 = None
        for p_point in point:
            p_name, p_x, p_y = p_point.name, p_point.x, p_point.y
            
            if p_name == point1:
                coord_point1 = (p_x, p_y)
            elif p_name == point2:
                coord_point2 = (p_x, p_y)

        if coord_point1 and coord_point2:
            door = Door(x1=coord_point1[0], y1=coord_point1[1], x2=coord_point2[0], y2=coord_point2[1], state="close")
            if load_active:
                read_doors.append(door)
            else:
                doors.append(door)
            draw_door(canvas, door)
            line_window.destroy()

    # window for points name
    line_window = tk.Toplevel(window)
    line_window.title("Add door")

    tk.Label(line_window, text="Door").pack()

    tk.Label(line_window, text="Point 1:").pack()
    point1_entry = tk.Entry(line_window)
    point1_entry.pack()

    tk.Label(line_window, text="Point 2:").pack()
    point2_entry = tk.Entry(line_window)
    point2_entry.pack()

    # draw line button
    tk.Button(line_window, text="Draw Line", command=draw_line).pack()


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
