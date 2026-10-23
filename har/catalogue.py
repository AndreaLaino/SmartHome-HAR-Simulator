"""Shared object vocabulary, independent of activity labels or recognition rules."""

from __future__ import annotations

from copy import deepcopy

from har import storage


MEASUREMENTS = {"presence", "power", "contact", "weight", "temperature", "water_flow", "generic"}


def default_catalogue() -> dict:
    return {
        "schema_version": 1,
        "device_categories": {
            "DeviceCooking": "Appliance used to prepare or heat food.",
            "DeviceEntertainment": "Equipment used for entertainment.",
            "DeviceComputer": "General-purpose computing equipment.",
            "DeviceCleaning": "Equipment used to clean clothing, dishes or rooms.",
            "DeviceFoodStorage": "Equipment used to preserve food.",
            "DeviceBeverage": "Equipment used to prepare beverages.",
            "DeviceLighting": "Light source, independent of control or meter type.",
            "DeviceClimate": "Equipment used to heat, cool or ventilate a room.",
            "DeviceBed": "Bed or other sleeping fixture.",
            "DeviceSeating": "Chair, sofa or other seating fixture.",
            "DeviceWaterFixture": "Fixture that supplies water.",
            "DeviceToilet": "Toilet fixture.",
        },
        "sensor_categories": {
            "SensorPresence": {"description": "Presence observation.", "measurement": "presence", "unit": "binary"},
            "SensorPower": {"description": "Instantaneous electrical power.", "measurement": "power", "unit": "W"},
            "SensorContact": {"description": "Open/closed or contact observation.", "measurement": "contact", "unit": "binary"},
            "SensorWeight": {"description": "Weight or load observation; use the configured sensor scale.", "measurement": "weight", "unit": "configured"},
            "SensorTemperature": {"description": "Temperature observation.", "measurement": "temperature", "unit": "°C"},
            "SensorWaterFlow": {"description": "Water flow observation; use the configured sensor unit.", "measurement": "water_flow", "unit": "configured"},
            "SensorGeneric": {"description": "Other numeric observation.", "measurement": "generic", "unit": "configured"},
        },
        "device_types": {
            "Oven": ["DeviceCooking"], "Microwave": ["DeviceCooking"],
            "Toaster": ["DeviceCooking"], "Stove": ["DeviceCooking"],
            "Cooktop": ["DeviceCooking"], "Air Fryer": ["DeviceCooking"],
            "TV": ["DeviceEntertainment"], "Television": ["DeviceEntertainment"],
            "PlayStation": ["DeviceEntertainment"], "Console": ["DeviceEntertainment"],
            "Computer": ["DeviceComputer", "DeviceEntertainment"],
            "PC": ["DeviceComputer", "DeviceEntertainment"],
            "Washing_Machine": ["DeviceCleaning"], "Washing Machine": ["DeviceCleaning"],
            "Dishwasher": ["DeviceCleaning"], "Vacuum Cleaner": ["DeviceCleaning"],
            "Fridge": ["DeviceFoodStorage"], "Freezer": ["DeviceFoodStorage"],
            "Coffee_Machine": ["DeviceBeverage"], "Coffee Machine": ["DeviceBeverage"],
            "Kettle": ["DeviceBeverage"],
            "Normal Light": ["DeviceLighting"], "Smart Light": ["DeviceLighting"],
            "Lighting": ["DeviceLighting"],
            "Air Conditioner": ["DeviceClimate"], "Heater": ["DeviceClimate"],
            "Fan": ["DeviceClimate"], "Bed": ["DeviceBed"],
            "Sofa": ["DeviceSeating"], "Chair": ["DeviceSeating"],
            "Shower": ["DeviceWaterFixture"], "Sink": ["DeviceWaterFixture"],
            "Toilet": ["DeviceToilet"],
        },
        "sensor_types": {
            "PIR": ["SensorPresence"], "Presence": ["SensorPresence"],
            "Smart Meter": ["SensorPower"], "Smartmeter": ["SensorPower"],
            "Switch": ["SensorContact"], "Contact": ["SensorContact"],
            "Door Contact": ["SensorContact"], "Weight": ["SensorWeight"],
            "Temperature": ["SensorTemperature"], "Water Flow": ["SensorWaterFlow"],
            "Water_Flow": ["SensorWaterFlow"],
        },
    }


