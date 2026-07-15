import os
import csv
import json
import glob
import pickle
import re
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk
from datetime import datetime, date
from pathlib import Path
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.ticker import MaxNLocator, FormatStrFormatter
import numpy as np
import pandas as pd

from sensor import (
    sensors,
    get_llm_generation_events,
    get_llm_smartmeter_mode,
    get_llm_used_cases,
    is_smartmeter_bound_to_real_device,
)
from consumption_profiles import consumption_profiles
from read import read_sensors, read_devices
from device import devices
from app.hardware import real_sensors
from models import Sensor, Device

plt.rcParams.update({
    "axes.titlesize": 16,
    "axes.labelsize": 14,
    "xtick.labelsize": 10,
    "ytick.labelsize": 12,
    "legend.fontsize": 12
})

_LLM_PROFILE_PATH = os.path.join("LLM", "smartmeter", "llm_smartmeter_profiles.json")
_LLM_PROFILE_LEGACY_PATH = "llm_smartmeter_profiles.json"
_PROJECT_DIR = Path(__file__).resolve().parent
_SENSOR_MAP_PATH = _PROJECT_DIR / "sensor_map.json"


def _llm_path_variants(path: Path) -> list[Path]:
    """Return the path plus preserved best/human output-folder variants."""
    variants = [path]
    if path.name.endswith("_output"):
        output_dir = path
        output_name = path.name
        for suffix in ("_best_k", "_human_k"):
            variants.append(output_dir.with_name(output_name + suffix))
    elif path.parent.name.endswith("_output"):
        output_dir = path.parent
        output_name = output_dir.name
        for suffix in ("_best_k", "_human_k"):
            variants.append(output_dir.with_name(output_name + suffix) / path.name)
    return variants


def _resolve_llm_path(value: str | None) -> str:
    if not value:
        return ""
    path = Path(value)
    candidates = [path] if path.is_absolute() else [
        Path("LLM") / "smartmeter" / path,
        Path(_LLM_PROFILE_PATH).parent / path,
        Path(_LLM_PROFILE_LEGACY_PATH).parent / path,
    ]
    for candidate in candidates:
        for variant in _llm_path_variants(candidate):
            if variant.exists():
                return str(variant)
    return str(candidates[0])


def _discover_selected_case_csv(appliance_key: str) -> str | None:
    """Find selected_case_latest_incomplete_day.csv for the appliance, tolerant to folder typos."""
    if not appliance_key:
        return None

    # Generic search first: robust to naming variants (e.g. Computer vs Computerr).
    pattern = os.path.join(
        "LLM",
        "smartmeter",
        "*",
        "*",
        "selected_case_latest_incomplete_day.csv",
    )
    candidates = sorted(glob.glob(pattern))

    # Prefer paths that contain the appliance key text.
    key_tokens = {
        "computer": ["computer"],
        "coffee_machine": ["coffee", "machine"],
        "dishwasher": ["dishwasher"],
        "refrigerator": ["refrigerator", "fridge"],
        "washing_machine": ["washing", "machine"],
    }.get(appliance_key, [appliance_key])

    for p in candidates:
        low = p.replace("\\", "/").lower()
        if all(tok in low for tok in key_tokens):
            return p

    # Do not fall back to an unrelated appliance.
    return None


def _load_llm_profiles(path: str = _LLM_PROFILE_PATH) -> dict:
    candidate_paths = [path, _LLM_PROFILE_LEGACY_PATH]
    try:
        for p in candidate_paths:
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for payload in data.values():
                            if not isinstance(payload, dict):
                                continue
                            if "output_dir" in payload:
                                payload["output_dir"] = _resolve_llm_path(str(payload.get("output_dir") or ""))
                            if "pkl_path" in payload:
                                payload["pkl_path"] = _resolve_llm_path(str(payload.get("pkl_path") or ""))
                    return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _appliance_key_from_device_type(device_type: str | None) -> str | None:
    mapping = {
        "computer": "computer",
        "coffee_machine": "coffee_machine",
        "dishwasher": "dishwasher",
        "fridge": "refrigerator",
        "refrigerator": "refrigerator",
        "washing_machine": "washing_machine",
    }
    if not device_type:
        return None
    return mapping.get(str(device_type).strip().lower())


def _associated_device_name(sensor_name: str, sensor_states: dict) -> str | None:
    data = sensor_states.get(sensor_name, {}) if isinstance(sensor_states, dict) else {}
    assoc = data.get("associated_device")
    if isinstance(assoc, str) and assoc.strip():
        return assoc.strip()

    for s in sensors:
        if s.name == sensor_name and getattr(s, "associated_device", None):
            return str(s.associated_device).strip()

    for s in read_sensors:
        if s.name == sensor_name and getattr(s, "associated_device", None):
            return str(s.associated_device).strip()

    return None


def _device_type_for_name(device_name: str | None) -> str | None:
    if not device_name:
        return None
    for d in devices:
        if d.name == device_name:
            return d.type

    for d in read_devices:
        if d.name == device_name:
            return d.type
    return None


