"""Explainable activity scores from weighted sensor capabilities and sequences.

Scores describe rule support, not calibrated probabilities. Object names are
identifiers only; capabilities, sensor observations and room context drive rules.
"""

import copy
import math

from device_properties import default_sensor_properties, normalize_properties, validate_weight
from lighting import LIGHT_TYPES, with_integrated_light_meters
from room_recognition import point_in_polygon


DEFAULT_RULES = {
    "cooking": {"weights": {"cooking": .8, "presence": .2}, "required": ["cooking"]},
    "eating": {"weights": {"eating": .7, "presence": .2, "recent_cooking": .1}, "required": ["eating"]},
    "eating while relaxing": {"weights": {"recent_cooking": .55, "relaxing": .45},
                              "required": ["cooking_then_relaxing"]},
    "working": {"weights": {"working": 1}, "required": ["working"]},
    "relaxing": {"weights": {"relaxing": 1}, "required": ["relaxing"]},
    "laundry": {"weights": {"laundry": 1}, "required": ["laundry"]},
    "dishwasher": {"weights": {"dishwashing": 1}, "required": ["dishwashing"]},
    "cleaning": {"weights": {"cleaning": 1}, "required": ["cleaning"]},
    "using the bathroom": {"weights": {"toileting": .8, "bathroom_presence": .2}, "required": ["toileting"]},
    "taking a shower": {"weights": {"showering": .75, "water_flow": .15, "bathroom_presence": .1},
                         "required": ["showering", "water_flow"]},
    "washing hands": {"weights": {"washing_hands": .8, "water_flow": .2}, "required": ["washing_hands", "water_flow"]},
    "preparing a drink": {"weights": {"drink_preparation": 1}, "required": ["drink_preparation"]},
    "grooming": {"weights": {"grooming": 1}, "required": ["grooming"]},
    "sleeping": {"weights": {"bedroom_presence": .25, "bed_weight": .4, "lights_off": .15, "light_power_zero": .2},
                 "required": ["bedroom_presence", "bed_weight", "lights_off", "light_power_zero"]},
}
BACKGROUND_ACTIVITIES = frozenset({"laundry", "dishwasher", "cleaning"})


def recognition_settings(config=None):
    config = config or {}
    result = {"meal_window_minutes": 60.0, "sleep_minutes": 10.0, "minimum_confidence": 0.0,
              "rules": {name: {"threshold": .65, "weight": 1.0, **copy.deepcopy(rule)}
                        for name, rule in DEFAULT_RULES.items()}}
    for key in ("meal_window_minutes", "sleep_minutes"):
        if key in config:
            value = float(config[key])
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be a positive finite number.")
            result[key] = value
    result["minimum_confidence"] = validate_weight(config.get("minimum_confidence", 0))
    for name, overrides in config.get("rules", {}).items():
        if not str(name).strip():
            raise ValueError("Activity names cannot be empty.")
        base = result["rules"].setdefault(name, {"threshold": .65, "weight": 1.0, "required": []})
        base.update(copy.deepcopy(overrides))
        base["weights"] = normalize_properties(base["weights"])
        if not any(base["weights"].values()):
            raise ValueError("Each rule needs at least one positive evidence weight.")
        base["threshold"] = validate_weight(base["threshold"])
        base["weight"] = validate_weight(base["weight"])
        if name in DEFAULT_RULES:
            base["required"] = list(DEFAULT_RULES[name]["required"])
    return result


def _last(data, key, default=0):
    values = data.get(key, [])
    value = values[-1] if isinstance(values, (list, tuple)) and values else default
    try:
        value = float(value)
        return value if math.isfinite(value) else 0.0
    except (ValueError, TypeError):
        return 0.0


def _room_at(rooms, obj):
    return next((room for room in sorted(rooms, key=lambda room: room.room_id)
                 if point_in_polygon(obj.x, obj.y, room.polygon)), None)


