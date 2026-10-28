"""House-independent measurements and past-only temporal features.

Categories select input channels, never activities. No activity rules, predicted
labels, device names, or future observations enter a feature vector.
"""

from __future__ import annotations

import math
from collections import defaultdict

from har.catalogue import categories_for, category_name


ROOMS = ("unknown", "kitchen", "livingroom", "bedroom", "bathroom", "office", "hallway", "diningroom", "laundry", "other")


def number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def latest(data, key, default=None):
    value = data.get(key)
    if isinstance(value, (tuple, list)):
        value = value[-1] if value else None
    return number(value, default) if value is not None else default


def _room_type(value):
    key = "".join(char for char in str(value).casefold() if char.isalnum())
    return key if key in ROOMS else "other"


def _room_at(obj, rooms):
    from room_recognition import point_in_polygon
    for room in rooms:
        if point_in_polygon(number(obj.get("x")), number(obj.get("y")), room.get("polygon", [])):
            return _room_type(room.get("room_type", "unknown"))
    return "unknown"


def primitive_names(catalogue):
    names = []
    for category in sorted(catalogue["device_categories"]):
        names.extend(f"device/{category}/{key}" for key in
                     ("available", "on_fraction", "power_w", "power_fraction", "power_observed"))
    for category in sorted(catalogue["sensor_categories"]):
        names.extend(f"sensor/{category}/{key}" for key in ("available", "observed", "value", "normalized"))
        names.extend(f"room/{room}/{category}/{key}" for room in ROOMS for key in ("observed", "normalized"))
    return names


def _normalized(value, sensor):
    lower, upper = number(sensor.get("min_val")), number(sensor.get("max_val"), 1)
    if upper > lower:
        return max(0.0, min(1.0, (value - lower) / (upper - lower)))
    return value


def _known_categories(obj, kind, catalogue):
    # Frames are validated against their own dictionary at capture/evaluation.
    # Additional target categories must not remove its already known channels.
    explicit = obj.get("categories")
    if explicit is not None:
        resolved = (category_name(name, kind, catalogue) for name in explicit)
        return list(dict.fromkeys(name for name in resolved if name in catalogue[f"{kind}_categories"]))
    return categories_for(obj, kind, catalogue)


def primitives(frame, catalogue):
    """Aggregate current readings by category, with a frozen source schema."""
    pools = defaultdict(list)
    device_power = defaultdict(list)
    observations = frame.get("sensor_states", {})
    rooms = frame.get("rooms", [])
    for sensor in frame.get("sensors", []):
        data = observations.get(sensor["name"], {})
        for category in _known_categories(sensor, "sensor", catalogue):
            if category not in catalogue["sensor_categories"]:
                continue
            definition = catalogue["sensor_categories"][category]
            measurement = definition["measurement"]
            # Power in watts is authoritative, including an explicit zero.
            value = latest(data, "consumption") if measurement == "power" else None
            if value is None:
                value = latest(data, "state")
            if value is None:
                value = number(sensor.get("consumption"), None) if measurement == "power" else number(sensor.get("state"), None)
            key = f"sensor/{category}"
            pools[f"{key}/available"].append(1.0)
            pools[f"{key}/observed"].append(float(value is not None))
            room_key = f"room/{_room_at(sensor, rooms)}/{category}"
            pools[f"{room_key}/observed"].append(float(value is not None))
            if value is None:
                continue
            pools[f"{key}/value"].append(value)
            normalized = _normalized(value, sensor)
            pools[f"{key}/normalized"].append(normalized)
            pools[f"{room_key}/normalized"].append(normalized)
            if measurement == "power" and sensor.get("associated_device"):
                device_power[sensor["associated_device"]].append(max(0.0, value))
    for device in frame.get("devices", []):
        meter_values = device_power.get(device["name"])
        stored_power = number(device.get("current_consumption"), None)
        # Multiple whole-appliance meters observe the same load. Keep the
        # largest reading, independent of sensor names, rather than doubling it.
        power = max(meter_values) if meter_values is not None else stored_power
        rated = max(number(device.get("power")), number(device.get("max_consumption")))
        for category in _known_categories(device, "device", catalogue):
            if category not in catalogue["device_categories"]:
                continue
            key = f"device/{category}"
            pools[f"{key}/available"].append(1.0)
            pools[f"{key}/on_fraction"].append(float(number(device.get("state")) > 0))
            pools[f"{key}/power_observed"].append(float(power is not None))
            if power is not None:
                pools[f"{key}/power_w"].append(max(0.0, power))
                pools[f"{key}/power_fraction"].append(max(0.0, power) / rated if rated > 0 else 0.0)
    return [sum(pools[name]) / len(pools[name]) if pools[name] else 0.0 for name in primitive_names(catalogue)]


class FeatureEncoder:
    """Independent history per simulation/session; elapsed units are sim seconds."""

    def __init__(self, catalogue, window_seconds=600.0):
        self.catalogue = catalogue
        self.window_seconds = number(window_seconds, 0)
        if self.window_seconds <= 0:
            raise ValueError("The observation window must be positive simulated seconds.")
        self.names = primitive_names(catalogue)
        self.feature_names = [f"{name}/{stat}" for name in self.names
                              for stat in ("latest", "mean", "changes", "age_since_change")]
        self.feature_names.append("observed_window_seconds")
        self.history = []
        self.last_change = []

    def update(self, frame):
        now = number(frame.get("time_seconds"), -1)
        if now < 0 or (self.history and now < self.history[-1][0]):
            raise ValueError("Observation timestamps must be finite, nonnegative and ordered.")
        values = primitives(frame, self.catalogue)
        if not self.history:
            self.last_change = [now] * len(values)
        else:
            for index, (before, after) in enumerate(zip(self.history[-1][1], values)):
                if abs(before - after) > 1e-9:
                    self.last_change[index] = now
        # Repeated callbacks at one instant replace, rather than lengthen, history.
        if self.history and now == self.history[-1][0]:
            self.history[-1] = (now, values)
        else:
            self.history.append((now, values))
        while len(self.history) > 1 and self.history[1][0] <= now - self.window_seconds:
            self.history.pop(0)
        start = max(self.history[0][0], now - self.window_seconds)
        elapsed = now - start
        means, changes = [0.0] * len(values), [0.0] * len(values)
        for index, (stamp, observed) in enumerate(self.history):
            end = self.history[index + 1][0] if index + 1 < len(self.history) else now
            duration = max(0.0, min(end, now) - max(stamp, start))
            for column, value in enumerate(observed):
                means[column] += value * duration
                if index and stamp > start and abs(value - self.history[index - 1][1][column]) > 1e-9:
                    changes[column] += 1
        vector = []
        for column, value in enumerate(values):
            vector.extend((value, means[column] / elapsed if elapsed else value, changes[column],
                           min(self.window_seconds, now - self.last_change[column])))
        vector.append(elapsed)
        return vector


def encode_session(session, catalogue, window_seconds):
    encoder = FeatureEncoder(catalogue, window_seconds)
    rows = []
    for frame in session.get("frames", []):
        vector = encoder.update(frame)
        if frame.get("labels") is not None:
            rows.append((vector, list(frame["labels"])))
    return encoder.feature_names, rows
