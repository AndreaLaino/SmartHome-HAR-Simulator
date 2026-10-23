"""Live observations, explicit human labels and learned inference.

Recording does not require a model. Activity labels enter only the saved training
examples; prediction uses a separate encoder which never reads those labels.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from uuid import uuid4

from har import storage
from har.catalogue import categories_for, load_catalogue, validate_catalogue_compatibility
from har.features import FeatureEncoder, number


def current_runtime(ctx):
    from app.ui.device_inspector import current_devices, current_sensors
    return {"devices": current_devices(ctx), "sensors": current_sensors(ctx),
            "rooms": getattr(ctx, "rooms", []), "har_config": ctx.har_config}


def capture_frame(sensor_states, runtime, time_seconds, catalogue):
    from lighting import with_integrated_light_meters
    devices = runtime.get("devices", [])
    sensors = with_integrated_light_meters(runtime.get("sensors", []), devices)
    frame = {"time_seconds": float(time_seconds), "devices": [], "sensors": [], "rooms": [], "sensor_states": {}}
    for kind, objects in (("device", devices), ("sensor", sensors)):
        fields = (("name", "type", "x", "y", "state", "power", "min_consumption", "max_consumption", "current_consumption")
                  if kind == "device" else
                  ("name", "type", "x", "y", "min_val", "max_val", "state", "consumption", "associated_device", "integrated"))
        for obj in objects:
            item = {name: getattr(obj, name, None) for name in fields}
            for name in set(fields) - {"name", "type", "associated_device", "integrated"}:
                if item[name] is not None:
                    value = number(item[name], None)
                    if value is None:
                        raise ValueError(f"Invalid numeric observation {obj.name}: {name}.")
                    item[name] = value
            item["categories"] = categories_for(obj, kind, catalogue)
            frame[f"{kind}s"].append(item)
    for room in runtime.get("rooms", []):
        frame["rooms"].append({"room_type": room.room_type, "polygon": copy.deepcopy(room.polygon)})
    for name, values in sensor_states.items():
        observed = {}
        for key in ("state", "consumption"):
            value = values.get(key)
            if isinstance(value, (list, tuple)):
                value = value[-1] if value else None
            if value is not None:
                value = number(value, None)
                if value is None:
                    raise ValueError(f"Invalid numeric reading from {name}.")
                observed[key] = [value]
        frame["sensor_states"][name] = observed
    return frame


def start_recording(ctx, partition="source"):
    if partition not in {"source", "target"}:
        raise ValueError("Choose source or target for the session.")
    if recording_status(ctx)["recording"]:
        raise ValueError("A recording is already running. Stop and save it first.")
    if not getattr(ctx, "har_config", None):
        ctx.har_config = storage.new_house_config()
    config = storage.validate_house_config(ctx.har_config)
    simulation_id = ctx.house_state.values.setdefault("har_simulation_id", uuid4().hex)
    session = {"schema_version": 1, "session_id": uuid4().hex,
               "simulation_session_id": simulation_id, "house_id": config["house_id"],
               "house_label": config["house_label"], "partition": partition,
               "created_at": datetime.now(timezone.utc).isoformat(),
               "catalogue": load_catalogue(), "frames": []}
    ctx.house_state.values["har_recording"] = {"session": session, "labels": None}
    return session


def recording_status(ctx):
    recording = ctx.house_state.values.get("har_recording")
    if recording is None:
        return {"recording": False, "frames": 0, "labels": None, "session_id": None}
    return {"recording": True, "frames": len(recording["session"]["frames"]),
            "labels": copy.deepcopy(recording["labels"]), "session_id": recording["session"]["session_id"]}


def set_recording_labels(ctx, labels):
    recording = ctx.house_state.values.get("har_recording")
    if recording is None:
        raise ValueError("Start recording before applying labels.")
    if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
        raise ValueError("Provide activity names, or use an empty list for no activity.")
    recording["labels"] = sorted({label.strip() for label in labels})


def finish_recording(house_state):
    recording = house_state.values.get("har_recording")
    if recording is None:
        return None
    # Keep the complete session in memory on failure, permitting a safe retry.
    path = storage.save_session(recording["session"])
    house_state.values.pop("har_recording", None)
    return path


def stop_recording(ctx):
    path = finish_recording(ctx.house_state)
    if path is None:
        raise ValueError("No recording is running.")
    return path


def process_frame(sensor_states, runtime_sources, activity_state, now_seconds, *, house_state=None):
    now = number(now_seconds, -1)
    if now < 0:
        raise ValueError("Observation time must be finite and nonnegative.")
    recording = house_state.values.get("har_recording") if house_state is not None else None
    catalogue = recording["session"]["catalogue"] if recording else load_catalogue()
    frame = capture_frame(sensor_states, runtime_sources, now, catalogue)
    if recording:
        frames = recording["session"]["frames"]
        frame["labels"] = copy.deepcopy(recording["labels"])
        if frames and now < frames[-1]["time_seconds"]:
            raise ValueError("The recording clock moved backwards. Save and reset the simulation.")
        if frames and now == frames[-1]["time_seconds"]:
            frames[-1] = copy.deepcopy(frame)
        else:
            frames.append(copy.deepcopy(frame))
    config = runtime_sources.get("har_config", {})
    model_id, house_id = config.get("model_id"), config.get("house_id")
    if not model_id:
        result = {"activities": [], "probabilities": {}, "confidence": None, "abstained": False,
                  "status": "no_model", "message": "No trained model. Record labelled source sessions, train, then activate a model."}
        activity_state.pop("har_inference", None)
    else:
        from har.learning import predict_vector, validate_model
        inference = activity_state.get("har_inference")
        if inference is None or inference["model_id"] != model_id or inference.get("house_id") != house_id:
            model = validate_model(storage.load_model(model_id))
            inference = {"model_id": model_id, "house_id": house_id, "model": model,
                         "encoder": FeatureEncoder(model["catalogue"], model["window_seconds"])}
            activity_state["har_inference"] = inference
        validate_catalogue_compatibility(inference["model"]["catalogue"], catalogue)
        encoder = inference["encoder"]
        if encoder.history and now < encoder.history[-1][0]:
            encoder = FeatureEncoder(inference["model"]["catalogue"], inference["model"]["window_seconds"])
            inference["encoder"] = encoder
        result = predict_vector(inference["model"], encoder.update(frame))
    activity_state["har_prediction"] = result
    return result
