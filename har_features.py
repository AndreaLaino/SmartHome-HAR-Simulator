"""Causal, label-independent observations for controlled HAR experiments.

Reference features use a frozen role vocabulary. Direct features reproduce the
earlier property recognizer by exposing each observed property as its own role;
they do not consult the reference associations. Raw features deliberately retain
source object identities, providing an identity-dependent comparison baseline.
No activity predictions, hand-derived sequence decisions or confidence scores
enter any feature vector. Elapsed time since primitive observed use lets a
classifier learn temporal activity patterns from source-house examples.
"""

import copy
import json
import math

import recognition_library as library
from activity_recognition import recognize_activities, recognition_settings
from device_properties import SENSOR_PROPERTIES


def _number(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _latest(observation, key, default=0.0):
    values = observation.get(key, [])
    if isinstance(values, (list, tuple)):
        return _number(values[-1], default) if values else default
    return _number(values, default)


def _semantic_roles(settings):
    # Activity rules may contain engineered conclusions such as recent_cooking,
    # inferred eating and cooking_then_relaxing. They are intentionally excluded:
    # the learned model receives primitive observations and their causal ages.
    roles = {binding["feature"] for binding in settings["reference_bindings"].values()}
    roles.update({"presence", "kitchen_presence", "bedroom_presence", "bathroom_presence",
                  "bed_weight", "lights_off", "light_power_zero"})
    return sorted(roles)


def _direct_bindings(runtime):
    """Identity property mappings reproduce direct evidence without associations.

    A cooking=.8 property contributes .8 before object/sensor reliability, while
    heats_food alone has no implicit connection to the cooking activity.
    """
    bindings = {}
    for kind, objects in (("device", runtime["devices"]), ("sensor", runtime["sensors"])):
        properties = {prop for obj in objects for prop, value in obj.properties.items() if value > 0}
        if kind == "sensor":
            # The original collector supports observation-only and integrated
            # sensor channels using their catalogue properties.
            properties.update(prop for profile in SENSOR_PROPERTIES.values() for prop in profile)
        properties = sorted(properties)
        for prop in properties:
            bindings[f"direct:{kind}:{prop}"] = {
                "feature": prop, "kind": kind,
                "reference": {"name": prop, "type": "direct property", "properties": {prop: 1},
                              "recognition_weight": 1},
                "property_weights": {prop: 1}, "required_properties": [prop],
                "minimum_similarity": 0, "strength": 1, "weight": 1,
            }
    return bindings


def _raw_schema(runtime, supplied):
    if supplied is None:
        return [{"kind": kind, "name": obj.name}
                for kind, objects in (("sensor", runtime["sensors"]), ("device", runtime["devices"]))
                for obj in sorted(objects, key=lambda obj: obj.name)]
    result = []
    seen = set()
    for item in supplied:
        if not isinstance(item, dict) or item.get("kind") not in {"sensor", "device"} or not isinstance(item.get("name"), str):
            raise ValueError("Raw schema entries need kind='sensor' or 'device' and an object name.")
        key = (item["kind"], item["name"])
        if key in seen:
            raise ValueError("Raw schema contains duplicate object identities.")
        seen.add(key)
        result.append({"kind": key[0], "name": key[1]})
    return result


def _window_statistics(history, now, window_minutes):
    """Time-weighted means of piecewise constant observations, never future data."""
    start = max(history[0][0], now - window_minutes)
    elapsed = now - start
    latest = history[-1][1]
    means = [0.0] * len(latest)
    changes = [0.0] * len(latest)
    for index, (time, values) in enumerate(history):
        if index and time > start:
            previous = history[index - 1][1]
            for column, value in enumerate(values):
                changes[column] += float(abs(value - previous[column]) > 1e-12)
        end = history[index + 1][0] if index + 1 < len(history) else now
        duration = max(0.0, min(end, now) - max(time, start))
        for column, value in enumerate(values):
            means[column] += value * duration
    if elapsed:
        means = [value / elapsed for value in means]
    else:
        means = list(latest)
    flattened = [number for triple in zip(latest, means, changes) for number in triple]
    return flattened, elapsed


def _clock_minutes(frame):
    """Only an explicit clock is allowed: elapsed simulation time is not a clock."""
    if "time_of_day_minutes" in frame:
        value = _number(frame["time_of_day_minutes"], float("nan"))
        if math.isfinite(value):
            return value % 1440
    stamp = frame.get("simulated_time")
    if isinstance(stamp, str):
        from datetime import datetime, time
        try:
            clock = datetime.fromisoformat(stamp.replace("Z", "+00:00")).time()
        except ValueError:
            try:
                clock = time.fromisoformat(stamp)
            except ValueError:
                clock = None
        if clock is not None:
            return clock.hour * 60 + clock.minute + clock.second / 60
    raise ValueError("Time-of-day features require explicit time_of_day_minutes or simulated_time on every frame.")


def extract_feature_rows(dataset, config, *, window_minutes=10.0, use_time_of_day=False,
                         profile_library=None, strict_cooking=True, mode="reference", raw_schema=None):
    """Replay one independent run and return fixed-schema causal feature rows.

    Frames, including unlabelled warm-up observations, are processed in time
    order. Rows do not copy or inspect labels. Rolling means use observed values
    held until the next observation; change counts exclude the first sample.
    ``profile_library`` is a frozen catalogue, applied once before detaching the
    runtime from disk. Supplied rules always override snapshot/house rules.
    """
    if mode not in {"reference", "direct", "raw"}:
        raise ValueError("Feature mode must be reference, direct or raw.")
    window_minutes = _number(window_minutes, float("nan"))
    if not math.isfinite(window_minutes) or window_minutes <= 0:
        raise ValueError("window_minutes must be a positive finite number.")
    if not isinstance(strict_cooking, bool):
        raise ValueError("strict_cooking must be a boolean.")
    settings = recognition_settings(copy.deepcopy(config))
    settings["require_kitchen_presence"] = strict_cooking
    runtime = library.runtime_from_snapshot(dataset["house"])
    if profile_library is not None and dataset["house"].get("library_link", {}).get("enabled"):
        runtime["recognition_library_link"] = copy.deepcopy(dataset["house"]["library_link"])
        library.synchronize_runtime(runtime, profile_library)
    runtime.pop("recognition_library_link", None)
    # Snapshot dynamic fields may describe its final recording frame. Start with
    # no observations and apply each causal frame rather than leaking that state.
    for obj in runtime["devices"]:
        obj.state = 0
        obj.current_consumption = 0
    for obj in runtime["sensors"]:
        obj.state = 0
        obj.consumption = 0
    for door in runtime.get("doors", []):
        # A missing initial observation cannot inherit a final open doorway and
        # create appliance-use evidence through a formerly blocked sight line.
        door.state = "close"
    if mode == "direct":
        settings["reference_bindings"] = _direct_bindings(runtime)
    runtime["recognition_config"] = settings
    roles = _semantic_roles(recognition_settings(config))
    schema = _raw_schema(runtime, raw_schema) if mode == "raw" else None
    raw_sensor_names = {item["name"] for item in schema if item["kind"] == "sensor"} if schema is not None else None
    channels = ([f"raw:{item['kind']}:{item['name']}:{channel}" for item in schema for channel in ("state", "power")]
                if mode == "raw" else [f"role:{role}" for role in roles])
    feature_names = [f"{channel}:{stat}" for channel in channels for stat in ("latest", "mean", "changes")]
    if mode != "raw":
        feature_names += [f"role:{role}:since_active_minutes" for role in roles]
    feature_names += ["elapsed_window_minutes", "sensor_event_count"]
    if use_time_of_day:
        feature_names += ["time_of_day_sin", "time_of_day_cos"]
    frames = []
    for frame in dataset["frames"]:
        now = _number(frame.get("time_minutes"), float("nan"))
        if not math.isfinite(now):
            raise ValueError("Each frame needs a finite time_minutes.")
        frames.append((now, frame))
    frames.sort(key=lambda item: item[0])
    if len({time for time, _ in frames}) != len(frames):
        raise ValueError("An independent run cannot contain duplicate frame timestamps.")
    history, event_history, rows = [], [], []
    previous_sensor_values = {}
    last_positive = {}
    maximum_age = max(window_minutes, settings["meal_window_minutes"]) + 1
    state = {}
    devices = {obj.name: obj for obj in runtime["devices"]}
    for now, frame in frames:
        observations = frame.get("sensor_states", {})
        for name, values in frame.get("device_states", {}).items():
            if name in devices:
                for key in ("state", "current_consumption"):
                    if key in values:
                        setattr(devices[name], key, _number(values[key]))
        runtime["avatar_position"] = frame.get("avatar_position")
        for door, door_state in zip(runtime.get("doors", []), frame.get("door_states", [])):
            door.state = door_state
        events = 0
        for name, observation in observations.items():
            if raw_sensor_names is not None and name not in raw_sensor_names:
                continue
            value = (_latest(observation, "consumption") if observation.get("consumption")
                     else _latest(observation, "state"))
            if abs(value - previous_sensor_values.get(name, 0.0)) > 1e-12:
                events += 1
            previous_sensor_values[name] = value
        event_history.append((now, events))
        if mode == "raw":
            values = []
            for item in schema:
                if item["kind"] == "sensor":
                    observed = observations.get(item["name"], {})
                    values.extend((_latest(observed, "state"), _latest(observed, "consumption")))
                else:
                    obj = devices.get(item["name"])
                    values.extend((_number(obj.state), _number(obj.current_consumption)) if obj is not None else (0.0, 0.0))
            predicted, matches = [], []
            abstained, confidence = False, 1.0
        else:
            predicted = sorted(recognize_activities(observations, runtime, state, now,
                                                   person_inside=frame.get("person_inside", True)))
            values = [float(state["recognition_observations"].get(role, 0)) for role in roles]
            for role, value in zip(roles, values):
                if value > 0:
                    last_positive[role] = now
            results = state["recognition_results"]
            abstained = any(result.get("abstained", False) for result in results.values())
            active_confidences = [result["confidence"] for result in results.values() if result["active"]]
            confidence = (min(active_confidences) if active_confidences
                          else max(0.0, 1 - max((result["score"] for result in results.values()), default=0.0)))
            matches = []
            if mode == "reference":
                unique = {}
                for result in state["recognition_results"].values():
                    for match in result["reference_matches"]:
                        unique[json.dumps(match, sort_keys=True)] = match
                matches = list(unique.values())
        history.append((now, values))
        # Keep one value preceding the rolling boundary for step interpolation.
        while len(history) > 1 and history[1][0] <= now - window_minutes:
            history.pop(0)
        event_history = [(time, count) for time, count in event_history if time >= now - window_minutes]
        vector, elapsed = _window_statistics(history, now, window_minutes)
        if mode != "raw":
            vector.extend(min(maximum_age, now - last_positive[role]) if role in last_positive else maximum_age
                          for role in roles)
        start = max(history[0][0], now - window_minutes)
        vector.extend((elapsed, float(sum(count for time, count in event_history if time >= start))))
        if use_time_of_day:
            clock = _clock_minutes(frame)
            angle = 2 * math.pi * clock / 1440
            vector.extend((math.sin(angle), math.cos(angle)))
        rows.append({"time_minutes": now, "features": vector,
                     "rule_prediction": predicted, "reference_matches": matches,
                     "abstained": abstained, "rule_confidence": confidence})
    return {"feature_names": feature_names, "rows": rows, "raw_schema": schema}
