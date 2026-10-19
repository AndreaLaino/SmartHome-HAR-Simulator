import tkinter as tk
from utils import draw_sensor, raise_overlay_labels
from label_layout import create_map_label
from canvas_zoom import to_canvas, to_canvas_length
import csv
import io
from models import Point, Sensor, Device, Door, Wall, LightSwitch
from lighting import LIGHT_CONTROLS, LIGHT_TYPES
import json
from device_properties import normalize_properties, validate_weight

# Global lists to save data read from file
coordinates: list[Point] = []
read_walls: list[Wall] = []
read_sensors: list[Sensor] = []
read_devices: list[Device] = []
read_doors: list[Door] = []
read_room_overrides: dict[str, str] = {}
read_recognition_config: dict = {}
read_recognition_link: dict = {}

def read_coordinates_from_file(file_path):
    coordinates.clear()
    read_walls.clear()
    read_sensors.clear()
    read_devices.clear()
    read_doors.clear()
    read_room_overrides.clear()
    read_recognition_config.clear()
    read_recognition_link.clear()

    # Variable to track the current section
    current_section = None
    pending_walls: list[tuple[str, str]] = []
    pending_switches = []
    explicit_switches = False
    pending_properties = {}
    pending_weights = {}
    pending_profiles = {}

    with open(file_path, "rb") as source:
        raw = source.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Earlier Windows releases saved using the default ANSI encoding.
        content = raw.decode("cp1252")

    with io.StringIO(content, newline="") as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            # Skip empty rows
            if not row:
                continue
            # Section markers occupy their own row. Object names may also be
            # words such as "Walls" or "Sensors".
            section_name = row[0].strip().lower() if all(not cell.strip() for cell in row[1:]) else None
            if section_name == "positions":
                current_section = "Positions"
                continue
            elif section_name == "walls":
                current_section = "Walls"
                continue
            elif section_name == "sensors":
                current_section = "Sensors"
                continue
            elif section_name == "devices":
                current_section = "Devices"
                continue
            elif section_name == "doors":
                current_section = "Doors"
                continue
            elif section_name == "light switches":
                current_section = "Light Switches"
                explicit_switches = True
                continue
            elif section_name == "rooms":
                current_section = "Rooms"
                continue
            elif section_name in {"properties", "recognition weights", "activity recognition", "property profiles", "shared recognition"}:
                current_section = section_name
                continue

            if current_section == "Positions":
                try:
                    name_p, x_p, y_p = row
                    x_p = int(x_p)
                    y_p = int(y_p)
                    coordinates.append(Point(name=name_p, x=x_p, y=y_p))
                except ValueError:
                    print(f"Error in Positions row: {row}")

            elif current_section == "Walls":
                try:
                    point1, point2 = row
                    pending_walls.append((point1, point2))
                except ValueError:
                    print(f"Error in Walls row: {row}")

            elif current_section == "Sensors":
                try:
                    if len(row) < 11:
                        print(f"Sensors row incomplete: {row}")
                        continue
                    (name_s, x_s, y_s, type, min_val, max_val, step, state_s,
                     direction, consumption, associated_device) = row
                    x_s = int(x_s)
                    y_s = int(y_s)
                    min_val = float(min_val)
                    max_val = float(max_val)
                    step = float(step)
                    state_s = float(state_s)
                    if type in ("PIR", "Weight", "Water Flow"):
                        state_s = 0.0
                    # the direction field: if "None" or empty, set None, otherwise convert to float
                    direction = None if direction in ("", "None") else float(direction)
                    # the consumption field: if "None"or empty, set None, otherwise converted to float
                    consumption = None if consumption in ("", "None") else float(consumption)
                    if type == "Smart Meter":
                        state_s = 0.0
                        consumption = 0.0
                    read_sensors.append(
                        Sensor(
                            name=name_s,
                            x=x_s,
                            y=y_s,
                            type=type,
                            min_val=min_val,
                            max_val=max_val,
                            step=step,
                            state=state_s,
                            direction=direction,
                            consumption=consumption,
                            associated_device=None if associated_device in ("", "None") else associated_device,
                        )
                    )
                except ValueError:
                    print(f"Error in Sensors row: {row}")

            elif current_section == "Devices":
                try:
                    if len(row) < 10:
                        print(f"Devices row incomplete: {row}")
                        continue
                    (name_d, x_d, y_d, type_d, power, state_d, min_consumption,
                     max_consumption, current_consumption, consumption_direction) = row[:10]
                    light_control = row[10].strip().lower() if len(row) > 10 else "manual"
                    if len(row) > 11 or light_control not in LIGHT_CONTROLS:
                        raise ValueError("Invalid light control mode")
                    x_d = int(x_d)
                    y_d = int(y_d)
                    power = float(power)
                    state_d = int(float(state_d))
                    min_consumption = float(min_consumption)
                    max_consumption = float(max_consumption)
                    current_consumption = int(float(current_consumption))
                    try:
                        consumption_direction = int(float(consumption_direction))
                    except Exception:
                        consumption_direction = 1
                    state_d = 0
                    current_consumption = 0
                    consumption_direction = 1
                    read_devices.append(
                        Device(
                            name=name_d,
                            x=x_d,
                            y=y_d,
                            type=type_d,
                            power=power,
                            state=state_d,
                            min_consumption=min_consumption,
                            max_consumption=max_consumption,
                            current_consumption=current_consumption,
                            consumption_direction=consumption_direction,
                            light_control=light_control,
                        )
                    )
                except ValueError:
                    print(f"Error Devices row: {row}")

            elif current_section == "Doors":
                try:
                    x1_p, y1_p, x2_p, y2_p, state_p = row
                    x1_p = int(x1_p)
                    y1_p = int(y1_p)
                    x2_p = int(x2_p)
                    y2_p = int(y2_p)
                    read_doors.append(Door(x1=x1_p, y1=y1_p, x2=x2_p, y2=y2_p, state=state_p))
                except ValueError:
                    print(f"Error in Doors row: {row}")

            elif current_section == "Light Switches":
                try:
                    light_name, switch_name, switch_x, switch_y = row
                    if not switch_name.strip():
                        raise ValueError("Empty switch name")
                    pending_switches.append((light_name, switch_name, int(switch_x), int(switch_y)))
                except ValueError:
                    print(f"Error in Light Switches row: {row}")

            elif current_section == "properties":
                kind, name, prop, weight = row
                pending_properties.setdefault((kind, name), {})[prop] = validate_weight(weight)
            elif current_section == "recognition weights":
                kind, name, weight = row
                pending_weights[(kind, name)] = validate_weight(weight)
            elif current_section == "property profiles":
                kind, name, profile = row
                pending_profiles[(kind, name)] = profile or None
            elif current_section == "shared recognition":
                link = json.loads(row[0])
                if not isinstance(link, dict):
                    raise ValueError("Invalid shared library link.")
                read_recognition_link.update(link)
            elif current_section == "activity recognition":
                from activity_recognition import recognition_settings
                config = json.loads(row[0])
                recognition_settings(config)
                read_recognition_config.update(config)
            elif current_section == "Rooms":
                if len(row) >= 2:
                    room_id = row[0].strip()
                    room_type = row[1].strip()
                    if room_id and room_type:
                        read_room_overrides[room_id] = room_type

    for kind, objects in (("device", read_devices), ("sensor", read_sensors)):
        for obj in objects:
            if (kind, obj.name) in pending_properties:
                obj.properties = normalize_properties(pending_properties[(kind, obj.name)])
            obj.recognition_weight = pending_weights.get((kind, obj.name), 1.0)
            obj.recognition_profile = pending_profiles.get((kind, obj.name))

    if explicit_switches:
        lights_by_name = {device.name: device for device in read_devices if device.type in LIGHT_TYPES}
        for light in lights_by_name.values():
            light.light_switches = []
        used_switch_names = set()
        for light_name, switch_name, switch_x, switch_y in pending_switches:
            if light_name in lights_by_name and switch_name not in used_switch_names:
                lights_by_name[light_name].light_switches.append(LightSwitch(switch_name, switch_x, switch_y, light_name))
                used_switch_names.add(switch_name)

    point_by_name = {point.name: point for point in coordinates}
    for point1, point2 in pending_walls:
        p1 = point_by_name.get(point1)
        p2 = point_by_name.get(point2)
        if p1 is None or p2 is None:
            print(f"Coordinates not found: {point1}, {point2}")
            continue
        read_walls.append(Wall(x1=p1.x, y1=p1.y, x2=p2.x, y2=p2.y))
    return coordinates, read_walls, read_sensors, read_devices, read_doors