def collect_evidence(sensor_states, runtime):
    from utils import is_path_blocked_by_walls
    devices = runtime.get("devices", [])
    sensors = with_integrated_light_meters(runtime.get("sensors", []), devices)
    by_device = {device.name: device for device in devices}
    by_sensor = {sensor.name: sensor for sensor in sensors}
    rooms = runtime.get("rooms", [])
    signals = {}

    def add(prop, strength, source):
        if strength > 0:
            signals.setdefault(prop, {})[source] = max(signals.get(prop, {}).get(source, 0), min(1, strength))

    def add_device(device, strength, source):
        for prop, weight in device.properties.items():
            add(prop, weight * device.recognition_weight * strength, source)

    active_presence = []
    for name in dict.fromkeys([*by_sensor, *sensor_states]):
        sensor = by_sensor.get(name)
        data = sensor_states.get(name, {})
        sensor_type = sensor.type if sensor is not None else data.get("type", "")
        default_state = sensor.state if sensor is not None else 0
        value = _last(data, "state", default_state)
        if sensor_type == "Smart Meter" and data.get("consumption"):
            value = _last(data, "consumption")
        threshold = .5 if sensor_type == "Smart Meter" else sensor.min_val if sensor_type == "Temperature" and sensor is not None else 0
        if value <= threshold:
            continue
        properties = sensor.properties if sensor is not None else default_sensor_properties(sensor_type)
        reliability = sensor.recognition_weight if sensor is not None else 1.0
        for prop, weight in properties.items():
            add(prop, reliability * weight, name)
        if sensor is not None:
            room = _room_at(rooms, sensor)
            presence = properties.get("presence", 0) * reliability
            if presence:
                active_presence.append((sensor, room, presence))
                if (room is not None and room.room_type.lower() in {"bedroom", "bathroom"}
                        and not (room.room_type.lower() == "bedroom" and sensor_type == "Weight")):
                    add(f"{room.room_type.lower()}_presence", presence, name)
        associated = sensor.associated_device if sensor is not None else data.get("associated_device")
        device = by_device.get(associated)
        channel = {"Smart Meter": "energy", "Weight": "weight", "Water Flow": "water_flow",
                   "Switch": "contact", "PIR": "presence"}.get(sensor_type)
        channel_weight = properties.get(channel, max(properties.values(), default=0))
        if device is not None:
            add_device(device, reliability * channel_weight, name)
            if sensor_type == "Weight" and device.properties.get("sleeping", 0):
                add("bed_weight", device.properties["sleeping"] * device.recognition_weight * reliability * channel_weight, name)
        elif sensor is not None and sensor_type == "Weight":
            # Furniture can be observed without a mandatory name association.
            for fixture in devices:
                if (math.hypot(fixture.x - sensor.x, fixture.y - sensor.y) <= 30
                        and _room_at(rooms, fixture) is _room_at(rooms, sensor)
                        and not is_path_blocked_by_walls(fixture.x, fixture.y, sensor.x, sensor.y,
                                                        runtime.get("walls", []), runtime.get("doors", []))):
                    for prop in ("sleeping", "eating", "seating"):
                        if fixture.properties.get(prop, 0):
                            add(prop, fixture.properties[prop] * fixture.recognition_weight * reliability * channel_weight, name)
                    if fixture.properties.get("sleeping", 0):
                        add("bed_weight", fixture.properties["sleeping"] * fixture.recognition_weight * reliability * channel_weight, name)

    # An unmetered active appliance can be supported by a nearby presence sensor.
    # Walls/doors constrain this fallback; no capability is inferred from a name.
    for device in devices:
        if not device.state:
            continue
        for sensor, room, strength in active_presence:
            device_room = _room_at(rooms, device)
            nearby = (room is device_room if room is not None else math.hypot(device.x - sensor.x, device.y - sensor.y) <= 150)
            if nearby and not is_path_blocked_by_walls(device.x, device.y, sensor.x, sensor.y,
                                                     runtime.get("walls", []), runtime.get("doors", [])):
                add_device(device, strength, sensor.name)

    avatar = runtime.get("avatar_position")
    if avatar is not None:
        room = next((room for room in rooms if point_in_polygon(*avatar, room.polygon)), None)
        if room is not None and room.room_type.lower() in {"bedroom", "bathroom"}:
            add(f"{room.room_type.lower()}_presence", 1, "avatar position")
        add("presence", 1, "avatar position")
    features = {prop: max(sources.values()) for prop, sources in signals.items()}
    lights = [device for device in devices if device.type in LIGHT_TYPES or device.properties.get("lighting", 0) > 0]
    light_names = {light.name for light in lights}
    light_power = sum(max(0, float(light.current_consumption), float(light.power) if light.state else 0) for light in lights)
    # An observed nonzero reading must veto sleep even if the model says OFF.
    observed_light_power = sum(max(0, _last(data, "consumption")) for data in sensor_states.values()
                               if data.get("associated_device") in light_names)
    features["lights_off"] = float(bool(lights) and all(not light.state for light in lights))
    features["light_power_zero"] = float(bool(lights) and light_power <= 1e-6 and observed_light_power <= 1e-6)
    return features, signals, light_power


