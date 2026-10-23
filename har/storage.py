"""Data-only persistence for learned HAR, separate from legacy recognition data."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4


DATA_DIR = Path(__file__).resolve().parent.parent / "har_data"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")


def safe_identifier(value: str) -> str:
    """Keep names within their data directory, including when loading files."""
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid HAR identifier")
    return value


def atomic_json(path: str | Path, payload) -> None:
    """Write complete, finite JSON before atomically replacing a prior file."""
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def read_json(path: str | Path) -> dict:
    def invalid_constant(value):
        raise ValueError(f"Non-finite JSON value: {value}")

    with Path(path).open(encoding="utf-8") as stream:
        data = json.load(stream, parse_constant=invalid_constant)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    # A numeric exponent such as 1e9999 is otherwise accepted as infinity.
    json.dumps(data, allow_nan=False)
    return data


def new_house_config() -> dict:
    return {"house_id": uuid4().hex, "house_label": "House", "model_id": None}


def validate_house_config(config: dict) -> dict:
    if not isinstance(config, dict):
        raise ValueError("Invalid HAR house configuration")
    result = dict(config)
    safe_identifier(result.get("house_id"))
    label = result.get("house_label", "House")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("House name cannot be empty")
    result["house_label"] = label.strip()
    if result.get("model_id") is not None:
        safe_identifier(result["model_id"])
    result.setdefault("model_id", None)
    json.dumps(result, allow_nan=False)
    return result


def ensure_data() -> Path:
    """Create the shared dictionary and directories without loading recursively."""
    for name in ("sessions", "models", "reports"):
        (Path(DATA_DIR) / name).mkdir(parents=True, exist_ok=True)
    dictionary = Path(DATA_DIR) / "catalogue.json"
    if not dictionary.exists():
        from har.catalogue import default_catalogue

        atomic_json(dictionary, default_catalogue())
    return Path(DATA_DIR)


def validate_session(session: dict) -> dict:
    if not isinstance(session, dict) or session.get("schema_version") != 1:
        raise ValueError("Unsupported HAR session schema")
    safe_identifier(session.get("session_id"))
    validate_house_config({
        "house_id": session.get("house_id"),
        "house_label": session.get("house_label"),
    })
    if session.get("partition") not in ("source", "target"):
        raise ValueError("Session partition must be source or target")
    from har.catalogue import validate_catalogue, validate_categories

    validate_catalogue(session.get("catalogue"))
    frames = session.get("frames")
    if not isinstance(frames, list):
        raise ValueError("Session frames must be a list")
    previous_time = None
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("Invalid session frame")
        seconds = frame.get("time_seconds")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
            raise ValueError("Frame time must be a nonnegative number")
        if previous_time is not None and seconds < previous_time:
            raise ValueError("Frame times must be chronological")
        previous_time = seconds
        for key in ("devices", "sensors", "rooms"):
            if not isinstance(frame.get(key), list):
                raise ValueError(f"Frame {key} must be a list")
        for kind in ("device", "sensor"):
            for obj in frame[f"{kind}s"]:
                if not isinstance(obj, dict) or any(
                    not isinstance(obj.get(field), str) or not obj[field].strip()
                    for field in ("name", "type")
                ):
                    raise ValueError(f"Each {kind} observation needs a name and type")
                if obj.get("categories") is not None:
                    validate_categories(obj["categories"], kind, session["catalogue"])
        if not isinstance(frame.get("sensor_states"), dict):
            raise ValueError("Frame sensor_states must be an object")
        if any(not isinstance(values, dict) for values in frame["sensor_states"].values()):
            raise ValueError("Each sensor's readings must be an object")
        for room in frame["rooms"]:
            if not isinstance(room, dict) or not isinstance(room.get("room_type"), str):
                raise ValueError("Each room observation needs a room_type")
            polygon = room.get("polygon")
            if not isinstance(polygon, (list, tuple)) or any(
                not isinstance(point, (list, tuple)) or len(point) != 2
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in point)
                for point in polygon
            ):
                raise ValueError("Room polygons must contain numeric coordinate pairs")
        labels = frame.get("labels")
        if labels is not None and (
            not isinstance(labels, list)
            or any(not isinstance(label, str) or not label.strip() for label in labels)
        ):
            raise ValueError("Labels must be null or a list of nonempty activity names")
    json.dumps(session, allow_nan=False)
    return session


_validate_session = validate_session  # Compatibility for callers of the initial internal helper.


def save_session(session: dict) -> Path:
    validate_session(session)
    path = ensure_data() / "sessions" / f"{session['session_id']}.json"
    atomic_json(path, session)
    return path


def list_sessions() -> list[dict]:
    sessions = []
    for path in sorted((ensure_data() / "sessions").glob("*.json")):
        try:
            session = validate_session(read_json(path))
            if path.stem != session["session_id"]:
                continue
            sessions.append(session)
        except (OSError, ValueError, TypeError):
            continue
    return sessions


def save_model(model: dict) -> Path:
    if not isinstance(model, dict):
        raise ValueError("A model must be a JSON object")
    identifier = safe_identifier(model.get("model_id"))
    path = ensure_data() / "models" / f"{identifier}.json"
    atomic_json(path, model)
    return path


def load_model(model_id: str) -> dict:
    identifier = safe_identifier(model_id)
    data = read_json(ensure_data() / "models" / f"{identifier}.json")
    if data.get("model_id") != identifier:
        raise ValueError("Model identifier does not match its file")
    return data


def list_models() -> list[dict]:
    result = []
    for path in sorted((ensure_data() / "models").glob("*.json")):
        try:
            result.append(load_model(path.stem))
        except (OSError, ValueError, TypeError):
            continue
    return result