def _normalize_profile_token(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return cleaned.strip("_")


def _profile_source_token(payload: dict) -> str:
    source_name = str(payload.get("source_name") or "").strip()
    if source_name:
        return _normalize_profile_token(source_name)
    output_dir = str(payload.get("output_dir") or "").strip()
    if output_dir:
        name = Path(output_dir).name
        if name.endswith("_output"):
            name = name[: -len("_output")]
        return _normalize_profile_token(name)
    return ""


def _profile_match_score(payload: dict, candidates: set[str]) -> int:
    source_token = _profile_source_token(payload)
    if not source_token:
        return 0
    if source_token in candidates:
        return 100
    for candidate in candidates:
        if not candidate:
            continue
        if source_token.endswith(candidate) or candidate.endswith(source_token):
            return 80
        if candidate in source_token or source_token in candidate:
            return 60
    return 0


def _profile_candidates_for_sensor(sensor_name: str, associated_device: str | None) -> set[str]:
    raw_values = {
        sensor_name,
        associated_device or "",
        f"smartmeter_{sensor_name}",
        f"smartmeter_{associated_device}" if associated_device else "",
    }
    candidates = {
        _normalize_profile_token(value)
        for value in raw_values
        if _normalize_profile_token(value)
    }

    # The scenario uses ``fr``/``sm_fr`` while the refrigerator dataset is
    # stored under the historical ``re``/``sm_re`` naming.
    aliases = {
        "fr": {"fr", "re", "fridge", "refrigerator"},
        "re": {"fr", "re", "fridge", "refrigerator"},
        "fridge": {"fr", "re", "fridge", "refrigerator"},
        "refrigerator": {"fr", "re", "fridge", "refrigerator"},
    }
    for value in (sensor_name, associated_device):
        token = _normalize_profile_token(value)
        if token.startswith("smartmeter_"):
            token = token[len("smartmeter_"):]
        if token.startswith("sm_"):
            token = token[len("sm_"):]
        for alias in aliases.get(token, set()):
            candidates.update({
                alias,
                f"sm_{alias}",
                f"smartmeter_{alias}",
                f"smartmeter_sm_{alias}",
            })
    return candidates


def _profile_for_smartmeter(
    profiles: dict,
    appliance_key: str,
    sensor_name: str,
    associated_device: str | None,
) -> tuple[dict | None, str]:
    candidates = _profile_candidates_for_sensor(sensor_name, associated_device)
    by_source = profiles.get("by_source")
    appliance_profiles: list[dict] = []
    if isinstance(by_source, dict):
        for payload in by_source.values():
            if isinstance(payload, dict) and str(payload.get("appliance_key") or "") == appliance_key:
                appliance_profiles.append(payload)

    scored = [
        (_profile_match_score(payload, candidates), payload)
        for payload in appliance_profiles
    ]
    scored = [(score, payload) for score, payload in scored if score > 0]
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1], "llm_profile_by_source"

    payload = profiles.get(appliance_key)
    if isinstance(payload, dict):
        if _profile_source_token(payload) and _profile_match_score(payload, candidates) <= 0:
            return None, ""
        return payload, "llm_profile_json"
    return None, ""


def _llm_info_for_smartmeter(sensor_name: str, sensor_states: dict) -> dict:
    profiles = _load_llm_profiles()
    associated_device = _associated_device_name(sensor_name, sensor_states)
    device_type = _device_type_for_name(associated_device)
    appliance_key = _appliance_key_from_device_type(device_type)
    if not appliance_key:
        return {}
    payload, source = _profile_for_smartmeter(profiles, appliance_key, sensor_name, associated_device)
    if isinstance(payload, dict):
        out = dict(payload)
        out.setdefault("source", source or "llm_profile_json")
        out.setdefault("associated_device", associated_device)
        requested_mode = get_llm_smartmeter_mode()
        realtime_bound = is_smartmeter_bound_to_real_device(sensor_name)
        out["smartmeter_mode_requested"] = requested_mode
        out["smartmeter_realtime_bound"] = realtime_bound
        out["smartmeter_mode"] = (
            requested_mode
            if requested_mode != "realtime_dt" or realtime_bound
            else "completion_simulation"
        )
        logged_used_cases, logged_generation_events = _logged_llm_cases(sensor_name)
        out["used_cases"] = logged_used_cases or get_llm_used_cases(sensor_name)
        out["generation_events"] = (
            logged_generation_events or get_llm_generation_events(sensor_name)
        )
        if out["generation_events"]:
            latest_event = out["generation_events"][-1]
            out["runtime_cycle_id"] = latest_event.get("cycle_id")
            out["runtime_cluster"] = latest_event.get("cluster")
        elif out["used_cases"]:
            latest_case = out["used_cases"][-1]
            out["runtime_cycle_id"] = latest_case.get("cycle_id")
            out["runtime_cluster"] = latest_case.get("cluster")
        if "output_dir" in out:
            out["output_dir"] = _resolve_llm_path(str(out.get("output_dir") or ""))
            summary_path = Path(str(out["output_dir"])) / "completed_last_cluster_summary.json"
            if summary_path.is_file():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    out.setdefault("completion_reference_cycle_id", summary.get("matched_cycle_id"))
                    out.setdefault("completion_reference_cluster", summary.get("matched_cluster"))
                    out.setdefault("completion_partial_duration_minutes", summary.get("partial_duration_minutes"))
                    out.setdefault("completion_added_minutes", summary.get("completion_added_minutes"))
                except Exception:
                    pass
        if "pkl_path" not in out and "output_dir" in out:
            out["pkl_path"] = os.path.join(str(out["output_dir"]), "llm_runtime_cycles.pkl")
        if "pkl_path" in out:
            out["pkl_path"] = _resolve_llm_path(str(out.get("pkl_path") or ""))
        return out

    profile_payload = consumption_profiles.get(device_type)
    if not isinstance(profile_payload, dict):
        return {}
    profile = profile_payload.get("profile") or {}
    keys = sorted(float(k) for k in profile.keys()) if profile else []
    return {
        "appliance_key": appliance_key,
        "device_type": device_type,
        "associated_device": associated_device,
        "source": "consumption_profile",
        "profile_duration_min": int(keys[-1]) if keys else None,
        "profile_points": len(keys),
        "standby_w": float(profile_payload.get("standby", 0.0) or 0.0),
    }