def recognize_activities(sensor_states, runtime, state, now_minutes, *, person_inside=True):
    from recognition_library import synchronize_runtime, runtime_fingerprint
    settings = recognition_settings(synchronize_runtime(runtime))
    signature = runtime_fingerprint(runtime, settings)
    if state.get("recognition_model_signature") not in (None, signature):
        state.pop("semantic", None)
        state.pop("recognition_trace", None)
        state.pop("validation_run_id", None)
    state["recognition_model_signature"] = signature
    memory = state.setdefault("semantic", {})
    if now_minutes < memory.get("last_time", now_minutes):
        memory.clear()
    memory["last_time"] = now_minutes
    features, signals, light_power = collect_evidence(sensor_states, runtime)
    previous = memory.setdefault("active_sources", {})
    started = memory.setdefault("started", {})
    last_active = memory.setdefault("last_active", {})
    strengths = memory.setdefault("strengths", {})
    last_sources = memory.setdefault("last_sources", {})
    for prop in ("cooking", "relaxing"):
        active_sources = set(signals.get(prop, {}))
        if person_inside and active_sources - set(previous.get(prop, [])):
            started[prop] = now_minutes
        previous[prop] = list(active_sources) if person_inside else []
        if person_inside and features.get(prop, 0) >= .3:
            last_active[prop] = now_minutes
            strengths[prop] = features[prop]
            last_sources[prop] = list(active_sources)
    window = settings["meal_window_minutes"]
    cooking_age = now_minutes - last_active.get("cooking", -math.inf)
    recent = strengths.get("cooking", 0) * max(0, 1 - cooking_age / window)
    features["recent_cooking"] = recent
    if recent > 0:
        signals["recent_cooking"] = {source: recent for source in last_sources.get("cooking", [])}
    if recent > 0 and features.get("presence", 0) > 0 and not features.get("cooking", 0):
        features["eating"] = max(features.get("eating", 0), .9 * recent)
    features["cooking_then_relaxing"] = float(
        recent > 0 and features.get("relaxing", 0) > 0
        and started.get("relaxing", -math.inf) > started.get("cooking", math.inf))
    results = {}
    for name, rule in settings["rules"].items():
        weights = rule["weights"]
        score = sum(features.get(prop, 0) * weight for prop, weight in weights.items()) / sum(weights.values())
        score = min(1, score * rule["weight"])
        missing = [prop for prop in rule.get("required", []) if features.get(prop, 0) <= 0]
        active = score > 0 and score >= rule["threshold"] and not missing
        if not person_inside and name not in BACKGROUND_ACTIVITIES:
            active = False
        if name == "sleeping":
            if active:
                memory.setdefault("sleep_since", now_minutes)
                active = now_minutes - memory["sleep_since"] >= settings["sleep_minutes"]
            else:
                memory.pop("sleep_since", None)
        if name == "eating" and features.get("cooking", 0) > 0:
            active = False
        evidence = [f"{prop}: {features.get(prop, 0):.2f}" for prop in weights if features.get(prop, 0) > 0]
        sources = sorted({source for prop in weights for source in signals.get(prop, {})})
        results[name] = {"score": round(score, 4), "active": active, "evidence": evidence,
                         "sources": sources, "missing": missing,
                         "inferred": name == "eating while relaxing" or (name == "eating" and not signals.get("eating"))}
    for name, result in results.items():
        weights = settings["rules"][name]["weights"]
        coverage = sum(weight for prop, weight in weights.items() if features.get(prop, 0) > 0) / sum(weights.values())
        result["evidence_coverage"] = round(coverage, 4)
        result["confidence"] = round(result["score"] * coverage * (.85 if result["inferred"] else 1), 4) if result["active"] else 0.0
        result["confidence_method"] = "uncalibrated evidence support"
        if result["active"] and result["confidence"] < settings["minimum_confidence"]:
            result["active"] = False
            result["abstained"] = True
    if results.get("eating while relaxing", {}).get("active"):
        for name in ("eating", "relaxing", "working", "cooking"):
            if name in results:
                results[name]["active"] = False
                results[name]["suppressed_by"] = "eating while relaxing"
    if results.get("laundry", {}).get("active") or results.get("dishwasher", {}).get("active"):
        results.get("cleaning", {})["active"] = False
        results.get("cleaning", {})["suppressed_by"] = "laundry / dishwasher"
    state["recognition_results"] = results
    state["recognition_features"] = features
    state["lighting_power_w"] = light_power
    for result in results.values():
        if result.get("suppressed_by"):
            result["confidence"] = 0.0
    return {name for name, result in results.items() if result["active"]}
