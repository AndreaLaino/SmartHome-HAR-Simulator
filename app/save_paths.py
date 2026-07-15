from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import threading


BASE_DIR = Path(__file__).resolve().parents[1]
HOUSES_DIR = BASE_DIR / "houses"
SAVES_DIR = BASE_DIR / "saves"
DEVICES_DIR = BASE_DIR / "devices"

_session_lock = threading.Lock()
_current_session_dir: Path | None = None

SESSION_SUBDIRS = {
    "interactions": "interactions",
    "sensors": "sensors",
    "activities": "activities",
    "devices_k": "devices_k",
}


def _sanitize_suffix(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("_")


def ensure_houses_dir() -> Path:
    HOUSES_DIR.mkdir(parents=True, exist_ok=True)
    return HOUSES_DIR


def ensure_saves_dir() -> Path:
    SAVES_DIR.mkdir(parents=True, exist_ok=True)
    return SAVES_DIR


def ensure_devices_dir() -> Path:
    DEVICES_DIR.mkdir(parents=True, exist_ok=True)
    return DEVICES_DIR


def _session_stamp_minute() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M")


def ensure_session_subdirs(session_dir: Path) -> None:
    for rel in SESSION_SUBDIRS.values():
        (session_dir / rel).mkdir(parents=True, exist_ok=True)


def get_session_subdir(kind: str, session_dir: Path | None = None) -> Path:
    if kind not in SESSION_SUBDIRS:
        raise ValueError(f"Unknown session subdir kind: {kind}")

    base = session_dir if session_dir is not None else get_or_create_current_save_session()
    out = base / SESSION_SUBDIRS[kind]
    out.mkdir(parents=True, exist_ok=True)
    return out


def create_new_save_session(suffix: str = "") -> Path:
    global _current_session_dir

    saves_root = ensure_saves_dir()
    base_name = _session_stamp_minute()
    if suffix:
        safe_suffix = _sanitize_suffix(suffix)
        if safe_suffix:
            base_name = f"{base_name}_{safe_suffix}"

    with _session_lock:
        candidate = saves_root / base_name
        idx = 1
        while candidate.exists():
            candidate = saves_root / f"{base_name}_{idx:02d}"
            idx += 1

        candidate.mkdir(parents=True, exist_ok=False)
        ensure_session_subdirs(candidate)
        _current_session_dir = candidate
        return candidate


def get_or_create_current_save_session(suffix: str = "") -> Path:
    global _current_session_dir

    with _session_lock:
        if _current_session_dir is not None and _current_session_dir.exists():
            return _current_session_dir

    return create_new_save_session(suffix=suffix)


def get_current_save_session() -> Path | None:
    with _session_lock:
        if _current_session_dir is not None and _current_session_dir.exists():
            return _current_session_dir
    return None
