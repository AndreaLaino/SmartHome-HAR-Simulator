"""Light types and their built-in metering, derived from the owning device."""

from models import LightSwitch, Sensor


NORMAL_LIGHT = "Normal Light"
SMART_LIGHT = "Smart Light"
LIGHT_TYPES = frozenset(("Lighting", NORMAL_LIGHT, SMART_LIGHT))
LIGHT_CONTROLS = ("manual", "automatic")


def ensure_light_switches(light):
    if light.type not in LIGHT_TYPES:
        return []
    if light.light_switches is None:
        light.light_switches = []
        if light.light_control == "manual":
            add_light_switch(light)
    for switch in light.light_switches:
        switch.associated_device = light.name
    return light.light_switches


def add_light_switch(light, x=None, y=None):
    if light.light_switches is None:
        light.light_switches = []
    switches = light.light_switches
    base = f"switch_{light.name}"
    name = base
    number = 2
    while any(switch.name == name for switch in switches):
        name = f"{base}_{number}"
        number += 1
    switch = LightSwitch(name, light.x - 25 * (len(switches) + 1) if x is None else x,
                         light.y + 25 if y is None else y, light.name)
    switches.append(switch)
    return switch


def light_switch_position(light):
    switches = ensure_light_switches(light)
    return (switches[0].x, switches[0].y) if switches else (light.x - 25, light.y + 25)


def manual_light_at_switch(devices, x, y):
    for light in devices:
        if light.type not in LIGHT_TYPES or light.light_control != "manual":
            continue
        if any(abs(x - switch.x) <= 7 and abs(y - switch.y) <= 7 for switch in ensure_light_switches(light)):
            return light
    return None


def automatic_light_states(devices, sensors, rooms, avatar_position, walls=(), doors=()):
    """Resolve presence per room, with nearby visibility as an open-plan fallback."""
    from room_recognition import point_in_polygon
    from utils import PIR_MAX_DISTANCE, calculate_distance, is_path_blocked_by_walls

    ordered_rooms = sorted(rooms, key=lambda room: room.room_id)

    def room_at(x, y):
        return next((room for room in ordered_rooms if point_in_polygon(x, y, room.polygon)), None)

    # The avatar is the authoritative presence source during local simulation.
    # Without an avatar, active PIR readings can drive automatic lights.
    positions = [avatar_position] if avatar_position is not None else [
        (sensor.x, sensor.y) for sensor in sensors if sensor.type == "PIR" and sensor.state > 0
    ]
    for light in devices:
        if light.type not in LIGHT_TYPES or light.light_control != "automatic":
            continue
        light_room = room_at(light.x, light.y)
        occupied = any(
            room_at(x, y) is light_room if light_room is not None else (
                calculate_distance(light.x, light.y, x, y) <= PIR_MAX_DISTANCE
                and not is_path_blocked_by_walls(light.x, light.y, x, y, walls, doors)
            )
            for x, y in positions
        )
        yield light, int(occupied)


def with_integrated_light_meters(sensors, devices):
    """Return sensor objects plus smart lights' meters without changing source lists.

    Built-in meters are runtime objects owned by a device. Saving the device type
    is sufficient to restore them; they have no independent canvas marker.
    """
    result = [sensor for sensor in sensors if not getattr(sensor, "integrated", False)]
    lights = [device for device in devices if getattr(device, "type", None) == SMART_LIGHT]
    if not lights:
        return result
    used_names = {sensor.name for sensor in result}
    for light in lights:
        base_name = f"smartmeter_{light.name}"
        meter_name = base_name
        suffix = 2
        while meter_name in used_names:
            meter_name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(meter_name)
        meter = light._integrated_smartmeter
        if meter is None:
            meter = Sensor(meter_name, light.x, light.y, "Smart Meter", 0, light.power,
                           1, 0, consumption=0.0, associated_device=light.name, integrated=True)
            light._integrated_smartmeter = meter
        meter.name = meter_name
        meter.x, meter.y = light.x, light.y
        meter.associated_device = light.name
        meter.max_val = float(light.power)
        meter.consumption = float(light.power) if light.state == 1 else 0.0
        meter.state = float(light.state)
        result.append(meter)
    return result