def _logged_llm_cases(sensor_name: str) -> tuple[list[dict], list[dict]]:
    path = _latest_interactions_csv()
    if not path:
        return [], []

    normal_cases = []
    generated_cases = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if row.get("event_type") != "llm_cycle" or row.get("name") != sensor_name:
                    continue
                try:
                    payload = json.loads(row.get("extra") or "{}")
                except Exception:
                    payload = {}
                try:
                    cycle_id = int(payload.get("cycle_id", row.get("value")))
                except Exception:
                    continue
                try:
                    cluster = int(payload.get("cluster"))
                except Exception:
                    cluster = None
                event_type = str(payload.get("event_type") or "normal")
                item = {
                    "cycle_id": cycle_id,
                    "cluster": cluster,
                    "started_at": row.get("timestamp_sim"),
                    "duration_minutes": payload.get("duration_minutes"),
                    "event_type": event_type,
                    "case_type": payload.get("case_type", "normal"),
                    "source_name": payload.get("source_name", ""),
                }
                if event_type in {"completion_match", "dt_prediction"}:
                    generated_cases.append(item)
                else:
                    normal_cases.append(item)
    except Exception:
        return [], []
    return normal_cases, generated_cases


def _load_profile_reference_values(info: dict) -> np.ndarray:
    device_type = str(info.get("device_type") or "").strip()
    payload = consumption_profiles.get(device_type)
    if not isinstance(payload, dict):
        return np.array([], dtype=float)
    profile = payload.get("profile") or {}
    if not isinstance(profile, dict) or not profile:
        return np.array([], dtype=float)
    try:
        keys = sorted(float(k) for k in profile.keys())
        vals = [float(profile[k]) for k in keys]
    except Exception:
        return np.array([], dtype=float)
    return np.asarray(vals, dtype=float)


def _zscore(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=float)
    if arr.size == 0:
        return arr
    mu = float(np.mean(arr))
    sigma = float(np.std(arr))
    if sigma < 1e-12:
        return arr - mu
    return (arr - mu) / sigma


def _resample_by_index(values: np.ndarray, target_len: int = 200) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return values
    if values.size == 1:
        return np.repeat(values, target_len)
    src_x = np.linspace(0.0, 1.0, num=values.size)
    dst_x = np.linspace(0.0, 1.0, num=target_len)
    return np.interp(dst_x, src_x, values)


