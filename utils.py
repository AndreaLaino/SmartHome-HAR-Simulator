import tkinter as tk
import math
import os
import json
from typing import Optional

from consumption_profiles import consumption_profiles, get_device_consumption
from models import Sensor, Device, Point, Door, Wall
from label_layout import create_map_label, layout_map_labels
from canvas_zoom import to_canvas, to_canvas_length

# Cache to avoid re-reading CSVs repeatedly when drawing sensors.
_REAL_TEMP_CACHE: dict[str, bool] = {}


def _is_real_temperature_sensor(sensor_name: str, logs_dir: str = "devices") -> bool:
    """Return True if this Temperature sensor is backed by real DHT logs.

    We detect it by:
      1) Trying label-based CSV lookup (sensor_name).
      2) Falling back to sensor_map.json -> gpio lookup.
    """
    if not sensor_name:
        return False
    if sensor_name in _REAL_TEMP_CACHE:
        return _REAL_TEMP_CACHE[sensor_name]

    ok = False
    try:
        from app.hardware.real_sensors import load_temp_by_gpio_any_csv, load_temp_by_label_any_csv

        # 1) label lookup
        df = None
        try:
            df = load_temp_by_label_any_csv(sensor_name, logs_dir=logs_dir)
        except TypeError:
            # older signature (no logs_dir)
            df = load_temp_by_label_any_csv(sensor_name)
        if df is not None and not df.empty and "value" in df.columns:
            ok = True
        else:
            # 2) gpio lookup via sensor_map.json
            mapping_path = "sensor_map.json"
            if os.path.isfile(mapping_path):
                with open(mapping_path, "r", encoding="utf-8") as f:
                    mapping = json.load(f) if f else {}
                cfg = mapping.get(sensor_name, {}) if isinstance(mapping, dict) else {}
                if isinstance(cfg, dict) and cfg.get("by") == "dht":
                    gpio = cfg.get("gpio")
                    if gpio is not None:
                        try:
                            df2 = load_temp_by_gpio_any_csv(int(gpio), logs_dir=logs_dir)
                        except TypeError:
                            df2 = load_temp_by_gpio_any_csv(int(gpio))
                        if df2 is not None and not df2.empty and "value" in df2.columns:
                            ok = True
    except Exception:
        ok = False

    _REAL_TEMP_CACHE[sensor_name] = ok
    return ok


def _temperature_color(sensor_name: str, changing: bool = False) -> str:
    """Color rules for Temperature sensors.

    - Red by default
    - Green only while changing
    """
    return "green" if changing else "red"


def draw_sensor(canvas, sensor):
    name, x, y, type_s, min_val, state = sensor.name, sensor.x, sensor.y, sensor.type, sensor.min_val, sensor.state
    
    # Default coloring:
    #   - Temperature sensors: red by default; green only when changing
    #   - Other sensors: keep legacy behavior (green if above min)
    if type_s == "Temperature":
        # At draw time we don't know if it's "changing" yet -> show red (or cyan if real)
        color = _temperature_color(name, changing=False)
    else:
        color = "green" if float(state) > float(min_val) else "red"
    rect_tag = f'{name}_rect_sensor'
    text_tag = f'{name}_text_sensor'
    canvas_x, canvas_y = to_canvas(canvas, x, y)
    radius = to_canvas_length(canvas, 5)
    marker_id = canvas.create_rectangle(
        canvas_x - radius,
        canvas_y - radius,
        canvas_x + radius,
        canvas_y + radius,
        fill=color,
        tags=('sensor', rect_tag),
    )
    create_map_label(
        canvas,
        x,
        y,
        text=name,
        fill=color,
        tags=('sensor', 'sensor_label', text_tag),
        kind="sensor",
        font=("Helvetica", 9),
        hover_target=marker_id,
    )
    raise_overlay_labels(canvas)


def raise_overlay_labels(canvas):
    if canvas is None:
        return
    canvas.tag_raise('sensor_label')
    canvas.tag_raise('device_label')
    layout_map_labels(canvas)

def calculate_distance(x1, y1, x2, y2):
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)

