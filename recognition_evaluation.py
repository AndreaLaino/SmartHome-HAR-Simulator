"""Cross-house capability reports and evaluation against human-labelled runs."""

import copy
import json
import uuid

import recognition_library as library
from activity_recognition import collect_evidence, recognition_settings, recognize_activities


def capture_frame(runtime, sensor_states, now_minutes, *, person_inside=True):
    observations = {}
    for name, data in sensor_states.items():
        item = {key: copy.deepcopy(data[key]) for key in ("type", "associated_device") if key in data}
        for key in ("state", "consumption"):
            values = data.get(key, [])
            if values:
                item[key] = [values[-1]]
        observations[name] = item
    return {"time_minutes": float(now_minutes), "sensor_states": observations,
            "device_states": {obj.name: {"state": obj.state, "current_consumption": obj.current_consumption}
                              for obj in runtime.get("devices", [])},
            "door_states": [obj.state for obj in runtime.get("doors", [])],
            "avatar_position": copy.deepcopy(runtime.get("avatar_position")), "person_inside": bool(person_inside)}


def record_trace(state, frame):
    trace = state.setdefault("recognition_trace", [])
    if trace and frame["time_minutes"] < trace[-1]["time_minutes"]:
        trace.clear()
        state.pop("validation_run_id", None)
    if trace and trace[-1]["time_minutes"] == frame["time_minutes"]:
        trace[-1] = frame
    else:
        trace.append(frame)
    del trace[:-4096]


def label_current_frame(ctx, expected_activities, *, partition="test"):
    if partition not in {"reference", "test"}:
        raise ValueError("Choose Reference or Held-out test.")
    link = ctx.recognition_library_link
    if not link.get("house_id"):
        raise ValueError("Register this house before recording labelled examples.")
    trace = ctx.house_state.activity_state().get("recognition_trace", [])
    if not trace:
        raise ValueError("Start Manual simulation and make observations before labelling a frame.")
    expected = sorted(set(str(name).strip() for name in expected_activities if str(name).strip()))
    state = ctx.house_state.activity_state()
    run_id = state.setdefault("validation_run_id", uuid.uuid4().hex)
    path = library.LIBRARY_DIR / "datasets" / f"{run_id}.json"
    runtime = library.context_runtime(ctx)
    snapshot = library.house_snapshot(runtime, link["house_id"], link.get("label", "House"))
    signature = _house_signature(snapshot)
    if path.exists():
        dataset = json.loads(path.read_text(encoding="utf-8"))
        if dataset["partition"] != partition or dataset["house_signature"] != signature:
            raise ValueError("House properties/model or partition changed. Reset simulation to start an independent run.")
    else:
        dataset = {"schema_version": 1, "run_id": run_id, "house_id": link["house_id"],
                   "partition": partition, "house": snapshot, "house_signature": signature, "frames": []}
    # Unlabelled context establishes sequence order and sleep duration. It is
    # never treated as ground truth, or scored as a negative example.
    frames = {frame["time_minutes"]: frame for frame in dataset["frames"]}
    for frame in trace:
        previous = frames.get(frame["time_minutes"], {})
        frames[frame["time_minutes"]] = {**copy.deepcopy(frame)}
        if "expected_activities" in previous:
            frames[frame["time_minutes"]]["expected_activities"] = previous["expected_activities"]
    frames[trace[-1]["time_minutes"]]["expected_activities"] = expected
    dataset["frames"] = [frames[key] for key in sorted(frames)]
    library.atomic_json(path, dataset)
    return path


def _house_signature(snapshot):
    # State changes during a run; definitions and effective rules must not.
    stable = copy.deepcopy(snapshot)
    for obj in [*stable["devices"], *stable["sensors"]]:
        for key in ("state", "current_consumption", "consumption"):
            obj.pop(key, None)
    for obj in stable.get("rooms", []):
        obj.pop("total_power_w", None)
    for obj in stable.get("doors", []):
        obj.pop("state", None)
    stable.get("library_link", {}).pop("revision", None)
    import hashlib
    return hashlib.sha256(json.dumps(stable, sort_keys=True, allow_nan=False).encode()).hexdigest()


