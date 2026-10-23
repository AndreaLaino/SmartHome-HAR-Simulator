"""Shared, versioned properties and activity rules for independent houses."""

import copy
import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path

from device_properties import (DEVICE_PROPERTIES, SENSOR_PROPERTIES, default_device_properties,
                               default_sensor_properties, normalize_properties, validate_weight)


LIBRARY_DIR = Path(__file__).resolve().parent / "recognition_library"


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def default_library():
    from activity_recognition import recognition_settings
    profiles = {}
    for kind, catalogue in (("device", DEVICE_PROPERTIES), ("sensor", SENSOR_PROPERTIES)):
        for name, properties in catalogue.items():
            profiles[f"{kind}:{name}"] = {"kind": kind, "label": name, "properties": dict(properties),
                                          "recognition_weight": 1.0}
    settings = recognition_settings()
    settings["minimum_confidence"] = .5
    # The shared oven reference also describes the physical-capability example.
    # Keep the original association for existing activity-labelled devices.
    oven = settings["reference_bindings"]["device:Oven:cooking"]
    oven["reference"]["properties"].update(heats_food=1, food_preparation=1)
    physical = copy.deepcopy(oven)
    physical.update(property_weights={"heats_food": .6, "food_preparation": .4},
                    required_properties=["food_preparation"])
    settings["reference_bindings"]["device:Oven:cooking:food_preparation"] = physical
    return {"schema_version": 1, "library_id": uuid.uuid4().hex, "revision": 1,
            "profiles": profiles, "recognition_config": settings, "reference_house_id": None}


def validate_library(payload):
    from activity_recognition import recognition_settings
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Unsupported recognition library format.")
    if not payload.get("library_id") or not isinstance(payload.get("revision"), int):
        raise ValueError("The library needs an ID and revision.")
    recognition_settings(payload["recognition_config"])
    for key, profile in payload["profiles"].items():
        if not key or profile["kind"] not in {"device", "sensor"}:
            raise ValueError("Invalid property profile.")
        normalize_properties(profile["properties"])
        validate_weight(profile["recognition_weight"])
    return payload


def load_library():
    path = LIBRARY_DIR / "library.json"
    if not path.exists():
        raise ValueError("Create the shared library from Scenario > Shared recognition library first.")
    return validate_library(json.loads(path.read_text(encoding="utf-8")))


def ensure_library():
    for name in ("houses", "datasets", "reports"):
        (LIBRARY_DIR / name).mkdir(parents=True, exist_ok=True)
    if not (LIBRARY_DIR / "library.json").exists():
        atomic_json(LIBRARY_DIR / "library.json", default_library())
    shared = load_library()
    if "reference_bindings" not in shared["recognition_config"]:
        from activity_recognition import recognition_settings
        updated = copy.deepcopy(shared)
        updated["recognition_config"] = recognition_settings(updated["recognition_config"])
        shared = save_library(updated, shared["revision"])
    return shared


def save_library(payload, expected_revision):
    current = load_library()
    if current["revision"] != expected_revision:
        raise ValueError("The shared library changed. Reload it before saving your changes.")
    updated = validate_library(copy.deepcopy(payload))
    updated["revision"] = expected_revision + 1
    atomic_json(LIBRARY_DIR / "library.json", updated)
    return updated


def merge_config(shared, local):
    result = copy.deepcopy(shared)
    for key, value in local.items():
        if key == "rules":
            for name, override in value.items():
                result.setdefault("rules", {}).setdefault(name, {}).update(copy.deepcopy(override))
        else:
            result[key] = copy.deepcopy(value)
    return result


def synchronize_runtime(runtime, library=None):
    link = runtime.get("recognition_library_link", {})
    if not link.get("enabled"):
        return runtime.get("recognition_config", {})
    library = load_library() if library is None else library
    if link.get("library_id") != library["library_id"]:
        raise ValueError("This house is linked to a different shared library. Reconnect it explicitly.")
    effective = merge_config(library["recognition_config"], runtime.get("recognition_config", {}))
    from activity_recognition import recognition_settings
    recognition_settings(effective)
    updates = []
    for kind, objects in (("device", runtime.get("devices", [])), ("sensor", runtime.get("sensors", []))):
        defaults = default_device_properties if kind == "device" else default_sensor_properties
        for obj in objects:
            profile_key = getattr(obj, "recognition_profile", None)
            if profile_key is None and obj.properties == defaults(obj.type) and obj.recognition_weight == 1:
                candidate = f"{kind}:{obj.type}"
                if candidate in library["profiles"]:
                    profile_key = candidate
            if profile_key not in (None, "local"):
                profile = library["profiles"].get(profile_key)
                if profile is None or profile["kind"] != kind:
                    raise ValueError(f"Missing shared property profile: {profile_key}")
                updates.append((obj, profile_key, normalize_properties(profile["properties"]),
                                validate_weight(profile["recognition_weight"])))
    # Validate every binding before changing any object or the link revision.
    for obj, profile_key, properties, weight in updates:
        obj.recognition_profile = profile_key
        obj.properties = properties
        obj.recognition_weight = weight
    link["revision"] = library["revision"]
    link["model_snapshot"] = copy.deepcopy(library["recognition_config"])
    return effective