def draw_fov(canvas, x, y, max_distance, fov_angle, direction):
    direction_rad = math.radians(direction)
    fov_half_angle_rad = math.radians(fov_angle / 2)
    vertex1_x = x + max_distance * math.cos(direction_rad - fov_half_angle_rad)
    vertex1_y = y + max_distance * math.sin(direction_rad - fov_half_angle_rad)
    vertex2_x = x + max_distance * math.cos(direction_rad + fov_half_angle_rad)
    vertex2_y = y + max_distance * math.sin(direction_rad + fov_half_angle_rad)
    canvas.delete('fov')
    x, y = to_canvas(canvas, x, y)
    vertex1_x, vertex1_y = to_canvas(canvas, vertex1_x, vertex1_y)
    vertex2_x, vertex2_y = to_canvas(canvas, vertex2_x, vertex2_y)
    canvas.create_polygon(x, y, vertex1_x, vertex1_y, vertex2_x, vertex2_y,
                          fill="", outline="blue", width=2, tags='fov')

def get_nearby_device_states(sensor, devices, walls, doors, max_distance=100):
    x1, y1 = sensor.x, sensor.y
    
    nearby_device_states = []
    for device in devices:
        dx, dy, state = device.x, device.y, device.state
        
        if calculate_distance(x1, y1, dx, dy) <= max_distance:
            if not is_path_blocked_by_walls(x1, y1, dx, dy, walls, doors):
                nearby_device_states.append(state)
    return nearby_device_states

def is_within_fov(sensor, x, y, max_distance, fov_angle):
    sx, sy, direction = sensor.x, sensor.y, sensor.direction
    
    dx, dy = x - sx, y - sy
    distance = math.hypot(dx, dy)
    if distance > max_distance:
        return False
    angle = math.degrees(math.atan2(dy, dx)) % 360
    if direction is not None:
        direction %= 360
        relative_angle = (angle - direction) % 360
        if relative_angle > 180:
            relative_angle -= 360
        return abs(relative_angle) <= fov_angle / 2
    return False

def find_closest_sensor_without_intersection(point, sensors, walls_coordinates):
    x1, y1 = point
    
    def sensor_distance(s):
        return calculate_distance(x1, y1, s.x, s.y)
    
    sensors_sorted = sorted(sensors, key=sensor_distance)
    for sensor in sensors_sorted:
        x2, y2 = sensor.x, sensor.y
        
        intersects = False
        for wall in walls_coordinates:
            if intersect(x1, y1, x2, y2, wall.x1, wall.y1, wall.x2, wall.y2):
                intersects = True
                break
        if not intersects:
            return sensor
    return None

def find_closest_sensor_within_fov(point, sensors, walls_coordinates, doors, max_distance, fov_angle):
    x, y = point
    visible_sensors = [s for s in sensors if is_within_fov(s, x, y, max_distance, fov_angle)]
    
    def sensor_distance(s):
        return calculate_distance(x, y, s.x, s.y)
    
    visible_sensors.sort(key=sensor_distance)
    for sensor in visible_sensors:
        sx, sy = sensor.x, sensor.y
        
        if not is_path_blocked_by_walls(sx, sy, x, y, walls_coordinates, doors):
            return sensor
    return None

def is_path_blocked_by_walls(x1, y1, x2, y2, walls_coordinates, doors):
    for wall in walls_coordinates:
        if intersect(x1, y1, x2, y2, wall.x1, wall.y1, wall.x2, wall.y2):
            return True
    for door in doors:
        if door.is_closed() and intersect(x1, y1, x2, y2, door.x1, door.y1, door.x2, door.y2):
            return True
    return False

def on_segment(x1, y1, x2, y2, x, y):
    return min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2)

def orientation(x1, y1, x2, y2, x3, y3):
    val = (y2 - y1) * (x3 - x2) - (x2 - x1) * (y3 - y2)
    if val == 0:
        return 0
    return 1 if val > 0 else 2