def evaluate_datasets(datasets, config, *, reference_house_id=None, shared_library=None):
    settings = recognition_settings(config)
    counts = {name: {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for name in settings["rules"]}
    exact = total = 0
    house_counts = {}
    excluded = []
    for dataset in datasets:
        if dataset.get("partition") != "test" or dataset["house_id"] == reference_house_id:
            excluded.append(dataset["run_id"])
            continue
        runtime = library.runtime_from_snapshot(dataset["house"])
        if shared_library is not None and dataset["house"].get("library_link", {}).get("enabled"):
            runtime["recognition_library_link"] = copy.deepcopy(dataset["house"]["library_link"])
            library.synchronize_runtime(runtime, shared_library)
            runtime.pop("recognition_library_link")
        runtime["recognition_config"] = copy.deepcopy(settings)
        state = {}
        rows = sorted(dataset["frames"], key=lambda frame: frame["time_minutes"])
        per_house = house_counts.setdefault(dataset["house_id"], {"correct": 0, "samples": 0})
        for frame in rows:
            for obj in runtime["devices"]:
                for key, value in frame.get("device_states", {}).get(obj.name, {}).items():
                    setattr(obj, key, value)
            runtime["avatar_position"] = frame.get("avatar_position")
            for door, door_state in zip(runtime.get("doors", []), frame.get("door_states", [])):
                door.state = door_state
            predicted = recognize_activities(frame["sensor_states"], runtime, state, frame["time_minutes"],
                                             person_inside=frame.get("person_inside", True))
            if "expected_activities" not in frame:
                continue
            expected = set(frame["expected_activities"])
            for name in expected | predicted:
                counts.setdefault(name, {"tp": 0, "fp": 0, "fn": 0, "tn": total})
            total += 1
            per_house["samples"] += 1
            correct = predicted == expected
            exact += correct
            per_house["correct"] += correct
            for name, item in counts.items():
                key = "tp" if name in expected and name in predicted else "fn" if name in expected else "fp" if name in predicted else "tn"
                item[key] += 1
    per_activity = {}
    for name, item in counts.items():
        tp, fp, fn = item["tp"], item["fp"], item["fn"]
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
        per_activity[name] = {**item, "precision": precision, "recall": recall, "f1": f1,
                              "support": tp + fn}
    tp = sum(item["tp"] for item in counts.values())
    fp = sum(item["fp"] for item in counts.values())
    fn = sum(item["fn"] for item in counts.values())
    return {"schema_version": 1, "model_fingerprint": library.model_fingerprint(settings),
            "labelled_samples": total, "test_houses": len([item for item in house_counts.values() if item["samples"]]),
            "exact_match_accuracy": exact / total if total else None,
            "micro_f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None,
            "per_activity": per_activity, "per_house": house_counts, "excluded_reference_runs": excluded,
            "dataset_runs": [dataset["run_id"] for dataset in datasets]}


def evaluate_library():
    shared = library.load_library()
    datasets = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((library.LIBRARY_DIR / "datasets").glob("*.json"))]
    result = evaluate_datasets(datasets, shared["recognition_config"], reference_house_id=shared.get("reference_house_id"), shared_library=shared)
    result["library_revision"] = shared["revision"]
    library.atomic_json(library.LIBRARY_DIR / "reports" / "evaluation.json", result)
    return result


def _potential_features(snapshot):
    runtime = library.runtime_from_snapshot(snapshot)
    observations = {}
    for obj in runtime["devices"]:
        obj.state = 1
    for obj in runtime["sensors"]:
        observations[obj.name] = {"state": [max(1, obj.min_val + 1)]}
        if obj.type == "Smart Meter":
            observations[obj.name]["consumption"] = [1]
    features, _signals, _power = collect_evidence(observations, runtime)
    features["recent_cooking"] = features.get("cooking", 0)
    features["cooking_then_relaxing"] = min(features.get("cooking", 0), features.get("relaxing", 0))
    # Coverage asks what the house can observe, not what is active concurrently.
    from lighting import LIGHT_TYPES
    lights = any(obj.type in LIGHT_TYPES or obj.properties.get("lighting", 0) for obj in runtime["devices"])
    features["lights_off"] = features["light_power_zero"] = float(lights)
    if any(room.room_type.lower() == "bedroom" for room in runtime["rooms"]):
        features["bedroom_presence"] = 1.0  # Available avatar-position context.
    return features


def transfer_report(reference, target, config):
    settings = recognition_settings(config)
    rules = settings["rules"]
    source = _potential_features(reference) if reference else {}
    features = _potential_features(target)
    activities = {}
    for name, rule in rules.items():
        weights = rule["weights"]
        coverage = sum(weight * min(1, features.get(prop, 0)) for prop, weight in weights.items()) / sum(weights.values())
        missing = [prop for prop in rule.get("required", []) if features.get(prop, 0) <= 0]
        source_score = sum(weight * min(1, source.get(prop, 0)) for prop, weight in weights.items()) / sum(weights.values())
        supported_in_reference = (bool(reference) and source_score > 0
                                  and source_score * rule["weight"] >= rule["threshold"]
                                  and all(source.get(prop, 0) > 0 for prop in rule.get("required", [])))
        evidence_coverage = sum(weight for prop, weight in weights.items() if features.get(prop, 0) > 0) / sum(weights.values())
        # An upper bound only: sequence decay and concurrent conditions can
        # reduce confidence at runtime. Inferred meals receive the same penalty.
        maximum_confidence = coverage * rule["weight"] * evidence_coverage * (.85 if name == "eating while relaxing" else 1)
        activities[name] = {"property_coverage": round(coverage, 4), "missing": missing,
                            "maximum_confidence": round(maximum_confidence, 4),
                            "supported_in_reference": supported_in_reference,
                            "transferable": (not missing and coverage > 0 and coverage * rule["weight"] >= rule["threshold"]
                                             and maximum_confidence >= settings["minimum_confidence"])}
    equivalents = []
    if reference:
        for obj in reference["devices"]:
            relevant = {key: value for key, value in obj["properties"].items() if value > 0}
            if not relevant:
                continue
            candidates = []
            for other in target["devices"]:
                # Extra properties of the target do not disqualify an equivalent
                # role. This is directional capability coverage, not name/type similarity.
                score = sum(min(value, other["properties"].get(key, 0)) for key, value in relevant.items()) / sum(relevant.values())
                candidates.append((score, other))
            if candidates:
                score, other = max(candidates, key=lambda item: item[0])
                equivalents.append({"reference_device": obj["name"], "reference_type": obj["type"],
                                    "target_device": other["name"], "target_type": other["type"],
                                    "property_match": round(score, 4)})
    return {"reference_house": reference.get("label") if reference else None, "target_house": target["label"],
            "activities": activities, "device_equivalents": equivalents,
            "note": "Property coverage describes potential transfer; measured accuracy requires labelled test runs."}
