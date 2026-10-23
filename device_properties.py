"""Weighted capabilities shared by device types, sensors and activity rules."""

import math
import re
from collections.abc import Mapping


def property_key(value):
    key = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    key = key.removesuffix("_device")
    return {"relax": "relaxing", "work": "working", "sleep": "sleeping",
            "cook": "cooking", "clean": "cleaning", "bathroom": "toileting",
            "shower": "showering"}.get(key, key)


def validate_weight(value):
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise ValueError("Weights must be finite numbers between 0 and 1.")
    return number


def normalize_properties(properties):
    if not isinstance(properties, Mapping) or not properties:
        raise ValueError("Assign at least one property.")
    result = {}
    for name, weight in properties.items():
        key = property_key(name)
        if not key:
            raise ValueError("Property names cannot be empty.")
        result[key] = validate_weight(weight)
    return result


def parse_properties(text):
    result = {}
    for item in text.split(","):
        if not item.strip():
            continue
        name, separator, weight = item.partition("=")
        result[name.strip()] = float(weight) if separator else 1.0
    return normalize_properties(result)


def format_properties(properties):
    return ", ".join(f"{name}={weight:g}" for name, weight in sorted(properties.items()))


# Values are editable recognition strengths, not measured probabilities.
DEVICE_PROPERTIES = {
    "Fridge": {"food_storage": 1},
    "Washing_Machine": {"cleaning": 1, "laundry": 1},
    "Oven": {"cooking": 1, "heating": .8},
    "Microwave": {"cooking": 1, "heating": .8},
    "Coffee_Machine": {"drink_preparation": 1},
    "Computer": {"working": .85, "relaxing": .55},
    "TV": {"relaxing": 1},
    "PlayStation": {"relaxing": 1, "gaming": 1},
    "Dishwasher": {"cleaning": 1, "dishwashing": 1},
    "Vacuum_Cleaner": {"cleaning": 1},
    "Kettle": {"drink_preparation": 1, "heating": .8},
    "Toaster": {"cooking": 1},
    "Shower": {"showering": 1, "hygiene": 1},
    "Toilet": {"toileting": 1, "hygiene": .8},
    "Sink": {"washing_hands": 1, "hygiene": .8},
    "Bed": {"sleeping": 1},
    "Dining_Table": {"eating": 1},
    "Sofa": {"relaxing": .7, "seating": 1},
    "Hair_Dryer": {"grooming": 1},
    "Normal Light": {"lighting": 1},
    "Smart Light": {"lighting": 1},
    "Lighting": {"lighting": 1},
}
SENSOR_PROPERTIES = {
    "PIR": {"presence": 1}, "Weight": {"weight": 1, "presence": .8},
    "Switch": {"contact": 1}, "Smart Meter": {"energy": 1},
    "Temperature": {"temperature": 1}, "Water Flow": {"water_flow": 1},
}
_DEVICE_TYPES = {property_key(name): name for name in DEVICE_PROPERTIES}
_DEVICE_TYPES.update({"pc": "Computer", "television": "TV", "console": "PlayStation",
                      "washing_machine": "Washing_Machine", "vacuum": "Vacuum_Cleaner"})


def canonical_device_type(device_type):
    return _DEVICE_TYPES.get(property_key(device_type), device_type)


def default_device_properties(device_type):
    return dict(DEVICE_PROPERTIES.get(canonical_device_type(device_type), {"device": 1}))


def default_sensor_properties(sensor_type):
    return dict(SENSOR_PROPERTIES.get(sensor_type, {"measurement": 1}))


def is_static_device(device_type):
    return canonical_device_type(device_type) in {
        "Microwave", "TV", "PlayStation", "Vacuum_Cleaner", "Kettle", "Toaster",
        "Shower", "Toilet", "Sink", "Bed", "Dining_Table", "Sofa", "Hair_Dryer",
    }


STATIC_DEVICE_POWER = {
    "Microwave": 1000, "TV": 120, "PlayStation": 180, "Vacuum_Cleaner": 800,
    "Kettle": 1800, "Toaster": 800, "Hair_Dryer": 1500,
    # These are fixtures/furniture; usage is observed by contact, weight or flow.
    "Shower": 0, "Toilet": 0, "Sink": 0, "Bed": 0, "Dining_Table": 0, "Sofa": 0,
}