def intersect(x1, y1, x2, y2, x3, y3, x4, y4):
    o1 = orientation(x1, y1, x2, y2, x3, y3)
    o2 = orientation(x1, y1, x2, y2, x4, y4)
    o3 = orientation(x3, y3, x4, y4, x1, y1)
    o4 = orientation(x3, y3, x4, y4, x2, y2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and on_segment(x1, y1, x2, y2, x3, y3):
        return True
    if o2 == 0 and on_segment(x1, y1, x2, y2, x4, y4):
        return True
    if o3 == 0 and on_segment(x3, y3, x4, y4, x1, y1):
        return True
    if o4 == 0 and on_segment(x3, y3, x4, y4, x2, y2):
        return True
    return False

def find_switch_sensors_by_doors(doors, sensors):
    results = []
    for door in doors:
        x1, y1, x2, y2, state_p = door.x1, door.y1, door.x2, door.y2, door.state
        
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        associated_sensors = []
        for sensor in sensors:
            is_switch = sensor.type == "Switch"
            x, y = sensor.x, sensor.y
            
            if is_switch and calculate_distance(center_x, center_y, x, y) < 50:
                associated_sensors.append(sensor)
        
        if associated_sensors:
            results.append((door, associated_sensors, state_p))
    return results

def update_sensor_color(canvas, name, state, min_val):
    if canvas is None:
        return
    # Legacy rule for most sensors.
    color = "green" if float(state) > float(min_val) else "red"
    rect_tag = f'{name}_rect_sensor'
    text_tag = f'{name}_text_sensor'
    canvas.itemconfig(rect_tag, fill=color)
    canvas.itemconfig(text_tag, fill=color)


def update_temperature_sensor_color(canvas, name: str, *, changing: bool) -> None:
    """Explicit Temperature sensor color update.

    - Red by default
    - Green only while changing
    """
    if canvas is None:
        return
    color = _temperature_color(name, changing=changing)
    rect_tag = f'{name}_rect_sensor'
    text_tag = f'{name}_text_sensor'
    canvas.itemconfig(rect_tag, fill=color)
    canvas.itemconfig(text_tag, fill=color)

def update_devices_consumption(canvas, devices, delta_seconds, timer_app_instance=None, active_cycles_store=None):
    if timer_app_instance is None:
        print("Timer not provided to update_devices_consumption.")
        return

    from datetime import datetime

    if not isinstance(active_cycles_store, dict):
        active_cycles_store = {}

    # Rebuild simulated datetime
    simulated_time_str = timer_app_instance.get_simulated_time()
    current_date_str = timer_app_instance.current_date
    current_datetime = datetime.strptime(f"{current_date_str} {simulated_time_str}", "%Y-%m-%d %H:%M")

    for i in range(len(devices)):
        device = devices[i]
        name, dx, dy, type, power, state, min_c, max_c, current_cons, cons_dir = (
            device.name,
            device.x,
            device.y,
            device.type,
            device.power,
            device.state,
            device.min_consumption,
            device.max_consumption,
            device.current_consumption,
            device.consumption_direction,
        )

        if state == 1:
            if name in active_cycles_store:
                cycle_entry = active_cycles_store[name]
                if isinstance(cycle_entry, dict):
                    start_time = cycle_entry.get("start_time")
                    cycle_type = cycle_entry.get("cycle_type")
                    llm_meta = cycle_entry.get("llm")
                else:
                    start_time, cycle_type = cycle_entry
                    llm_meta = None
                if start_time is None or cycle_type is None:
                    continue
                elapsed_min = (current_datetime - start_time).total_seconds() / 60.0
                profile_duration = max(consumption_profiles[cycle_type]["profile"].keys())
                if isinstance(llm_meta, dict):
                    try:
                        llm_duration = float(llm_meta.get("duration_minutes"))
                        if llm_duration > 0:
                            profile_duration = llm_duration
                    except Exception:
                        pass

                # At end of profile: for non-continuous devices, turn OFF and close cycle.
                # Continuous: Refrigerator, Computer and Oven continue in duration module.
                if elapsed_min > profile_duration:
                    if cycle_type not in ["Fridge", "Computer", "Oven"]:
                        # Turn off the device and close the cycle
                        device.state = 0
                        device.current_consumption = 0
                        device.consumption_direction = 0
                        try:
                            del active_cycles_store[name]
                        except KeyError:
                            pass
                        if canvas is not None:
                            canvas.itemconfig(name, fill="red")
                        continue
                    else:
                        elapsed_min = elapsed_min % profile_duration  # continuous cycle

                # Calculate consumption
                current_consumption = get_device_consumption(
                    name, cycle_type, current_datetime, active_cycles_store, state
                )
                device.current_consumption = current_consumption
            else:
                # Device turned on but without active cycle: use profile of its type
                current_consumption = get_device_consumption(
                    name, type, current_datetime, active_cycles_store, state
                )
                device.current_consumption = current_consumption
        else:
            # if OFF, consumption is zero
            device.current_consumption = 0