def _kind(kind: str) -> str:
    if kind not in ("device", "sensor"):
        raise ValueError("Object kind must be device or sensor")
    return kind


def validate_categories(names, kind: str, catalogue: dict | None = None) -> list[str]:
    kind = _kind(kind)
    catalogue = load_catalogue() if catalogue is None else catalogue
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("Categories must be a list of names")
    available = catalogue[f"{kind}_categories"]
    result = []
    for name in names:
        name = name.strip()
        if name not in available:
            raise ValueError(f"Unknown {kind} category: {name}")
        if name not in result:
            result.append(name)
    return result


def validate_catalogue(catalogue: dict) -> dict:
    if not isinstance(catalogue, dict) or catalogue.get("schema_version") != 1:
        raise ValueError("Unsupported category dictionary schema")
    for kind in ("device", "sensor"):
        definitions = catalogue.get(f"{kind}_categories")
        types = catalogue.get(f"{kind}_types")
        if not isinstance(definitions, dict) or not isinstance(types, dict):
            raise ValueError(f"Missing {kind} dictionary definitions")
        for name, definition in definitions.items():
            if not isinstance(name, str) or not name.strip() or name != name.strip():
                raise ValueError("Category names must be nonempty and trimmed")
            if kind == "device":
                if not isinstance(definition, str):
                    raise ValueError("Device categories need a text description")
            elif (
                not isinstance(definition, dict)
                or not isinstance(definition.get("description"), str)
                or definition.get("measurement") not in MEASUREMENTS
                or not isinstance(definition.get("unit"), str)
                or not definition["unit"].strip()
            ):
                raise ValueError("Sensor categories need description, canonical measurement and unit")
        seen = set()
        for name, categories in types.items():
            if not isinstance(name, str) or not name.strip() or name != name.strip():
                raise ValueError("Device and sensor types must be nonempty and trimmed")
            normalized = name.casefold()
            if normalized in seen:
                raise ValueError("Type names must be unique regardless of case")
            seen.add(normalized)
            validate_categories(categories, kind, catalogue)
    return deepcopy(catalogue)


def load_catalogue() -> dict:
    path = storage.ensure_data() / "catalogue.json"
    return validate_catalogue(storage.read_json(path))


def save_catalogue(catalogue: dict) -> dict:
    saved = validate_catalogue(catalogue)
    storage.atomic_json(storage.ensure_data() / "catalogue.json", saved)
    return saved


def validate_catalogue_compatibility(source: dict, target: dict) -> None:
    """Require the same physical meaning for shared sensor category channels."""
    validate_catalogue(source)
    validate_catalogue(target)
    shared = set(source["sensor_categories"]) & set(target["sensor_categories"])
    for name in sorted(shared):
        before = source["sensor_categories"][name]
        after = target["sensor_categories"][name]
        for field in ("measurement", "unit"):
            if before[field] != after[field]:
                raise ValueError(
                    f"Incompatible sensor category {name}: {field} is "
                    f"{before[field]!r} in the source dictionary and {after[field]!r} in the target. "
                    "Use a consistent measurement and unit across houses."
                )


def categories_for(obj, kind: str, catalogue: dict | None = None) -> list[str]:
    kind = _kind(kind)
    catalogue = load_catalogue() if catalogue is None else catalogue
    if isinstance(obj, dict):
        explicit = obj.get("categories")
        object_type = obj.get("type", "")
    else:
        explicit = getattr(obj, "categories", None)
        object_type = getattr(obj, "type", "")
    if explicit is not None:
        return validate_categories(explicit, kind, catalogue)
    normalized = str(object_type).strip().casefold()
    for name, categories in catalogue[f"{kind}_types"].items():
        if name.casefold() == normalized:
            return list(categories)
    return []