def _find_true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif (not v) and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def _extract_activity_segment(values: np.ndarray, min_len: int = 8) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return values
    if values.size < max(3, min_len):
        return values

    lo = float(np.percentile(values, 20))
    hi = float(np.percentile(values, 95))
    dyn = hi - lo
    if dyn < 1e-6:
        return values

    threshold = lo + 0.35 * dyn
    active = values > threshold
    runs = [r for r in _find_true_runs(active) if (r[1] - r[0]) >= min_len]

    if not runs:
        fallback = values > float(np.percentile(values, 80))
        runs = [r for r in _find_true_runs(fallback) if (r[1] - r[0]) >= max(4, min_len // 2)]
        if not runs:
            return values

    def run_score(run: tuple[int, int]) -> float:
        s, e = run
        seg = values[s:e]
        return float(np.clip(seg - threshold, 0.0, None).sum())

    best_s, best_e = max(runs, key=run_score)
    pad = max(2, int(0.15 * (best_e - best_s)))
    s = max(0, best_s - pad)
    e = min(len(values), best_e + pad)
    return values[s:e]


def _extract_activity_runs(values: np.ndarray, min_len: int = 8) -> list[tuple[np.ndarray, int, int]]:
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return []

    lo = float(np.percentile(values, 20))
    hi = float(np.percentile(values, 95))
    dyn = hi - lo
    if dyn < 1e-6:
        return [(values, 0, values.size)]

    threshold = lo + 0.35 * dyn
    active = values > threshold
    runs = [r for r in _find_true_runs(active) if (r[1] - r[0]) >= min_len]
    if not runs:
        return []

    out = []
    for s, e in runs:
        pad = max(2, int(0.12 * (e - s)))
        rs = max(0, s - pad)
        re = min(len(values), e + pad)
        seg = values[rs:re]
        if seg.size >= min_len:
            out.append((seg, rs, re))
    return out


def _align_by_best_lag(a: np.ndarray, b: np.ndarray, max_lag: int = 35) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.size == 0 or b.size == 0:
        return np.array([], dtype=float), np.array([], dtype=float)

    best_corr = -np.inf
    best_pair = (a, b)

    for lag in range(-max_lag, max_lag + 1):
        if lag > 0:
            x, y = a[lag:], b[:-lag]
        elif lag < 0:
            x, y = a[:lag], b[-lag:]
        else:
            x, y = a, b

        if x.size < 20 or y.size < 20:
            continue
        if np.std(x) < 1e-12 or np.std(y) < 1e-12:
            continue

        corr = float(np.corrcoef(x, y)[0, 1])
        if np.isfinite(corr) and corr > best_corr:
            best_corr = corr
            best_pair = (x, y)

    return best_pair


def _load_representative_cycle_values(info: dict) -> np.ndarray:
    try:
        cycle_id = int(float(info.get("runtime_cycle_id", info.get("selected_cycle_id"))))
    except Exception:
        return np.array([], dtype=float)

    pkl_path = _resolve_llm_path(str(info.get("pkl_path") or "").strip())
    if not pkl_path or not os.path.isfile(pkl_path):
        return np.array([], dtype=float)

    try:
        with open(pkl_path, "rb") as fp:
            cycles = pickle.load(fp)
    except Exception:
        return np.array([], dtype=float)

    if not isinstance(cycles, list):
        return np.array([], dtype=float)

    selected = None
    for c in cycles:
        try:
            if int(c.get("cycle_id")) == cycle_id:
                selected = c
                break
        except Exception:
            continue
    if not selected:
        return np.array([], dtype=float)

    df = pd.DataFrame(selected.get("data") or [])
    if df.empty or "value" not in df.columns:
        return np.array([], dtype=float)

    vals = pd.to_numeric(df["value"], errors="coerce").dropna().to_numpy(dtype=float)
    return vals


def _load_cluster_mean_values(info: dict) -> np.ndarray:
    try:
        selected_cluster = int(float(info.get("runtime_cluster", info.get("selected_cluster"))))
        chosen_k = int(float(info.get("chosen_k")))
    except Exception:
        return np.array([], dtype=float)

    output_dir = _resolve_llm_path(str(info.get("output_dir") or "").strip())
    pkl_path = _resolve_llm_path(str(info.get("pkl_path") or "").strip())
    if not output_dir or not pkl_path:
        return np.array([], dtype=float)

    clusters_csv = os.path.join(output_dir, f"results_k{chosen_k}", "clusters.csv")
    if not os.path.isfile(clusters_csv) or not os.path.isfile(pkl_path):
        return np.array([], dtype=float)

    try:
        df_clusters = pd.read_csv(clusters_csv)
        df_clusters["cluster"] = pd.to_numeric(df_clusters.get("cluster"), errors="coerce")
        df_clusters["cycle_id"] = pd.to_numeric(df_clusters.get("cycle_id"), errors="coerce")
        cycle_ids = (
            df_clusters[df_clusters["cluster"] == float(selected_cluster)]["cycle_id"]
            .dropna()
            .astype(int)
            .tolist()
        )
    except Exception:
        return np.array([], dtype=float)

    if not cycle_ids:
        return np.array([], dtype=float)

    try:
        with open(pkl_path, "rb") as fp:
            cycles = pickle.load(fp)
    except Exception:
        return np.array([], dtype=float)

    if not isinstance(cycles, list):
        return np.array([], dtype=float)

    by_id = {}
    for c in cycles:
        try:
            by_id[int(c.get("cycle_id"))] = c
        except Exception:
            continue

    curves = []
    for cid in cycle_ids:
        c = by_id.get(int(cid))
        if not c:
            continue
        df = pd.DataFrame(c.get("data") or [])
        if df.empty or "value" not in df.columns:
            continue
        vals = pd.to_numeric(df["value"], errors="coerce").dropna().to_numpy(dtype=float)
        if vals.size < 10:
            continue
        seg = _extract_activity_segment(vals, min_len=8)
        if seg.size < 10:
            continue
        curves.append(_zscore(_resample_by_index(seg, target_len=200)))

    if not curves:
        return np.array([], dtype=float)

    return np.mean(np.vstack(curves), axis=0)


def _compute_shape_similarity(df_sim: pd.DataFrame, info: dict) -> dict:
    out = {
        "shape_available": False,
        "shape_corr": None,
        "shape_rmse": None,
        "shape_ref": None,
        "shape_window_start": None,
        "shape_window_end": None,
    }
    if df_sim is None or df_sim.empty:
        return out

    sim_series = pd.to_numeric(df_sim["value"], errors="coerce").dropna()
    if sim_series.empty:
        return out
    sim_vals = sim_series.to_numpy(dtype=float)
    rep_vals = _load_representative_cycle_values(info)
    shape_ref = "runtime_cycle"
    if rep_vals.size < 10:
        rep_vals = _load_profile_reference_values(info)
        shape_ref = "consumption_profile"
    rep_seg = _extract_activity_segment(rep_vals, min_len=8)
    if rep_seg.size < 10:
        return out

    sim_runs = _extract_activity_runs(sim_vals, min_len=8)
    if not sim_runs:
        return out

    target_len = rep_seg.size

    def _run_score(run_item: tuple[np.ndarray, int, int]) -> tuple[float, float]:
        seg, _s, _e = run_item
        # Primary: closest duration to selected cycle. Secondary: higher activity energy.
        dur_delta = abs(float(seg.size) - float(target_len))
        energy = float(np.clip(seg - np.percentile(seg, 20), 0.0, None).sum())
        return (dur_delta, -energy)

    sim_seg, sim_s, sim_e = min(sim_runs, key=_run_score)

    sim_norm = _zscore(_resample_by_index(sim_seg, target_len=200))
    rep_norm = _zscore(_resample_by_index(rep_seg, target_len=200))

    sim_aligned, rep_aligned = _align_by_best_lag(sim_norm, rep_norm, max_lag=20)

    if sim_aligned.size < 20 or rep_aligned.size < 20:
        return out

    rmse = float(np.sqrt(np.mean((sim_aligned - rep_aligned) ** 2)))
    if np.std(sim_aligned) < 1e-12 or np.std(rep_aligned) < 1e-12:
        corr = None
    else:
        corr = float(np.corrcoef(sim_aligned, rep_aligned)[0, 1])

    out["shape_available"] = True
    out["shape_corr"] = corr
    out["shape_rmse"] = rmse
    out["shape_ref"] = shape_ref
    try:
        idx = sim_series.index
        if len(idx) >= sim_e and sim_s < sim_e:
            out["shape_window_start"] = str(pd.Timestamp(idx[sim_s]).strftime("%H:%M"))
            out["shape_window_end"] = str(pd.Timestamp(idx[sim_e - 1]).strftime("%H:%M"))
    except Exception:
        pass
    out["_sim_norm"] = sim_aligned
    out["_rep_norm"] = rep_aligned
    return out


def _draw_smartmeter_info_box(ax, info: dict):
    ax.set_axis_off()
    ax.set_facecolor("#f7f7f7")
    if info:
        source_name = str(info.get("source_name") or "").strip()
        if not source_name:
            output_dir = str(info.get("output_dir") or "").strip()
            source_name = Path(output_dir).name if output_dir else str(info.get("source") or "?")
            if source_name.endswith("_output"):
                source_name = source_name[: -len("_output")]
        lines = [
            f"LLM profile: {source_name}",
            f"appliance: {info.get('appliance_key', '?')}",
            f"mode: {info.get('smartmeter_mode', '?')}",
        ]
        if str(info.get("source") or "").startswith("llm_profile"):
            used_cases = [
                item for item in (info.get("used_cases") or [])
                if item.get("case_type", "normal") == "normal"
            ]
            generation_events = [
                item for item in (info.get("generation_events") or [])
                if item.get("event_type") in {"completion_match", "dt_prediction"}
            ]
            if used_cases and generation_events:
                lines[2] = "mode: mixed"

            runtime_line = "normal cases: -"
            if used_cases:
                runtime_line = "normal cases: " + ", ".join(
                    f"case_{item.get('cycle_id', '?')}(cluster_{item.get('cluster', '?')})"
                    for item in used_cases[-8:]
                )

            completion_line = "completed cases: -"
            if generation_events:
                completion_line = (
                    "completed cases: "
                    + ", ".join(
                        f"case_{item.get('cycle_id', '?')}(cluster_{item.get('cluster', '?')})"
                        for item in generation_events[-8:]
                    )
                )

            lines.extend([runtime_line, completion_line])
        else:
            lines.extend([
                f"device type: {info.get('device_type', '?')}",
                f"profile duration: {info.get('profile_duration_min', '?')} min",
                f"profile points: {info.get('profile_points', '?')}",
                f"standby: {info.get('standby_w', '?')} W",
            ])
    else:
        lines = [
            "Smart Meter metadata not found",
            "No LLM profile in llm_smartmeter_profiles.json",
            "No fallback consumption profile for this device",
        ]
    ax.text(
        0.03,
        0.97,
        "\n".join(lines),
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=8.5,
        wrap=True,
    )

def _draw_llm_generation_windows(ax, info: dict):
    generation_events = [
        item for item in (info.get("generation_events") or [])
        if item.get("event_type") in {"completion_match", "dt_prediction"}
    ]
    if not generation_events:
        return

    added_label = False
    for item in generation_events[-6:]:
        try:
            start = pd.to_datetime(item.get("started_at"), errors="raise")
            duration = float(item.get("duration_minutes") or 0.0)
        except Exception:
            continue
        if duration <= 0:
            continue

        end = start + pd.to_timedelta(duration, unit="m")
        label = (
            "LLM completion match"
            if item.get("event_type") == "completion_match"
            else "LLM DT prediction"
        ) if not added_label else None
        ax.axvspan(start, end, color="tab:green", alpha=0.08, linewidth=0, label=label, zorder=0)
        ax.axvline(start, color="tab:green", linestyle="--", linewidth=0.9, alpha=0.45, zorder=1)
        added_label = True

def _parse_datetime(time_str: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    # if only "HH:MM" arrives I turn it into dummy datetime with date 1900-01-01
    return datetime.strptime(time_str, "%H:%M").replace(year=1900, month=1, day=1)

def _align_len(lst, target_len, fill=None):
    if lst is None:
        return [fill] * target_len
    out = list(lst)
    if len(out) < target_len:
        out.extend([fill] * (target_len - len(out)))
    elif len(out) > target_len:
        out = out[:target_len]
    return out

def _build_dataframe(time_list_str, values_list):
    time_list = [_parse_datetime(t) for t in time_list_str]
    vals = pd.to_numeric(pd.Series(values_list), errors="coerce")
    df = pd.DataFrame({"timestamp": time_list, "value": vals})
    df = df.dropna(subset=["value"])
    if df.empty:
        return df
    df.sort_values("timestamp", inplace=True)
    df = df.drop_duplicates(subset="timestamp", keep="last")
    df.set_index("timestamp", inplace=True)
    # resample per minute and ffill for clean lines
    df = df.resample("1min").ffill()
    return df

def _sensor_type(name: str, sensor_states: dict):
    # from sensor_states
    t = sensor_states.get(name, {}).get("type")
    if t:
        return t
    # from runtime
    for s in sensors:
        if s.name == name:
            return s.type
    # from uploaded from files
    for s in read_sensors:
        if s.name == name:
            return s.type
    # if 'consumption' exists = Smart Meter
    if "consumption" in sensor_states.get(name, {}):
        return "Smart Meter"
    return None


def _latest_interactions_csv():
    candidates = []

    def _collect(root_dir: str):
        if not os.path.isdir(root_dir):
            return
        for root, _dirs, files in os.walk(root_dir):
            if "interactions.csv" in files:
                csv_path = os.path.join(root, "interactions.csv")
                candidates.append((os.path.getmtime(csv_path), csv_path))

    # New structured location first, then legacy logs fallback.
    _collect("saves")
    _collect("logs")

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]

def _load_consumption_from_interactions(sensor_name: str) -> dict:
    path = _latest_interactions_csv()
    if not path:
        return {}
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("event_type") == "sensor" and row.get("name") == sensor_name:
                    # 'value' is consumption detected by the sensor
                    ts = row.get("timestamp_sim", "")
                    val = row.get("value", "")
                    try:
                        out[ts] = float(val)
                    except Exception:
                        # if not numeric, skip
                        continue
    except Exception:
        return {}
    return out

def _match_full_or_suffix(times: list[str], full_ts_to_val: dict) -> list:
    values = []
    keys = list(full_ts_to_val.keys())
    for t in times:
        key = None
        if len(t) == 5:  # HH:MM
            # Search for a key that ends with ' HH:MM'
            suffix = f" {t}"
            for k in keys:
                if k.endswith(suffix):
                    key = k
                    break
        else:
            if t in full_ts_to_val:
                key = t
        values.append(full_ts_to_val[key] if key is not None else None)
    return values


def _load_sensor_map(path: str | os.PathLike | None = None) -> dict:
    """Load sensor_map.json if exists"""
    resolved_path = Path(path) if path is not None else _SENSOR_MAP_PATH
    if not resolved_path.is_absolute():
        resolved_path = _PROJECT_DIR / resolved_path
    try:
        if resolved_path.is_file():
            with resolved_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _get_binding_dht_gpio(sensor_name: str) -> int | None:
    """Get GPIO binding for temperature sensor"""
    mp = _load_sensor_map()
    v = mp.get(sensor_name)
    if isinstance(v, dict) and v.get("by") == "dht":
        try:
            return int(v.get("gpio"))
        except Exception:
            return None
    return None


def _load_real_temperature_series(sensor_name: str) -> pd.DataFrame:
    """Load a DHT series by label, falling back to its GPIO binding."""
    try:
        df = real_sensors.load_temp_by_label_any_csv(sensor_name)
    except Exception:
        df = pd.DataFrame()

    if df is not None and not df.empty and "value" in df.columns:
        return df

    gpio = _get_binding_dht_gpio(sensor_name)
    if gpio is None:
        return pd.DataFrame()
    try:
        return real_sensors.load_temp_by_gpio_any_csv(gpio)
    except Exception:
        return pd.DataFrame()


def _recorded_real_temperature_series(time_list, sensor_data: dict) -> pd.DataFrame:
    """Build the real curve recorded alongside simulated temperature samples."""
    values = sensor_data.get("real_state") if isinstance(sensor_data, dict) else None
    if not values:
        return pd.DataFrame()
    aligned_values = _align_len(values, len(time_list), fill=None)
    return _build_dataframe(time_list, aligned_values)


def _get_binding_gpio(sensor_name: str, expected_kind: str | None = None) -> tuple[int, str] | None:
    """Get GPIO binding for PIR, Switch, and Weight sensors."""
    mp = _load_sensor_map()
    v = mp.get(sensor_name)
    if not isinstance(v, dict) or v.get("by") != "gpio":
        return None
    kind = str(v.get("kind") or expected_kind or "").strip().lower()
    if expected_kind and kind != expected_kind:
        return None
    try:
        return int(v.get("gpio")), kind
    except Exception:
        return None


def _get_binding_ip(sensor_name: str) -> str | None:
    """Get IP binding for smart meter sensor"""
    mp = _load_sensor_map()
    v = mp.get(sensor_name)
    if isinstance(v, dict) and v.get("by") == "ip" and isinstance(v.get("value"), str):
        ip = v["value"].strip()
        return ip if ip else None
    return None


def _simple_rebase_to_day(df: pd.DataFrame, ref_day: date) -> pd.DataFrame:
    """Rebase dataframe index to a reference day"""
    if df is None or df.empty:
        return df
    df = df.copy()
    new_index = [datetime.combine(ref_day, ts.time()) for ts in df.index]
    df.index = new_index
    df = df.sort_index()
    return df


def _align_real_series_to_simulation(df_real: pd.DataFrame, df_sim: pd.DataFrame) -> pd.DataFrame:
    """Select the relevant real day, rebase it, and crop it to the simulation."""
    if df_real is None or df_real.empty or df_sim is None or df_sim.empty:
        return pd.DataFrame()

    df_real = df_real.dropna(subset=["value"]).sort_index()
    if df_real.empty:
        return pd.DataFrame()

    ref_day = df_sim.index[0].date()
    available_days = sorted(set(df_real.index.date))
    if ref_day in available_days:
        source_day = ref_day
    else:
        previous_days = [day for day in available_days if day <= ref_day]
        source_day = previous_days[-1] if previous_days else available_days[0]

    selected = df_real[df_real.index.date == source_day]
    selected = _simple_rebase_to_day(selected, ref_day)
    sim_min = df_sim.index[0]
    sim_max = df_sim.index[-1]
    return selected[(selected.index >= sim_min) & (selected.index <= sim_max)]


# Graphic design (manual)

def show_graphs(canvas, sensor_states):
    def generate_graph(sensor, sensor_data, frame):
        fig, ax = plt.subplots(figsize=(14, 6), facecolor="white")
        ax.set_facecolor("white")
        smartmeter_info = None

        time_list = sensor_data.get('time', [])
        state_list = sensor_data.get('state', [])

        sensor_type = _sensor_type(sensor, sensor_states)

        if sensor_type == "Smart Meter":
            consumption_list = sensor_data.get('consumption')
            if not consumption_list:
                m = _load_consumption_from_interactions(sensor)
                if m:
                    consumption_list = _match_full_or_suffix(time_list, m)
            if not consumption_list:
                # no consumption available: blank message and graph
                ax.text(0.5, 0.5, "Consumption not available for this Smart Meter", ha="center", va="center", transform=ax.transAxes)
                y_series = []
            else:
                y_series = _align_len(consumption_list, len(time_list), fill=None)
            y_label = "Power (W)"
        else:
            y_series = state_list
            if sensor_type == "Temperature":
                y_label = "Temperature (°C)"
            elif sensor_type in ("PIR", "Switch"):
                y_label = "State"
            else:
                y_label = "Value"

        df = _build_dataframe(time_list, y_series) if y_series else pd.DataFrame()

        # Load real data if binding exists
        df_real = pd.DataFrame()
        real_source = "none"
        if sensor_type == "Temperature":
            df_real = _recorded_real_temperature_series(time_list, sensor_data)
            if not df_real.empty:
                real_source = "simulation"
            else:
                df_real = _load_real_temperature_series(sensor)
                real_source = "CSV" if not df_real.empty else "none"
        elif sensor_type in ("PIR", "Switch", "Weight"):
            kind = sensor_type.lower()
            binding = _get_binding_gpio(sensor, kind)
            if binding is not None:
                gpio, real_kind = binding
                df_real = real_sensors.load_value_by_gpio_any_csv(gpio, real_kind)
        elif sensor_type == "Smart Meter":
            # Smart Meter "real" overlay is intentionally disabled.
            df_real = pd.DataFrame()

        # Rebase real data to match the simulated timeline.
        if real_source != "simulation" and not df_real.empty and not df.empty:
            df_real = _align_real_series_to_simulation(df_real, df)

        if sensor_type == "Temperature":
            print(f"[TEMP GRAPH] {sensor}: {len(df_real)} real samples (source: {real_source})")

        if df.empty and df_real.empty:
            if y_series:  # data existed but was not valid
                ax.text(0.5, 0.5, "No valid data to plot", ha="center", va="center", transform=ax.transAxes)
        else:
            # Plot simulated data
            if not df.empty:
                sim_color = 'blue' if sensor_type == "Smart Meter" else 'blue'
                unique_vals = set(df["value"].dropna().unique().tolist())
                is_binary = unique_vals.issubset({0.0, 1.0})
                if is_binary and sensor_type not in ("Smart Meter", "Temperature"):
                    ax.plot(df.index, df["value"], drawstyle='steps-post', marker='o', linestyle='-', label=f"{sensor} (sim)", color=sim_color)
                    ax.set_ylim(-0.1, 1.1)
                    ax.set_yticks([0, 1])
                else:
                    ax.plot(df.index, df["value"], linestyle='-', linewidth=1.5, marker='o', markersize=2, label=f"{sensor} (sim)", color=sim_color)

            # Plot real data if available
            if not df_real.empty:
                ax.plot(df_real.index, df_real["value"], linestyle='--', linewidth=2.0, label=f"{sensor} (real)", color='orange', zorder=3)

        # Use simulated data for date, fallback to real
        df_for_date = df if not df.empty else df_real
        try:
            date_str = df_for_date.index[0].date() if not df_for_date.empty else ""
        except Exception:
            date_str = ""
        title = f"{sensor} - {date_str}"
        if sensor_type == "Smart Meter":
            info = _llm_info_for_smartmeter(sensor, sensor_states)
            sim_cmp = _compute_shape_similarity(df, info)
            info.update({k: v for k, v in sim_cmp.items() if not str(k).startswith("_")})
            _draw_llm_generation_windows(ax, info)
            smartmeter_info = info
        ax.set_title(title)
        ax.set_xlabel("Time")
        ax.set_ylabel(y_label)

        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc="upper right")
        ax.grid(True, linestyle=':', alpha=0.7)
        ax.yaxis.set_major_locator(MaxNLocator(8))
        ax.yaxis.set_major_formatter(FormatStrFormatter('%.1f'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        if smartmeter_info:
            fig.tight_layout(rect=[0.0, 0.0, 0.74, 1.0])
            info_ax = fig.add_axes([0.765, 0.12, 0.22, 0.78])
            _draw_smartmeter_info_box(info_ax, smartmeter_info)
        else:
            fig.tight_layout()

        canvas_plot = FigureCanvasTkAgg(fig, master=frame)
        toolbar = NavigationToolbar2Tk(canvas_plot, frame)
        toolbar.update()
        toolbar.pack(side=tk.TOP, fill=tk.X)
        canvas_plot.draw()
        canvas_plot.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        plt.close(fig)

    def save_selected_logs():
        selected = [s for s, state in select_sensors.items() if state.get()]
        if not selected:
            messagebox.showwarning("Warning", "Select at least one sensor to generate the graph.")
            return

        graph_window = tk.Toplevel()
        graph_window.title("Graphs from sensors")

        container = ttk.Frame(graph_window)
        canvas = tk.Canvas(container)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        container.pack(fill="both", expand=True)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for sensor in selected:
            frame = ttk.Frame(scrollable_frame)
            frame.pack(fill="both", pady=10)
            generate_graph(sensor, sensor_states[sensor], frame)

    selection_window = tk.Toplevel()
    selection_window.title("Select sensors")

    tk.Label(selection_window, text="Select the sensors for which to generate the graph:").pack(pady=10)
    select_sensors = {s: tk.BooleanVar() for s in sensor_states.keys()}

    select_all_var = tk.BooleanVar(value=False)
    def on_toggle_select_all():
        val = bool(select_all_var.get())
        for var in select_sensors.values():
            var.set(val)

    tk.Checkbutton(
        selection_window,
        text="Select all",
        variable=select_all_var,
        command=on_toggle_select_all,
        fg="blue"
    ).pack(anchor="w", pady=(0, 5))

    for sensor, state in select_sensors.items():
        tk.Checkbutton(selection_window, text=sensor, variable=state).pack(anchor="w")

    tk.Button(selection_window, text="Generate Graphs", command=save_selected_logs).pack(pady=10)

# Graphic design (auto)

def show_graphs_auto(sensor_states, selected_keys, target_frame):
    def generate_graph(sensor, sensor_data, frame):
        fig, ax = plt.subplots(figsize=(14, 6), facecolor="white")
        ax.set_facecolor("white")
        smartmeter_info = None

        time_list = sensor_data.get('time', [])
        state_list = sensor_data.get('state', [])
        sensor_type = _sensor_type(sensor, sensor_states)

        if sensor_type == "Smart Meter":
            consumption_list = sensor_data.get('consumption')
            if not consumption_list:
                m = _load_consumption_from_interactions(sensor)
                if m:
                    consumption_list = _match_full_or_suffix(time_list, m)
            if not consumption_list:
                ax.text(0.5, 0.5, "Consumption not available for this Smart Meter", ha="center", va="center", transform=ax.transAxes)
                y_series = []
            else:
                y_series = _align_len(consumption_list, len(time_list), fill=None)
            y_label = "Power (W)"
        else:
            y_series = state_list
            if sensor_type == "Temperature":
                y_label = "Temperature (°C)"
            elif sensor_type in ("PIR", "Switch"):
                y_label = "State"
            else:
                y_label = "Value"

        df = _build_dataframe(time_list, y_series) if y_series else pd.DataFrame()

        # Load real data if binding exists
        df_real = pd.DataFrame()
        real_source = "none"
        if sensor_type == "Temperature":
            df_real = _recorded_real_temperature_series(time_list, sensor_data)
            if not df_real.empty:
                real_source = "simulation"
            else:
                df_real = _load_real_temperature_series(sensor)
                real_source = "CSV" if not df_real.empty else "none"
        elif sensor_type in ("PIR", "Switch", "Weight"):
            kind = sensor_type.lower()
            binding = _get_binding_gpio(sensor, kind)
            if binding is not None:
                gpio, real_kind = binding
                df_real = real_sensors.load_value_by_gpio_any_csv(gpio, real_kind)
        elif sensor_type == "Smart Meter":
            # Smart Meter "real" overlay is intentionally disabled.
            df_real = pd.DataFrame()

        # Rebase real data to match the simulated timeline.
        if real_source != "simulation" and not df_real.empty and not df.empty:
            df_real = _align_real_series_to_simulation(df_real, df)

        if sensor_type == "Temperature":
            print(f"[TEMP GRAPH] {sensor}: {len(df_real)} real samples (source: {real_source})")

        if df.empty and df_real.empty:
            if y_series:
                ax.text(0.5, 0.5, "No valid data to plot", ha="center", va="center", transform=ax.transAxes)
        else:
            # Plot simulated data
            if not df.empty:
                sim_color = 'blue' if sensor_type == "Smart Meter" else 'blue'
                unique_vals = set(df["value"].dropna().unique().tolist())
                is_binary = unique_vals.issubset({0.0, 1.0})
                if is_binary and sensor_type not in ("Smart Meter", "Temperature"):
                    ax.plot(df.index, df["value"], drawstyle='steps-post', marker='o', linestyle='-', label=f"{sensor} (sim)", color=sim_color)
                    ax.set_ylim(-0.1, 1.1)
                    ax.set_yticks([0, 1])
                else:
                    ax.plot(df.index, df["value"], linestyle='-', linewidth=1.5, marker='o', markersize=2, label=f"{sensor} (sim)", color=sim_color)

            # Plot real data if available
            if not df_real.empty:
                ax.plot(df_real.index, df_real["value"], linestyle='--', linewidth=2.0, label=f"{sensor} (real)", color='orange', zorder=3)

        title = f"Sensor trend: {sensor}"
        if sensor_type == "Smart Meter":
            info = _llm_info_for_smartmeter(sensor, sensor_states)
            sim_cmp = _compute_shape_similarity(df, info)
            info.update({k: v for k, v in sim_cmp.items() if not str(k).startswith("_")})
            _draw_llm_generation_windows(ax, info)
            smartmeter_info = info
        ax.set_title(title)
        ax.set_xlabel("Time")
        ax.set_ylabel(y_label)

        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc="upper right")
        ax.grid(True, linestyle=':', alpha=0.7)
        ax.yaxis.set_major_locator(MaxNLocator(8))
        ax.yaxis.set_major_formatter(FormatStrFormatter('%.1f'))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
        if smartmeter_info:
            fig.tight_layout(rect=[0.0, 0.0, 0.74, 1.0])
            info_ax = fig.add_axes([0.765, 0.12, 0.22, 0.78])
            _draw_smartmeter_info_box(info_ax, smartmeter_info)
        else:
            fig.tight_layout()

        canvas_plot = FigureCanvasTkAgg(fig, master=frame)
        toolbar = NavigationToolbar2Tk(canvas_plot, frame)
        toolbar.update()
        toolbar.pack(side=tk.TOP, fill=tk.X)
        canvas_plot.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        canvas_plot.draw()
        plt.close(fig)

    # clean target_frame
    for w in target_frame.winfo_children():
        w.destroy()

    container = ttk.Frame(target_frame)
    container.pack(fill="both", expand=True)

    for key in selected_keys:
        if key not in sensor_states:
            continue
        card = ttk.Frame(container)
        card.pack(fill="x", pady=10)
        generate_graph(key, sensor_states[key], card)