def context_runtime(ctx):
    from app.ui.device_inspector import current_devices, current_sensors, current_points, current_walls, current_doors
    return {"devices": current_devices(ctx), "sensors": current_sensors(ctx), "points": current_points(ctx),
            "walls": current_walls(ctx), "doors": current_doors(ctx), "rooms": ctx.rooms,
            "recognition_config": ctx.recognition_config, "recognition_library_link": ctx.recognition_library_link}


def model_fingerprint(config):
    from activity_recognition import recognition_settings
    content = json.dumps(recognition_settings(config), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode()).hexdigest()


def house_snapshot(runtime, house_id, label):
    config = synchronize_runtime(runtime)
    fields = {"device": ("name", "x", "y", "type", "power", "state", "min_consumption", "max_consumption",
                          "current_consumption", "light_control", "properties", "recognition_weight", "recognition_profile"),
              "sensor": ("name", "x", "y", "type", "min_val", "max_val", "step", "state", "direction", "consumption",
                          "associated_device", "properties", "recognition_weight", "recognition_profile")}
    snapshot = {"schema_version": 1, "house_id": house_id, "label": label, "recognition_config": copy.deepcopy(config),
                "library_link": copy.deepcopy(runtime.get("recognition_library_link", {})),
                "model_fingerprint": model_fingerprint(config)}
    for kind, key in (("device", "devices"), ("sensor", "sensors")):
        snapshot[key] = [{name: copy.deepcopy(getattr(obj, name, None)) for name in fields[kind]} for obj in runtime.get(key, [])]
    for key in ("rooms", "walls", "doors", "points"):
        snapshot[key] = [asdict(obj) for obj in runtime.get(key, [])]
    return snapshot


def runtime_from_snapshot(snapshot):
    from models import Device, Sensor, Room, Wall, Door, Point
    runtime = {"devices": [Device(**obj) for obj in snapshot["devices"]],
               "sensors": [Sensor(**obj) for obj in snapshot["sensors"]],
               "rooms": [Room(**obj) for obj in snapshot.get("rooms", [])],
               "walls": [Wall(obj["x1"], obj["y1"], obj["x2"], obj["y2"]) for obj in snapshot.get("walls", [])],
               "doors": [Door(**obj) for obj in snapshot.get("doors", [])],
               "points": [Point(**obj) for obj in snapshot.get("points", [])],
               "recognition_config": copy.deepcopy(snapshot["recognition_config"])}
    runtime["house_id"] = snapshot.get("house_id")
    return runtime


def register_house(ctx, label, *, reference=False, new=False):
    library = ensure_library()
    if not label.strip():
        raise ValueError("Enter a house name.")
    link = ctx.recognition_library_link
    if link.get("library_id") not in (None, library["library_id"]):
        raise ValueError("This house belongs to another library. Disconnect it before registering here.")
    if new:
        link.pop("house_id", None)
    house_id = link.setdefault("house_id", uuid.uuid4().hex)
    link.update({"library_id": library["library_id"], "label": label.strip()})
    snapshot = house_snapshot(context_runtime(ctx), house_id, label.strip())
    atomic_json(LIBRARY_DIR / "houses" / f"{house_id}.json", snapshot)
    if reference:
        set_reference_house(house_id)
    return snapshot


def set_reference_house(house_id):
    """Select a registered snapshot without publishing its local rules."""
    from reference_transfer import anchor_reference_bindings
    from activity_recognition import recognition_settings
    snapshot = read_house(house_id)
    library = load_library()
    library["reference_house_id"] = house_id
    config = recognition_settings(library["recognition_config"])
    config["reference_bindings"] = anchor_reference_bindings(config["reference_bindings"], snapshot)
    library["recognition_config"] = config
    return save_library(library, library["revision"])


def connect_house(ctx, *, enabled=True):
    if not enabled:
        # Freeze the effective model before detaching so behaviour is preserved.
        try:
            effective = synchronize_runtime(context_runtime(ctx))
        except (OSError, ValueError):
            effective = merge_config(ctx.recognition_library_link.get("model_snapshot", {}), ctx.recognition_config)
        ctx.recognition_config.clear()
        ctx.recognition_config.update(effective)
        for obj in [*context_runtime(ctx)["devices"], *context_runtime(ctx)["sensors"]]:
            obj.recognition_profile = "local"
        ctx.recognition_library_link["enabled"] = False
        ctx.recognition_library_link.pop("library_id", None)
        return
    library = ensure_library()
    runtime = context_runtime(ctx)
    link = copy.deepcopy(ctx.recognition_library_link)
    link.update({"enabled": True, "library_id": library["library_id"]})
    runtime["recognition_library_link"] = link
    synchronize_runtime(runtime, library)
    ctx.recognition_library_link.clear()
    ctx.recognition_library_link.update(link)


def runtime_fingerprint(runtime, config):
    payload = {"config": config}
    for kind in ("devices", "sensors"):
        payload[kind] = [(obj.name, obj.type, obj.properties, obj.recognition_weight,
                          getattr(obj, "associated_device", None)) for obj in runtime.get(kind, [])]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def read_house(house_id):
    if not house_id or not str(house_id).isalnum():
        raise ValueError("Invalid house ID.")
    return json.loads((LIBRARY_DIR / "houses" / f"{house_id}.json").read_text(encoding="utf-8"))
