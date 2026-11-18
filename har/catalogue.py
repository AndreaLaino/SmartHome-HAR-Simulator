"""Shared object vocabulary, independent of activity labels or recognition rules."""

from __future__ import annotations

from copy import deepcopy

from har import storage


MEASUREMENTS = {"presence", "power", "contact", "weight", "temperature", "water_flow", "generic"}
_CATEGORY_RENAMES = {
    f"{prefix}{suffix}": f"{prefix}_{suffix}"
    for prefix, suffixes in (
        ("Device", ("Cooking", "Entertainment", "Computer", "Cleaning", "FoodStorage", "Beverage",
                    "Lighting", "Climate", "Bed", "Seating", "WaterFixture", "Toilet", "Defrosting", "Interactive")),
        ("Sensor", ("Presence", "Power", "Contact", "Weight", "Temperature", "WaterFlow", "Generic")),
    )
    for suffix in suffixes
}


def default_catalogue() -> dict:
    return {
        "schema_version": 1,
        "device_categories": {
            "Device_Cooking": "Appliance used to prepare or heat food.",
            "Device_Entertainment": "Equipment used for entertainment.",
            "Device_Computer": "General-purpose computing equipment.",
            "Device_Cleaning": "Equipment used to clean clothing, dishes or rooms.",
            "Device_FoodStorage": "Equipment used to preserve food.",
            "Device_Beverage": "Equipment used to prepare beverages.",
            "Device_Lighting": "Light source, independent of control or meter type.",
            "Device_Climate": "Equipment used to heat, cool or ventilate a room.",
            "Device_Bed": "Bed or other sleeping fixture.",
            "Device_Seating": "Chair, sofa or other seating fixture.",
            "Device_WaterFixture": "Fixture that supplies water.",
            "Device_Toilet": "Toilet fixture.",
        },
        "sensor_categories": {
            "Sensor_Presence": {"description": "Presence observation.", "measurement": "presence", "unit": "binary"},
            "Sensor_Power": {"description": "Instantaneous electrical power.", "measurement": "power", "unit": "W"},
            "Sensor_Contact": {"description": "Open/closed or contact observation.", "measurement": "contact", "unit": "binary"},
            "Sensor_Weight": {"description": "Weight or load observation; use the configured sensor scale.", "measurement": "weight", "unit": "configured"},
            "Sensor_Temperature": {"description": "Temperature observation.", "measurement": "temperature", "unit": "°C"},
            "Sensor_WaterFlow": {"description": "Water flow observation; use the configured sensor unit.", "measurement": "water_flow", "unit": "configured"},
            "Sensor_Generic": {"description": "Other numeric observation.", "measurement": "generic", "unit": "configured"},
        },
        "device_types": {
            "Oven": ["Device_Cooking"], "Microwave": ["Device_Cooking"],
            "Toaster": ["Device_Cooking"], "Stove": ["Device_Cooking"],
            "Cooktop": ["Device_Cooking"], "Air Fryer": ["Device_Cooking"],
            "TV": ["Device_Entertainment"], "Television": ["Device_Entertainment"],
            "PlayStation": ["Device_Entertainment"], "Console": ["Device_Entertainment"],
            "Computer": ["Device_Computer", "Device_Entertainment"],
            "PC": ["Device_Computer", "Device_Entertainment"],
            "Washing_Machine": ["Device_Cleaning"], "Washing Machine": ["Device_Cleaning"],
            "Dishwasher": ["Device_Cleaning"], "Vacuum Cleaner": ["Device_Cleaning"],
            "Fridge": ["Device_FoodStorage"], "Freezer": ["Device_FoodStorage"],
            "Coffee_Machine": ["Device_Beverage"], "Coffee Machine": ["Device_Beverage"],
            "Kettle": ["Device_Beverage"],
            "Normal Light": ["Device_Lighting"], "Smart Light": ["Device_Lighting"],
            "Lighting": ["Device_Lighting"],
            "Air Conditioner": ["Device_Climate"], "Heater": ["Device_Climate"],
            "Fan": ["Device_Climate"], "Bed": ["Device_Bed"],
            "Sofa": ["Device_Seating"], "Chair": ["Device_Seating"],
            "Shower": ["Device_WaterFixture"], "Sink": ["Device_WaterFixture"],
            "Toilet": ["Device_Toilet"],
        },
        "sensor_types": {
            "PIR": ["Sensor_Presence"], "Presence": ["Sensor_Presence"],
            "Smart Meter": ["Sensor_Power"], "Smartmeter": ["Sensor_Power"],
            "Switch": ["Sensor_Contact"], "Contact": ["Sensor_Contact"],
            "Door Contact": ["Sensor_Contact"], "Weight": ["Sensor_Weight"],
            "Temperature": ["Sensor_Temperature"], "Water Flow": ["Sensor_WaterFlow"],
            "Water_Flow": ["Sensor_WaterFlow"],
        },
    }


def _kind(kind: str) -> str:
    if kind not in ("device", "sensor"):
        raise ValueError("Object kind must be device or sensor")
    return kind


def category_name(name: str, kind: str, catalogue: dict) -> str:
    """Resolve renamed built-ins into the requested dictionary's own spelling."""
    available = catalogue[f"{kind}_categories"]
    if name not in available:
        for before, after in _CATEGORY_RENAMES.items():
            if name == before and after in available:
                return after
            if name == after and before in available:
                return before
    return name


def validate_categories(names, kind: str, catalogue: dict | None = None) -> list[str]:
    kind = _kind(kind)
    catalogue = load_catalogue() if catalogue is None else catalogue
    if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
        raise ValueError("Categories must be a list of names")
    available = catalogue[f"{kind}_categories"]
    result = []
    for name in names:
        name = category_name(name.strip(), kind, catalogue)
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


def _current_catalogue(catalogue: dict) -> dict:
    """Upgrade the shared dictionary without rewriting frozen training data."""
    result = validate_catalogue(catalogue)
    for kind in ("device", "sensor"):
        definitions = result[f"{kind}_categories"]
        for before, after in _CATEGORY_RENAMES.items():
            if before not in definitions:
                continue
            if after in definitions and definitions[before] != definitions[after]:
                raise ValueError(f"Conflicting category definitions: {before} and {after}.")
            definitions[after] = definitions.pop(before)
        for object_type, names in result[f"{kind}_types"].items():
            result[f"{kind}_types"][object_type] = validate_categories(names, kind, result)
    return result


def load_catalogue() -> dict:
    path = storage.ensure_data() / "catalogue.json"
    original = storage.read_json(path)
    current = _current_catalogue(original)
    if current != original:
        storage.atomic_json(path, current)
    return current


def save_catalogue(catalogue: dict) -> dict:
    saved = _current_catalogue(catalogue)
    storage.atomic_json(storage.ensure_data() / "catalogue.json", saved)
    return saved


def validate_catalogue_compatibility(source: dict, target: dict) -> None:
    """Require the same physical meaning for shared sensor category channels."""
    validate_catalogue(source)
    validate_catalogue(target)
    for name, before in sorted(source["sensor_categories"].items()):
        other = category_name(name, "sensor", target)
        if other not in target["sensor_categories"]:
            continue
        after = target["sensor_categories"][other]
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
            return validate_categories(categories, kind, catalogue)
    return []