def draw_points(coordinates, canvas):
    for point in coordinates:
        x, y = point.x, point.y
        canvas_x, canvas_y = to_canvas(canvas, x, y)
        radius = to_canvas_length(canvas, 5)
        canvas.create_oval(
            canvas_x - radius,
            canvas_y - radius,
            canvas_x + radius,
            canvas_y + radius,
            fill="blue",
            tags='point',
        )

read_walls_coordinates: list[Wall] = []

def draw_walls(read_walls, coordinates, canvas):
    read_walls_coordinates.clear()
    for wall in read_walls:
        x1, y1 = to_canvas(canvas, wall.x1, wall.y1)
        x2, y2 = to_canvas(canvas, wall.x2, wall.y2)
        canvas.create_line(x1, y1, x2, y2, fill="black", width=3, tags='wall')
        read_walls_coordinates.append(wall)
    raise_overlay_labels(canvas)

def draw_sensors(read_sensors, canvas):
    for sensor in read_sensors:
        draw_sensor(canvas, sensor)

def draw_devices(read_devices, canvas):
    from device import draw_device

    for device in read_devices:
        draw_device(canvas, device)

def draw_doors(read_doors, canvas):
    for door in read_doors:
        x1, y1 = to_canvas(canvas, door.x1, door.y1)
        x2, y2 = to_canvas(canvas, door.x2, door.y2)
        if door.state == 'close':
            canvas.create_line(x1, y1, x2, y2, fill="green", width=5, tags="door")
        else:
            canvas.create_line(x1, y1, x2, y2, fill="grey", width=3, dash=(4, 2), tags="door")
