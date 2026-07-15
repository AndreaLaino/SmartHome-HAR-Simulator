from __future__ import annotations

import copy
import csv
import importlib.util
import json
import math
import pickle
import re
import shutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import numpy as np
import pandas as pd
import tkinter as tk
from tkinter import messagebox, ttk
from app.io.safe_dialog import ask_open_file, ask_open_files, ask_save_file, ask_directory

from app.context import AppContext
from app.logging_setup import setup_logging
from app.save_paths import get_or_create_current_save_session, get_session_subdir
from read import read_devices

logger = setup_logging("controllers.llm_smartmeter")

BASE_DIR = Path(__file__).resolve().parents[2]
LLM_SM_DIR = BASE_DIR / "LLM" / "smartmeter"

APPLIANCE_OPTIONS = {
    "Computer": {
        "key": "computer",
        "folder": "Computer",
        "output": "computer_case_output",
    },
    "Coffee Machine": {
        "key": "coffee_machine",
        "folder": "Coffee_machine",
        "output": "coffee_machine_output",
    },
    "Dishwasher": {
        "key": "dishwasher",
        "folder": "Dishwasher",
        "output": "dishwasher_output",
    },
    "Refrigerator": {
        "key": "refrigerator",
        "folder": "Refrigerator",
        "output": "refrigerator_output",
    },
    "Washing Machine": {
        "key": "washing_machine",
        "folder": "Washing_machine",
        "output": "washing_machine_output",
    },
}


@dataclass
class LlmRunResult:
    chosen_k: int
    appliance_key: str
    source_name: str
    selected_cycle_id: int
    dominant_cluster: int
    dominant_cluster_n_cycles: int
    selected_cluster: int
    output_dir: Path
    cases_eval_csv: Path
    selected_case_csv: Path
    completion_summary_json: Path | None = None
    completion_data_csv: Path | None = None
    completion_plot_path: Path | None = None
    reference_plot_path: Path | None = None
    completion_reference_cycle_id: int | None = None
    completion_reference_cluster: int | None = None
    llm_interpretation_prompt: Path | None = None
    llm_interpretation_status: Path | None = None
    llm_interpretation_json: Path | None = None


def _archive_llm_run_to_saves(result: LlmRunResult) -> Path:
    session_dir = get_or_create_current_save_session(suffix="llm")
    devices_k_dir = get_session_subdir("devices_k", session_dir)

    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    run_dir = devices_k_dir / result.appliance_key / result.source_name / f"run_{stamp}_k{result.chosen_k}"
    run_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "appliance_key": result.appliance_key,
        "source_name": result.source_name,
        "chosen_k": int(result.chosen_k),
        "dominant_cluster": int(result.dominant_cluster),
        "dominant_cluster_n_cycles": int(result.dominant_cluster_n_cycles),
        "selected_cluster": int(result.selected_cluster),
        "selected_cycle_id": int(result.selected_cycle_id),
        "completion_reference_cycle_id": result.completion_reference_cycle_id,
        "completion_reference_cluster": result.completion_reference_cluster,
        "canonical_output_dir": str(result.output_dir),
        "cases_eval_csv": str(result.cases_eval_csv),
        "selected_case_csv": str(result.selected_case_csv),
        "llm_interpretation_prompt": str(result.llm_interpretation_prompt) if result.llm_interpretation_prompt else None,
        "llm_interpretation_status": str(result.llm_interpretation_status) if result.llm_interpretation_status else None,
        "llm_interpretation_json": str(result.llm_interpretation_json) if result.llm_interpretation_json else None,
        "archived_at": pd.Timestamp.now().isoformat(),
    }
    (run_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    if result.cases_eval_csv.exists():
        shutil.copy2(result.cases_eval_csv, run_dir / result.cases_eval_csv.name)
    if result.selected_case_csv.exists():
        shutil.copy2(result.selected_case_csv, run_dir / result.selected_case_csv.name)
    for extra_path in (
        result.completion_summary_json,
        result.completion_data_csv,
        result.completion_plot_path,
        result.reference_plot_path,
        result.llm_interpretation_prompt,
        result.llm_interpretation_status,
        result.llm_interpretation_json,
    ):
        if extra_path and extra_path.exists():
            shutil.copy2(extra_path, run_dir / extra_path.name)

    return run_dir


def _upsert_runtime_profile(
    appliance_key: str,
    source_name: str,
    output_dir: Path,
    chosen_k: int,
    dominant_cluster: int,
    dominant_cluster_n_cycles: int,
    selected_cluster: int,
    selected_cycle_id: int,
    completion_reference_cycle_id: int | None = None,
    completion_reference_cluster: int | None = None,
) -> Path:
    """Persist the latest LLM selection so runtime smart meters can replay it."""
    catalog_path = LLM_SM_DIR / "llm_smartmeter_profiles.json"
    try:
        output_dir_value = str(output_dir.relative_to(LLM_SM_DIR))
    except ValueError:
        output_dir_value = str(output_dir)

    try:
        pkl_path_value = str((output_dir / "llm_runtime_cycles.pkl").relative_to(LLM_SM_DIR))
    except ValueError:
        pkl_path_value = str(output_dir / "llm_runtime_cycles.pkl")

    payload = {
        "appliance_key": appliance_key,
        "source_name": source_name,
        "output_dir": output_dir_value,
        "chosen_k": int(chosen_k),
        "dominant_cluster": int(dominant_cluster),
        "dominant_cluster_n_cycles": int(dominant_cluster_n_cycles),
        "selected_cluster": int(selected_cluster),
        "selected_cycle_id": int(selected_cycle_id),
        "pkl_path": pkl_path_value,
        "updated_at": pd.Timestamp.now().isoformat(),
    }
    if completion_reference_cycle_id is not None:
        payload["completion_reference_cycle_id"] = int(completion_reference_cycle_id)
    if completion_reference_cluster is not None:
        payload["completion_reference_cluster"] = int(completion_reference_cluster)

    existing: dict = {}
    if catalog_path.exists():
        try:
            existing = json.loads(catalog_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except Exception:
            existing = {}

    existing[appliance_key] = payload
    by_source = existing.setdefault("by_source", {})
    if isinstance(by_source, dict):
        by_source[f"{appliance_key}:{source_name}"] = payload
    catalog_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog_path


def _load_module_from_file(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec for {module_name} from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_output_name_from_csv(csv_path: Path) -> str:
    stem = csv_path.stem.strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    cleaned = cleaned.strip("._-")
    return cleaned or "input"


def _normalize_profile_token(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return cleaned.strip("_")


def _device_type_to_appliance_key(device_type: str | None) -> str | None:
    mapping = {
        "computer": "computer",
        "coffee_machine": "coffee_machine",
        "dishwasher": "dishwasher",
        "refrigerator": "refrigerator",
        "fridge": "refrigerator",
        "washing_machine": "washing_machine",
    }
    return mapping.get(str(device_type or "").strip().lower())


def _device_key_from_source_name(source_name: str) -> str | None:
    source_token = _normalize_profile_token(source_name)
    if not source_token:
        return None

    candidates = []
    for device in read_devices:
        name = _normalize_profile_token(getattr(device, "name", ""))
        appliance_key = _device_type_to_appliance_key(getattr(device, "type", ""))
        if not name or not appliance_key:
            continue
        candidates.append((len(name), name, appliance_key))
        candidates.append((len(f"smartmeter_{name}"), f"smartmeter_{name}", appliance_key))

    for _length, token, appliance_key in sorted(candidates, reverse=True):
        padded_source = f"_{source_token}_"
        if source_token == token or source_token.startswith(f"{token}_") or f"_{token}_" in padded_source:
            return appliance_key
    return None


def _prepare_clean_output_dir(appliance_dir: Path, source_name: str) -> Path:
    output_dir = appliance_dir / f"{source_name}_output"
    resolved_appliance_dir = appliance_dir.resolve()
    resolved_output_dir = output_dir.resolve()
    if resolved_output_dir == resolved_appliance_dir or resolved_appliance_dir not in resolved_output_dir.parents:
        raise RuntimeError(f"Unsafe LLM output directory: {output_dir}")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _remove_unselected_k_results(output_dir: Path, chosen_k: int) -> None:
    keep_name = f"results_k{int(chosen_k)}"
    for candidate in output_dir.glob("results_k*"):
        if not candidate.is_dir() or candidate.name == keep_name:
            continue
        shutil.rmtree(candidate)


def _parse_optimal_k_report(report_path: Path) -> dict[str, int]:
    txt = report_path.read_text(encoding="utf-8")
    out: dict[str, int] = {}

    patterns = {
        "k_vote": r"Cluster suggerito \(voto\):\s*(\d+)",
        "k_human": r"Cluster human-friendly:\s*(\d+)",
        "k_accel": r"Elbow acceleration\s*->\s*k\s*=\s*(\d+)",
        "k_sil": r"Silhouette\s*->\s*k\s*=\s*(\d+)",
        "k_db": r"Davies-Bouldin\s*->\s*k\s*=\s*(\d+)",
        "k_ch": r"Calinski-Harabasz\s*->\s*k\s*=\s*(\d+)",
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, txt)
        if m:
            out[key] = int(m.group(1))

    return out


def _looks_like_csv_record_start(value: object) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", text):
        return True
    if "T" in text or ":" in text:
        return not pd.isna(pd.to_datetime(text, errors="coerce"))
    return False


def _split_concatenated_csv_row(header: list[str], row: list[str], line_no: int) -> list[list[str]]:
    expected_len = len(header)
    if len(row) == expected_len:
        return [row]
    if len(row) < expected_len:
        return [row + [""] * (expected_len - len(row))]

    starts = [0]
    for idx in range(1, len(row)):
        if _looks_like_csv_record_start(row[idx]):
            starts.append(idx)

    if len(starts) == 1:
        raise ValueError(
            f"CSV row {line_no} has {len(row)} fields, expected {expected_len}, "
            "and could not be repaired automatically."
        )

    starts.append(len(row))
    fixed_rows: list[list[str]] = []
    for start, end in zip(starts, starts[1:]):
        chunk = row[start:end]
        if len(chunk) < expected_len:
            chunk = chunk + [""] * (expected_len - len(chunk))
        elif len(chunk) > expected_len:
            extra = chunk[expected_len:]
            if any(str(value).strip() for value in extra):
                raise ValueError(
                    f"CSV row {line_no} contains a malformed record that could not be repaired automatically."
                )
            chunk = chunk[:expected_len]
        fixed_rows.append(chunk)
    return fixed_rows


def _read_csv_tolerating_concatenated_rows(path: Path) -> pd.DataFrame:
    with path.open("r", newline="", encoding="utf-8") as fp:
        reader = csv.reader(fp)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("CSV is empty.") from exc

        rows: list[list[str]] = []
        repaired_rows = 0
        for line_no, row in enumerate(reader, start=2):
            if not row or all(not str(value).strip() for value in row):
                continue
            if row == header:
                continue

            fixed = _split_concatenated_csv_row(header, row, line_no)
            if len(fixed) != 1 or len(row) != len(header):
                repaired_rows += 1
            rows.extend(fixed)

    if repaired_rows:
        logger.warning("Repaired %s malformed CSV row(s) while loading %s", repaired_rows, path)
    return pd.DataFrame(rows, columns=header)


def _load_time_value_csv(path: Path) -> pd.DataFrame:
    df = _read_csv_tolerating_concatenated_rows(path)

    candidates = [
        ("time", "value"),
        ("timestamp_iso", "power_W"),
        ("timestamp_sim", "value"),
        ("timestamp", "value"),
    ]

    time_col = None
    value_col = None
    for t_col, v_col in candidates:
        if t_col in df.columns and v_col in df.columns:
            time_col, value_col = t_col, v_col
            break

    if time_col is None or value_col is None:
        raise ValueError(
            "CSV must contain one valid pair of columns: "
            "(time,value), (timestamp_iso,power_W), (timestamp_sim,value), (timestamp,value)."
        )

    out = pd.DataFrame(
        {
            "time": pd.to_datetime(df[time_col], errors="coerce"),
            "value": pd.to_numeric(df[value_col], errors="coerce"),
        }
    )
    out = out.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)

    if out.empty:
        raise ValueError("CSV has no valid rows after time/value parsing.")

    return out


def _get_last_incomplete_day(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["day"] = d["time"].dt.date

    grouped = d.groupby("day", as_index=False).agg(last_time=("time", "max"), n=("time", "count"))
    grouped = grouped.sort_values("day", ascending=False).reset_index(drop=True)

    for _, row in grouped.iterrows():
        last_ts = pd.Timestamp(row["last_time"])
        # Conservative heuristic for "unfinished day".
        if last_ts.hour < 23:
            day = row["day"]
            return d[d["day"] == day].sort_values("time").reset_index(drop=True)

    # Fallback: latest day in file.
    day = grouped.iloc[0]["day"]
    return d[d["day"] == day].sort_values("time").reset_index(drop=True)


def _energy_kwh(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    t_sec = (df["time"] - df["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    p = df["value"].to_numpy(dtype=float)
    if len(t_sec) < 2:
        return 0.0
    dt_h = (t_sec[1:] - t_sec[:-1]) / 3600.0
    p_avg = (p[1:] + p[:-1]) / 2.0
    wh = float((p_avg * dt_h).sum())
    return wh / 1000.0


def _feature_vector(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return {
            "duration_minutes": 0.0,
            "max_power": 0.0,
            "mean_power": 0.0,
            "energy_kwh": 0.0,
            "time_of_peak_norm": 0.0,
        }

    start = pd.Timestamp(df["time"].iloc[0])
    end = pd.Timestamp(df["time"].iloc[-1])
    duration_minutes = max(0.0, (end - start).total_seconds() / 60.0)

    values = df["value"].to_numpy(dtype=float)
    max_power = float(values.max()) if len(values) else 0.0
    mean_power = float(values.mean()) if len(values) else 0.0
    energy_kwh = _energy_kwh(df)

    t_sec = (df["time"] - start).dt.total_seconds().to_numpy(dtype=float)
    if len(t_sec) > 0 and t_sec[-1] > 1e-9:
        peak_idx = int(values.argmax())
        peak_norm = float(t_sec[peak_idx] / t_sec[-1])
    else:
        peak_norm = 0.0

    return {
        "duration_minutes": duration_minutes,
        "max_power": max_power,
        "mean_power": mean_power,
        "energy_kwh": energy_kwh,
        "time_of_peak_norm": peak_norm,
    }

# for each case representative, compute distance from the last incomplete day features and sort by it.
def _compute_case_distances(rep_df: pd.DataFrame, partial_features: dict[str, float]) -> pd.DataFrame:

    cols = ["duration_minutes", "max_power", "mean_power", "energy_kwh", "time_of_peak_norm"]
    work = rep_df.copy()
    # Ensure all required columns exist; fill with NaN if missing
    for c in cols:
        if c not in work.columns:
            work[c] = float('nan')
        work[c] = pd.to_numeric(work[c], errors="coerce")

    std = work[cols].std(ddof=0).replace(0, 1).fillna(1)

    def row_distance(row: pd.Series) -> float:
        acc = 0.0
        used = 0
        for c in cols:
            v = row.get(c)
            if pd.isna(v):
                continue
            z = (float(v) - float(partial_features[c])) / float(std[c])
            acc += z * z
            used += 1
        return math.sqrt(acc / max(1, used))

    work["distance_to_last_incomplete_day"] = work.apply(row_distance, axis=1)
    work = work.sort_values("distance_to_last_incomplete_day", kind="mergesort").reset_index(drop=True)
    return work


def _positive_config_float(cfg: dict, key: str) -> float | None:
    try:
        value = float(cfg.get(key))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0.0:
        return None
    return value


def _filter_cases_for_selection(cases_df: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, bool]:
    selection_cfg = params.get("selection", {}) if isinstance(params, dict) else {}
    if not isinstance(selection_cfg, dict) or not selection_cfg:
        return cases_df.copy(), False

    work = cases_df.copy()
    applied = False
    for col in ("duration_minutes", "max_power", "mean_power", "energy_kwh"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")

    max_duration = _positive_config_float(selection_cfg, "max_duration_minutes")
    if max_duration is not None and "duration_minutes" in work.columns:
        work = work[work["duration_minutes"] <= max_duration]
        applied = True

    min_peak = _positive_config_float(selection_cfg, "min_peak_watts")
    if min_peak is not None and "max_power" in work.columns:
        work = work[work["max_power"] >= min_peak]
        applied = True

    min_mean = _positive_config_float(selection_cfg, "min_mean_watts")
    if min_mean is not None and "mean_power" in work.columns:
        work = work[work["mean_power"] >= min_mean]
        applied = True

    min_energy = _positive_config_float(selection_cfg, "min_energy_kwh")
    if min_energy is not None and "energy_kwh" in work.columns:
        work = work[work["energy_kwh"] >= min_energy]
        applied = True

    return work.reset_index(drop=True), applied


def _dominant_cluster_from_cases(cases_df: pd.DataFrame) -> tuple[int, int]:
    counts = (
        cases_df.groupby("cluster", as_index=False)
        .agg(n_cycles=("cycle_id", "count"))
        .sort_values(["n_cycles", "cluster"], ascending=[False, True], kind="mergesort")
        .reset_index(drop=True)
    )
    if counts.empty:
        raise RuntimeError("No valid clusters found for selected cases.")
    return int(counts.iloc[0]["cluster"]), int(counts.iloc[0]["n_cycles"])


def _extract_trailing_short_cycle(
    df: pd.DataFrame,
    *,
    threshold: float,
    min_duration_minutes: float,
    min_off_minutes: float,
    max_idle_gap_minutes: float | None,
) -> pd.DataFrame:
    work = df[["time", "value"]].copy()
    work = work.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
    if work.empty:
        return pd.DataFrame(columns=["time", "value"])

    values = work["value"].to_numpy(dtype=float)
    times = pd.to_datetime(work["time"]).to_numpy(dtype="datetime64[ns]")
    above = values > float(threshold)
    n = len(work)
    max_idle_gap_seconds = None
    if max_idle_gap_minutes is not None and float(max_idle_gap_minutes) > 0:
        max_idle_gap_seconds = float(max_idle_gap_minutes) * 60.0

    in_cycle = False
    start_idx = -1
    last_above_idx = -1
    off_start_idx = -1
    trailing_range: tuple[int, int] | None = None

    for i in range(n):
        if in_cycle and i > 0 and max_idle_gap_seconds is not None:
            gap_seconds = (pd.Timestamp(times[i]) - pd.Timestamp(times[i - 1])).total_seconds()
            if gap_seconds >= max_idle_gap_seconds:
                in_cycle = False
                start_idx = -1
                last_above_idx = -1
                off_start_idx = -1

        if above[i]:
            if not in_cycle:
                in_cycle = True
                start_idx = i
            last_above_idx = i
            off_start_idx = -1
            continue

        if not in_cycle:
            continue

        if off_start_idx < 0:
            off_start_idx = i

        off_minutes = (pd.Timestamp(times[i]) - pd.Timestamp(times[off_start_idx])).total_seconds() / 60.0
        if off_minutes >= float(min_off_minutes):
            in_cycle = False
            start_idx = -1
            last_above_idx = -1
            off_start_idx = -1

    if in_cycle and start_idx >= 0 and last_above_idx >= start_idx:
        start_time = pd.Timestamp(times[start_idx])
        end_time = pd.Timestamp(times[last_above_idx])
        duration_minutes = (end_time - start_time).total_seconds() / 60.0
        if duration_minutes < float(min_duration_minutes):
            trailing_range = (start_idx, last_above_idx)

    if trailing_range is None:
        return pd.DataFrame(columns=["time", "value"])

    start_idx, end_idx = trailing_range
    return work.iloc[start_idx : end_idx + 1].copy().reset_index(drop=True)


def _normalize_curve(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    mu = float(np.mean(arr))
    sigma = float(np.std(arr))
    if sigma < 1e-12:
        return arr - mu
    return (arr - mu) / sigma


def _resample_relative_curve(df: pd.DataFrame, target_len: int) -> np.ndarray:
    if len(df) < 2:
        return np.full(target_len, np.nan)
    t_sec = (pd.to_datetime(df["time"]) - pd.Timestamp(df["time"].iloc[0])).dt.total_seconds().to_numpy(dtype=float)
    y = pd.to_numeric(df["value"], errors="coerce").to_numpy(dtype=float)
    duration = float(t_sec[-1] - t_sec[0]) if len(t_sec) else 0.0
    if duration <= 0:
        return np.full(target_len, y[0] if len(y) else np.nan)
    xp = (t_sec - t_sec[0]) / duration
    target_x = np.linspace(0.0, 1.0, target_len)
    return np.interp(target_x, xp, y)


def _build_completed_cycle(
    partial_df: pd.DataFrame,
    reference_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    partial = partial_df.copy().reset_index(drop=True)
    reference = reference_df.copy().reset_index(drop=True)
    partial["time"] = pd.to_datetime(partial["time"])
    reference["time"] = pd.to_datetime(reference["time"])

    if partial.empty or reference.empty:
        return pd.DataFrame(columns=["time", "value"]), pd.DataFrame(columns=["time", "value"])

    partial_duration_seconds = max(
        0.0,
        (partial["time"].iloc[-1] - partial["time"].iloc[0]).total_seconds(),
    )
    ref_rel_seconds = (reference["time"] - reference["time"].iloc[0]).dt.total_seconds()
    suffix = reference.loc[ref_rel_seconds > partial_duration_seconds, ["time", "value"]].copy()
    if suffix.empty:
        return partial.copy(), pd.DataFrame(columns=["time", "value"])

    shifted_seconds = (pd.to_datetime(suffix["time"]) - reference["time"].iloc[0]).dt.total_seconds() - partial_duration_seconds
    suffix["time"] = partial["time"].iloc[-1] + pd.to_timedelta(shifted_seconds, unit="s")
    suffix = suffix[suffix["time"] > partial["time"].iloc[-1]].reset_index(drop=True)

    completed = pd.concat([partial[["time", "value"]], suffix[["time", "value"]]], ignore_index=True)
    return completed, suffix[["time", "value"]]


def _generate_completion_artifacts(
    *,
    partial_df: pd.DataFrame,
    matched_cycle: dict,
    matched_cluster_row: pd.Series,
    output_dir: Path,
    target_len: int,
) -> dict[str, object]:
    reference_df = pd.DataFrame(matched_cycle["data"]).copy()
    reference_df["time"] = pd.to_datetime(reference_df["time"])
    reference_df["value"] = pd.to_numeric(reference_df["value"], errors="coerce")
    reference_df = reference_df.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
    if len(partial_df) < 2 or len(reference_df) < 2:
        raise RuntimeError("Not enough data to generate completion charts.")

    completed_df, added_df = _build_completed_cycle(partial_df, reference_df)

    partial_duration_minutes = max(
        0.0,
        (partial_df["time"].iloc[-1] - partial_df["time"].iloc[0]).total_seconds() / 60.0,
    )
    partial_rel_min = (partial_df["time"] - partial_df["time"].iloc[0]).dt.total_seconds() / 60.0
    added_rel_min = (added_df["time"] - completed_df["time"].iloc[0]).dt.total_seconds() / 60.0 if not added_df.empty else []
    reference_rel_min = (reference_df["time"] - reference_df["time"].iloc[0]).dt.total_seconds() / 60.0

    completion_plot_path = output_dir / "completed_last_cluster_comparison.png"
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(partial_rel_min, partial_df["value"], color="black", linewidth=2.0, label="Initial partial data")
    if len(added_df) > 0:
        ax.plot(added_rel_min, added_df["value"], color="tab:blue", linewidth=2.0, linestyle="--", label="Generated completion")
    ax.axvline(partial_duration_minutes, color="0.5", linestyle="--", linewidth=1.2, label="Completion start")
    ax.set_title("Completed trailing short cycle")
    ax.set_xlabel("Minutes from cycle start")
    ax.set_ylabel("Power")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.savefig(completion_plot_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    reference_plot_path = output_dir / "completed_vs_reference_cycle.png"
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(reference_rel_min, reference_df["value"], color="tab:orange", linewidth=2.3, label="Matched historical cycle")
    ax.plot(partial_rel_min, partial_df["value"], color="black", linewidth=1.8, label="Initial partial data")
    if len(added_df) > 0:
        ax.plot(added_rel_min, added_df["value"], color="tab:blue", linewidth=2.0, linestyle="--", label="Generated completion")
    ax.axvline(partial_duration_minutes, color="0.5", linestyle="--", linewidth=1.2, label="Completion start")
    ax.set_title(
        "Reference cycle used for completion "
        f"(cycle_id={int(matched_cluster_row['cycle_id'])}, cluster={int(matched_cluster_row['cluster'])})"
    )
    ax.set_xlabel("Minutes from cycle start")
    ax.set_ylabel("Power")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.savefig(reference_plot_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    completion_data_csv = output_dir / "completed_last_cluster_data.csv"
    completed_export = completed_df.copy()
    completed_export["segment"] = "completed_cycle"
    partial_export = partial_df.copy()
    partial_export["segment"] = "initial_partial"
    added_export = added_df.copy()
    added_export["segment"] = "added_completion"
    reference_export = reference_df.copy()
    reference_export["segment"] = "matched_reference_cycle"
    export_df = pd.concat(
        [partial_export, added_export, completed_export, reference_export],
        ignore_index=True,
    )
    export_df.to_csv(completion_data_csv, index=False)

    prefix_len = max(25, min(int(target_len), len(partial_df) * 4))
    partial_curve = _normalize_curve(_resample_relative_curve(partial_df, prefix_len))
    ref_prefix = reference_df[
        (reference_df["time"] - reference_df["time"].iloc[0]).dt.total_seconds()
        <= partial_duration_minutes * 60.0
    ].copy()
    if len(ref_prefix) < 2:
        ref_prefix = reference_df.iloc[: min(len(reference_df), 2)].copy()
    ref_prefix_curve = _normalize_curve(_resample_relative_curve(ref_prefix, prefix_len))
    prefix_rmse = float(np.sqrt(np.nanmean((partial_curve - ref_prefix_curve) ** 2)))

    summary = {
        "matched_cycle_id": int(matched_cluster_row["cycle_id"]),
        "matched_cluster": int(matched_cluster_row["cluster"]),
        "matched_duration_minutes": float(matched_cluster_row["duration_minutes"]),
        "partial_duration_minutes": partial_duration_minutes,
        "completion_added_minutes": max(
            0.0,
            (completed_df["time"].iloc[-1] - partial_df["time"].iloc[-1]).total_seconds() / 60.0,
        ),
        "prefix_rmse": prefix_rmse,
        "completion_plot_path": str(completion_plot_path),
        "reference_plot_path": str(reference_plot_path),
        "completion_data_csv": str(completion_data_csv),
    }
    summary_path = output_dir / "completed_last_cluster_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "summary_path": summary_path,
        "completion_data_csv": completion_data_csv,
        "completion_plot_path": completion_plot_path,
        "reference_plot_path": reference_plot_path,
        "matched_cycle_id": int(matched_cluster_row["cycle_id"]),
        "matched_cluster": int(matched_cluster_row["cluster"]),
    }


def _complete_trailing_short_cycle(
    *,
    df: pd.DataFrame,
    params: dict,
    cycles_pkl_path: Path,
    clusters_df: pd.DataFrame,
    output_dir: Path,
) -> dict[str, object] | None:
    cycle_cfg = params.get("cycle", {})
    feature_cfg = params.get("features", {})
    partial_df = _extract_trailing_short_cycle(
        df,
        threshold=float(cycle_cfg.get("threshold_watts", 0.0)),
        min_duration_minutes=float(cycle_cfg.get("min_duration_minutes", 0.0)),
        min_off_minutes=float(cycle_cfg.get("min_off_minutes", 0.0)),
        max_idle_gap_minutes=cycle_cfg.get("max_idle_gap_minutes", 5.0),
    )
    if len(partial_df) < 2:
        return None

    with open(cycles_pkl_path, "rb") as fp:
        stored_cycles: list[dict] = pickle.load(fp)

    cycle_by_id = {int(cycle["cycle_id"]): cycle for cycle in stored_cycles if cycle.get("cycle_id") is not None}
    if not cycle_by_id:
        return None

    partial_duration_seconds = max(
        0.0,
        (partial_df["time"].iloc[-1] - partial_df["time"].iloc[0]).total_seconds(),
    )
    if partial_duration_seconds <= 0:
        return None

    target_len = max(40, int(feature_cfg.get("target_len", 200)))
    partial_curve = _normalize_curve(_resample_relative_curve(partial_df, target_len))
    if np.isnan(partial_curve).all():
        return None

    ranked_candidates: list[tuple[float, pd.Series]] = []
    for _, row in clusters_df.iterrows():
        try:
            cycle_id = int(row["cycle_id"])
        except Exception:
            continue
        cycle = cycle_by_id.get(cycle_id)
        if not cycle:
            continue

        reference_df = pd.DataFrame(cycle["data"]).copy()
        if len(reference_df) < 2:
            continue
        reference_df["time"] = pd.to_datetime(reference_df["time"])
        reference_df["value"] = pd.to_numeric(reference_df["value"], errors="coerce")
        reference_df = reference_df.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
        if len(reference_df) < 2:
            continue

        ref_rel_seconds = (reference_df["time"] - reference_df["time"].iloc[0]).dt.total_seconds()
        if float(ref_rel_seconds.iloc[-1]) <= partial_duration_seconds:
            continue

        ref_prefix = reference_df.loc[ref_rel_seconds <= partial_duration_seconds].copy()
        if len(ref_prefix) < 2:
            ref_prefix = reference_df.iloc[:2].copy()

        ref_curve = _normalize_curve(_resample_relative_curve(ref_prefix, target_len))
        if np.isnan(ref_curve).all():
            continue

        curve_rmse = float(np.sqrt(np.nanmean((partial_curve - ref_curve) ** 2)))
        partial_max = float(partial_df["value"].max())
        ref_max = float(ref_prefix["value"].max())
        partial_mean = float(partial_df["value"].mean())
        ref_mean = float(ref_prefix["value"].mean())
        power_penalty = abs(partial_max - ref_max) / max(1.0, partial_max, ref_max)
        mean_penalty = abs(partial_mean - ref_mean) / max(1.0, partial_mean, ref_mean)
        score = curve_rmse + 0.30 * power_penalty + 0.20 * mean_penalty
        ranked_candidates.append((score, row))

    if not ranked_candidates:
        return None

    ranked_candidates.sort(key=lambda item: item[0])
    _, matched_row = ranked_candidates[0]
    matched_cycle = cycle_by_id[int(matched_row["cycle_id"])]
    return _generate_completion_artifacts(
        partial_df=partial_df,
        matched_cycle=matched_cycle,
        matched_cluster_row=matched_row,
        output_dir=output_dir,
        target_len=target_len,
    )


def _run_llm_pipeline(
    appliance_label: str,
    csv_path: Path,
    k_mode: str,
    custom_k: int | None,
    custom_params_path: str | None,
    enable_completion: bool,
    progress_callback: Callable[[float, str], None] | None = None,
) -> LlmRunResult:
    def _progress(percent: float, message: str) -> None:
        if progress_callback is not None:
            progress_callback(max(0.0, min(100.0, float(percent))), message)

    _progress(1, "Loading LLM modules")
    common_dir = LLM_SM_DIR / "common"
    cycle_pipeline_mod = _load_module_from_file(
        "llm_smartmeter_cycle_pipeline", common_dir / "cycle_pipeline.py"
    )
    params_mod = _load_module_from_file(
        "llm_smartmeter_params", common_dir / "params.py"
    )
    run_cycle_pipeline = cycle_pipeline_mod.run_cycle_pipeline
    load_appliance_config = params_mod.load_appliance_config

    cfg = APPLIANCE_OPTIONS[appliance_label]
    appliance_key = cfg["key"]
    source_name = _safe_output_name_from_csv(csv_path)
    inferred_device_key = _device_key_from_source_name(source_name)
    if inferred_device_key is not None and inferred_device_key != appliance_key:
        raise RuntimeError(
            "Selected appliance does not match the CSV device name: "
            f"CSV appears to belong to '{inferred_device_key}', but '{appliance_key}' was selected."
        )
    appliance_dir = LLM_SM_DIR / cfg["folder"]

    _progress(4, "Loading CSV source")
    df = _load_time_value_csv(csv_path)

    _progress(6, "Loading parameters")
    params = copy.deepcopy(load_appliance_config(appliance_key, custom_path=custom_params_path))
    params.setdefault("cycle", {})["force_rebuild_pkl"] = True

    _progress(8, "Preparing clean output folder")
    output_dir = _prepare_clean_output_dir(appliance_dir, source_name)
    runtime_tsv = output_dir / "llm_runtime_input.tsv"
    df[["time", "value"]].to_csv(runtime_tsv, sep="\t", index=False)

    exact_k = custom_k if k_mode == "custom" else None

    def _pipeline_progress(percent: float, message: str) -> None:
        _progress(10 + 68 * (float(percent) / 100.0), message)

    run_cycle_pipeline(
        input_path=runtime_tsv,
        output_dir=output_dir,
        pkl_path=output_dir / "llm_runtime_cycles.pkl",
        chart_title_prefix=f"{appliance_key} - {source_name}",
        params=params,
        exact_k=exact_k,
        progress_callback=_pipeline_progress,
    )

    _progress(80, "Selecting best k")
    if k_mode == "custom":
        chosen_k = int(custom_k)
    else:
        picks = _parse_optimal_k_report(output_dir / "optimal_k_report.txt")
        if k_mode == "human":
            chosen_k = int(picks.get("k_human") or picks.get("k_vote") or picks.get("k_sil") or 2)
        else:
            chosen_k = int(picks.get("k_vote") or picks.get("k_human") or picks.get("k_sil") or 2)

    cases_dir = output_dir / f"results_k{chosen_k}"
    rep_path = cases_dir / "cluster_representatives.csv"
    clusters_path = cases_dir / "clusters.csv"
    summary_path = cases_dir / "cluster_summary.csv"
    _progress(82, f"Loading k={chosen_k} clustering results")
    if not rep_path.exists():
        raise FileNotFoundError(f"Cases file not found for chosen k: {rep_path}")
    if not clusters_path.exists():
        raise FileNotFoundError(f"Clusters file not found for chosen k: {clusters_path}")
    if not summary_path.exists():
        raise FileNotFoundError(f"Cluster summary file not found for chosen k: {summary_path}")

    rep_df = pd.read_csv(rep_path)
    if rep_df.empty:
        raise RuntimeError("No generated cases found in cluster_representatives.csv.")

    clusters_df = pd.read_csv(clusters_path)
    if clusters_df.empty:
        raise RuntimeError("No generated cycles found in clusters.csv.")

    summary_df = pd.read_csv(summary_path)
    if summary_df.empty or "cluster" not in summary_df.columns or "n_cycles" not in summary_df.columns:
        raise RuntimeError("Invalid or empty cluster_summary.csv for chosen k.")

    summary_df["cluster"] = pd.to_numeric(summary_df["cluster"], errors="coerce")
    summary_df["n_cycles"] = pd.to_numeric(summary_df["n_cycles"], errors="coerce")
    summary_df = summary_df.dropna(subset=["cluster", "n_cycles"])
    if summary_df.empty:
        raise RuntimeError("cluster_summary.csv has no valid cluster sizes.")

    summary_df = summary_df.sort_values(["n_cycles", "cluster"], ascending=[False, True], kind="mergesort")
    selectable_cases_df, selection_policy_active = _filter_cases_for_selection(clusters_df, params)
    if selection_policy_active and not selectable_cases_df.empty:
        dominant_cluster, dominant_cluster_n_cycles = _dominant_cluster_from_cases(selectable_cases_df)
        cases_for_selection = selectable_cases_df
    else:
        if selection_policy_active and selectable_cases_df.empty:
            logger.warning(
                "[LLM SmartMeter] selection policy for %s produced no candidates; using all cases",
                appliance_key,
            )
        dominant_cluster = int(summary_df.iloc[0]["cluster"])
        dominant_cluster_n_cycles = int(summary_df.iloc[0]["n_cycles"])
        cases_for_selection = clusters_df

    _progress(85, "Matching latest partial day against cases")
    partial_df = _get_last_incomplete_day(df)
    partial_features = _feature_vector(partial_df)

    ranked = _compute_case_distances(cases_for_selection, partial_features)
    ranked.insert(0, "chosen_k", chosen_k)
    ranked.insert(1, "dominant_cluster", dominant_cluster)
    ranked.insert(2, "dominant_cluster_n_cycles", dominant_cluster_n_cycles)
    for feat_name, feat_value in partial_features.items():
        ranked[f"last_day_{feat_name}"] = float(feat_value)

    cases_eval_csv = output_dir / f"cases_evaluation_k{chosen_k}.csv"
    ranked.to_csv(cases_eval_csv, index=False)

    dominant_ranked = ranked[ranked["cluster"] == dominant_cluster].copy().reset_index(drop=True)
    if not dominant_ranked.empty:
        feat_cols = ["duration_minutes", "max_power", "mean_power", "energy_kwh", "time_of_peak_norm"]
        for c in feat_cols:
            dominant_ranked[c] = pd.to_numeric(dominant_ranked[c], errors="coerce")

        representative_best = pd.DataFrame()
        if selection_policy_active:
            reps = rep_df.copy()
            reps["cluster"] = pd.to_numeric(reps.get("cluster"), errors="coerce")
            reps["cycle_id"] = pd.to_numeric(reps.get("cycle_id"), errors="coerce")
            representative_ids = set(
                reps.loc[reps["cluster"] == dominant_cluster, "cycle_id"]
                .dropna()
                .astype(int)
                .tolist()
            )
            if representative_ids:
                representative_best = dominant_ranked[
                    dominant_ranked["cycle_id"].astype(int).isin(representative_ids)
                ].copy()

        if not representative_best.empty:
            best = representative_best.iloc[[0]].copy()
            best["distance_to_cluster_center"] = 0.0
            best["selection_score"] = best["distance_to_last_incomplete_day"]
        else:
            center = dominant_ranked[feat_cols].mean(skipna=True)
            std = dominant_ranked[feat_cols].std(ddof=0).replace(0, 1).fillna(1)

            def _center_dist(row: pd.Series) -> float:
                acc = 0.0
                used = 0
                for c in feat_cols:
                    v = row.get(c)
                    if pd.isna(v) or pd.isna(center[c]):
                        continue
                    z = (float(v) - float(center[c])) / float(std[c])
                    acc += z * z
                    used += 1
                return math.sqrt(acc / max(1, used))

            dominant_ranked["distance_to_cluster_center"] = dominant_ranked.apply(_center_dist, axis=1)
            if selection_policy_active:
                dominant_ranked["selection_score"] = dominant_ranked["distance_to_cluster_center"]
            else:
                dominant_ranked["selection_score"] = (
                    dominant_ranked["distance_to_last_incomplete_day"]
                    + 0.35 * dominant_ranked["distance_to_cluster_center"]
                )
            dominant_ranked = dominant_ranked.sort_values(
                ["selection_score", "distance_to_last_incomplete_day", "cycle_id"],
                ascending=[True, True, True],
                kind="mergesort",
            ).reset_index(drop=True)
            best = dominant_ranked.iloc[[0]].copy()
    else:
        best = ranked.iloc[[0]].copy()
    selected_case_csv = output_dir / "selected_case_latest_incomplete_day.csv"
    best.to_csv(selected_case_csv, index=False)

    selected_cluster = int(best.iloc[0]["cluster"])
    selected_cycle_id = int(best.iloc[0]["cycle_id"])

    completion_info = None
    if enable_completion:
        _progress(90, "Completing trailing short cycle")
        completion_info = _complete_trailing_short_cycle(
            df=df,
            params=params,
            cycles_pkl_path=output_dir / "llm_runtime_cycles.pkl",
            clusters_df=clusters_df,
            output_dir=output_dir,
        )

    _progress(93, "Cleaning unselected k results")
    _remove_unselected_k_results(output_dir, chosen_k)

    llm_prompt_path = output_dir / "llm_interpretation_prompt.json"
    llm_status_path = output_dir / "llm_interpretation_status.json"
    llm_output_path = output_dir / "llm_interpretation.json"
    try:
        _progress(94, "Preparing LLM interpretation prompt")
        llm_interpreter_mod = _load_module_from_file(
            "llm_smartmeter_interpreter", common_dir / "llm_interpreter.py"
        )
        generate_llm_interpretation = llm_interpreter_mod.generate_llm_interpretation
        llm_status = generate_llm_interpretation(
            appliance_label=appliance_label,
            appliance_key=appliance_key,
            output_dir=output_dir,
            results_dir=cases_dir,
            chosen_k=chosen_k,
            selected_cluster=selected_cluster,
            dominant_cluster=dominant_cluster,
            selected_cycle_id=selected_cycle_id,
            selected_case_csv=selected_case_csv,
            completion_summary_json=completion_info["summary_path"] if completion_info else None,
        )
        logger.info("[LLM SmartMeter] interpretation status for %s: %s", appliance_key, llm_status.get("status"))
    except Exception as exc:
        logger.warning("[LLM SmartMeter] unable to prepare LLM interpretation: %s", exc, exc_info=True)

    _progress(95, "Updating runtime LLM profile")
    catalog_path = _upsert_runtime_profile(
        appliance_key=appliance_key,
        source_name=source_name,
        output_dir=output_dir,
        chosen_k=chosen_k,
        dominant_cluster=dominant_cluster,
        dominant_cluster_n_cycles=dominant_cluster_n_cycles,
        selected_cluster=selected_cluster,
        selected_cycle_id=selected_cycle_id,
        completion_reference_cycle_id=completion_info["matched_cycle_id"] if completion_info else None,
        completion_reference_cluster=completion_info["matched_cluster"] if completion_info else None,
    )
    logger.info(
        "[LLM SmartMeter] profile updated for %s (k=%s, dominant_cluster=%s, selected_cluster=%s, cycle_id=%s) -> %s",
        appliance_key,
        chosen_k,
        dominant_cluster,
        selected_cluster,
        selected_cycle_id,
        catalog_path,
    )
    _progress(98, "Finalizing LLM run")

    return LlmRunResult(
        chosen_k=chosen_k,
        appliance_key=appliance_key,
        source_name=source_name,
        selected_cycle_id=selected_cycle_id,
        dominant_cluster=dominant_cluster,
        dominant_cluster_n_cycles=dominant_cluster_n_cycles,
        selected_cluster=selected_cluster,
        output_dir=output_dir,
        cases_eval_csv=cases_eval_csv,
        selected_case_csv=selected_case_csv,
        completion_summary_json=completion_info["summary_path"] if completion_info else None,
        completion_data_csv=completion_info["completion_data_csv"] if completion_info else None,
        completion_plot_path=completion_info["completion_plot_path"] if completion_info else None,
        reference_plot_path=completion_info["reference_plot_path"] if completion_info else None,
        completion_reference_cycle_id=completion_info["matched_cycle_id"] if completion_info else None,
        completion_reference_cluster=completion_info["matched_cluster"] if completion_info else None,
        llm_interpretation_prompt=llm_prompt_path if llm_prompt_path.exists() else None,
        llm_interpretation_status=llm_status_path if llm_status_path.exists() else None,
        llm_interpretation_json=llm_output_path if llm_output_path.exists() else None,
    )


def _show_completion_chart_preview(
    parent: tk.Misc,
    title: str,
    completion_data_csv: Path,
) -> None:
    if not completion_data_csv.exists():
        messagebox.showerror("LLM Smart Meter", f"Completion data not found:\n{completion_data_csv}")
        return

    try:
        data = pd.read_csv(completion_data_csv)
    except Exception as exc:
        messagebox.showerror("LLM Smart Meter", f"Unable to load completion data:\n{exc}")
        return

    required_cols = {"time", "value", "segment"}
    if not required_cols.issubset(data.columns):
        messagebox.showerror("LLM Smart Meter", "Completion data CSV must contain time, value, and segment columns.")
        return

    data = data.copy()
    data["time"] = pd.to_datetime(data["time"], errors="coerce")
    data["value"] = pd.to_numeric(data["value"], errors="coerce")
    data = data.dropna(subset=["time", "value", "segment"]).sort_values("time")
    if data.empty:
        messagebox.showerror("LLM Smart Meter", "Completion data CSV has no valid rows.")
        return

    def _segment(name: str) -> pd.DataFrame:
        return data[data["segment"] == name].copy().sort_values("time")

    def _relative_minutes(df: pd.DataFrame) -> np.ndarray:
        if df.empty:
            return np.array([])
        return ((df["time"] - df["time"].iloc[0]).dt.total_seconds() / 60.0).to_numpy(dtype=float)

    partial_df = _segment("initial_partial")
    added_df = _segment("added_completion")
    reference_df = _segment("matched_reference_cycle")

    preview = tk.Toplevel(parent)
    preview.title(title)
    preview.geometry("1180x720")
    preview.configure(bg="#202020")

    fig, ax = plt.subplots(figsize=(12, 6))
    partial_artists = []
    if not reference_df.empty:
        ax.plot(
            _relative_minutes(reference_df),
            reference_df["value"],
            color="tab:orange",
            linewidth=2.0,
            label="Matched historical cycle",
        )
    if not added_df.empty:
        base_df = partial_df if not partial_df.empty else added_df
        ax.plot(
            ((added_df["time"] - base_df["time"].iloc[0]).dt.total_seconds() / 60.0).to_numpy(dtype=float),
            added_df["value"],
            color="tab:blue",
            linewidth=1.6,
            linestyle="--",
            label="Generated completion",
        )
    if not partial_df.empty:
        partial_x = _relative_minutes(partial_df)
        partial_line, = ax.plot(partial_x, partial_df["value"], color="black", linewidth=1.7, label="Initial partial data")
        partial_artists.append(partial_line)
        if len(partial_x) > 0:
            completion_start_line = ax.axvline(
                float(partial_x[-1]),
                color="0.55",
                linestyle="--",
                linewidth=1.2,
                label="Completion start",
            )
            partial_artists.append(completion_start_line)
    ax.set_title("Generated completion compared with matched historical cycle")
    ax.set_xlabel("Minutes from cycle start")
    ax.set_ylabel("Power")
    ax.grid(True, linestyle=":", alpha=0.7)

    def _refresh_legend() -> None:
        handles, labels = ax.get_legend_handles_labels()
        visible = [(handle, label) for handle, label in zip(handles, labels) if handle.get_visible()]
        if visible:
            next_handles, next_labels = zip(*visible)
            ax.legend(next_handles, next_labels)
        else:
            legend = ax.get_legend()
            if legend:
                legend.remove()

    _refresh_legend()
    fig.tight_layout()
    canvas_plot = FigureCanvasTkAgg(fig, master=preview)
    toolbar = NavigationToolbar2Tk(canvas_plot, preview)
    toolbar.update()
    toolbar.pack(side=tk.TOP, fill=tk.X)

    partial_visible = tk.BooleanVar(value=True)

    def _toggle_partial_cycle() -> None:
        next_visible = not partial_visible.get()
        partial_visible.set(next_visible)
        for artist in partial_artists:
            artist.set_visible(next_visible)
        toggle_partial_btn.config(text="Hide partial cycle" if next_visible else "Show partial cycle")
        _refresh_legend()
        canvas_plot.draw_idle()

    if partial_artists:
        toggle_partial_btn = tk.Button(toolbar, text="Hide partial cycle", command=_toggle_partial_cycle)
        toggle_partial_btn.pack(side=tk.LEFT, padx=(8, 2))

    _force_dark_tk_theme(toolbar)
    canvas_plot.draw()
    canvas_plot.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
    plt.close(fig)


def _force_dark_tk_theme(widget: tk.Misc) -> None:
    dark_bg = "#202020"
    panel_bg = "#2b2b2b"
    text_fg = "#f2f2f2"
    entry_bg = "#111111"
    active_bg = "#3a3a3a"
    klass = widget.winfo_class()
    is_button = klass == "Button"
    bg = entry_bg if klass in {"Entry", "TEntry"} else dark_bg
    if klass in {"Checkbutton", "Radiobutton", "Labelframe"}:
        bg = panel_bg
    fg = "#111111" if is_button else text_fg
    active_fg = "#111111" if is_button else text_fg
    try:
        widget.configure(bg=bg)
    except tk.TclError:
        pass
    try:
        widget.configure(fg=fg)
    except tk.TclError:
        pass
    try:
        widget.configure(activebackground=active_bg, activeforeground=active_fg)
    except tk.TclError:
        pass
    try:
        widget.configure(disabledforeground="#777777")
    except tk.TclError:
        pass
    try:
        widget.configure(readonlybackground=entry_bg, insertbackground=text_fg)
    except tk.TclError:
        pass
    try:
        widget.configure(selectcolor=panel_bg)
    except tk.TclError:
        pass
    try:
        widget.configure(highlightbackground=dark_bg, highlightcolor=active_bg)
    except tk.TclError:
        pass

    for child in widget.winfo_children():
        _force_dark_tk_theme(child)


def _show_info_message(parent: tk.Misc, message: str) -> None:
    messagebox.showinfo("LLM Smart Meter", message, parent=parent)


def open_llm_smartmeter_ui(ctx: AppContext):
    win = tk.Toplevel(ctx.window)
    win.title("LLM Smart Meter")
    win.geometry("920x620")
    win.configure(bg="#202020")

    frm = tk.Frame(win, bg="#202020")
    frm.pack(fill="both", expand=True, padx=12, pady=12)

    tk.Label(frm, text="Appliance").grid(row=0, column=0, sticky="w")
    appliance_var = tk.StringVar(value="Computer")
    appliance_combo = ttk.Combobox(
        frm,
        textvariable=appliance_var,
        values=list(APPLIANCE_OPTIONS.keys()),
        state="readonly",
        width=30,
    )
    appliance_combo.grid(row=0, column=1, sticky="w", padx=(8, 0))

    tk.Label(frm, text="CSV source").grid(row=1, column=0, sticky="w", pady=(10, 0))
    csv_var = tk.StringVar()
    csv_entry = tk.Entry(frm, textvariable=csv_var, width=48)
    csv_entry.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(10, 0))

    def _browse_csv():
        path = ask_open_file(
            title="Select CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            csv_var.set(path)

    tk.Button(frm, text="Browse", command=_browse_csv).grid(row=1, column=2, sticky="w", padx=(8, 0), pady=(10, 0))

    tk.Label(frm, text="Parameters").grid(row=2, column=0, sticky="w", pady=(12, 0))
    params_mode_var = tk.StringVar(value="default")

    params_wrap = tk.Frame(frm)
    params_wrap.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(12, 0))
    tk.Radiobutton(params_wrap, text="Use default parameters", value="default", variable=params_mode_var).pack(anchor="w")
    tk.Radiobutton(params_wrap, text="Use custom JSON parameters", value="custom", variable=params_mode_var).pack(anchor="w")

    params_path_var = tk.StringVar()
    params_entry = tk.Entry(
        frm,
        textvariable=params_path_var,
        width=48,
        state="readonly",
        readonlybackground="#111111",
        fg="#f2f2f2",
        bg="#111111",
        insertbackground="#f2f2f2",
    )
    params_entry.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

    def _browse_params_json():
        if params_mode_var.get() != "custom":
            _show_info_message(win, "Enable 'Use custom JSON parameters' to choose a JSON file.")
            return
        path = ask_open_file(
            title="Select parameters JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            params_path_var.set(path)

    params_browse_btn = tk.Button(frm, text="Browse", command=_browse_params_json)
    params_browse_btn.grid(row=3, column=2, sticky="w", padx=(8, 0), pady=(6, 0))

    tk.Label(frm, text="K mode").grid(row=4, column=0, sticky="w", pady=(14, 0))
    k_mode_var = tk.StringVar(value="best")

    rb_wrap = tk.Frame(frm)
    rb_wrap.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(14, 0))

    tk.Radiobutton(rb_wrap, text="Best k (vote)", value="best", variable=k_mode_var).pack(anchor="w")
    tk.Radiobutton(rb_wrap, text="Human k", value="human", variable=k_mode_var).pack(anchor="w")
    tk.Radiobutton(rb_wrap, text="Custom k", value="custom", variable=k_mode_var).pack(anchor="w")

    custom_k_var = tk.StringVar(value="20")
    custom_k_entry = tk.Entry(
        frm,
        textvariable=custom_k_var,
        width=8,
        state="readonly",
        readonlybackground="#111111",
        fg="#f2f2f2",
        bg="#111111",
        insertbackground="#f2f2f2",
    )
    custom_k_entry.grid(row=4, column=2, sticky="w", padx=(8, 0), pady=(14, 0))

    completion_var = tk.BooleanVar(value=True)
    completion_frame = tk.LabelFrame(frm, text="Trailing Cluster Completion")
    completion_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(16, 0))
    completion_frame.grid_columnconfigure(0, weight=1)
    completion_check = tk.Checkbutton(
        completion_frame,
        text="Complete the last short cycle using the closest historical case",
        variable=completion_var,
    )
    completion_check.grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
    tk.Label(
        completion_frame,
        text=(
            "If the CSV ends with a cycle shorter than the minimum duration, "
            "the initial shape is matched against historical cycles and the missing tail is generated."
        ),
        justify="left",
        wraplength=760,
    ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

    def _on_params_mode_change(*_args):
        custom_mode = params_mode_var.get() == "custom"
        params_entry.config(state="normal" if custom_mode else "readonly")

    params_mode_var.trace_add("write", _on_params_mode_change)

    def _on_k_mode_change(*_args):
        if k_mode_var.get() == "custom":
            custom_k_entry.config(state="normal")
        else:
            custom_k_entry.config(state="readonly")

    k_mode_var.trace_add("write", _on_k_mode_change)

    status_var = tk.StringVar(value="Ready")
    tk.Label(frm, textvariable=status_var, fg="blue").grid(row=6, column=0, columnspan=3, sticky="w", pady=(16, 0))

    progress_var = tk.DoubleVar(value=0.0)
    progress_bar = ttk.Progressbar(
        frm,
        mode="determinate",
        maximum=100,
        variable=progress_var,
        length=420,
    )
    progress_bar.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(6, 0))
    progress_bar.grid_remove()

    result_frame = tk.LabelFrame(frm, text="Completion Results")
    result_frame.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(14, 0))
    result_frame.grid_columnconfigure(0, weight=1)
    completion_status_var = tk.StringVar(value="No completion generated yet.")
    tk.Label(
        result_frame,
        textvariable=completion_status_var,
        justify="left",
        wraplength=760,
    ).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 8))

    comparison_plot_btn = tk.Button(
        result_frame,
        text="Show comparison chart",
        command=lambda: _show_info_message(win, "No comparison chart available yet."),
    )
    comparison_plot_btn.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

    btn_row = tk.Frame(frm)
    btn_row.grid(row=9, column=0, columnspan=3, sticky="e", pady=(20, 0))

    start_btn = tk.Button(btn_row, text="Start", width=12)
    start_btn.pack(side="right")

    def _set_running(running: bool):
        state = "disabled" if running else "normal"
        start_btn.config(state=state)
        appliance_combo.config(state="readonly")
        csv_entry.config(state="normal")
        completion_check.config(state="normal")
        custom_k_entry.config(state="normal" if (not running and k_mode_var.get() == "custom") else "readonly")
        params_entry.config(state="normal" if (not running and params_mode_var.get() == "custom") else "readonly")
        params_browse_btn.config(state="normal")
        if running:
            progress_var.set(0.0)
            progress_bar.grid()
        else:
            progress_bar.grid_remove()

    def _update_progress(percent: float, message: str) -> None:
        clean_percent = max(0.0, min(100.0, float(percent)))
        progress_var.set(clean_percent)
        status_var.set(f"{clean_percent:3.0f}% - {message}")

    def _run_worker(
        appliance_label: str,
        csv_source: str,
        k_mode: str,
        custom_k: int | None,
        custom_params_path: str | None,
        enable_completion: bool,
    ):
        def _thread_progress(percent: float, message: str) -> None:
            win.after(0, lambda: _update_progress(percent, message))

        try:
            result = _run_llm_pipeline(
                appliance_label=appliance_label,
                csv_path=Path(csv_source),
                k_mode=k_mode,
                custom_k=custom_k,
                custom_params_path=custom_params_path,
                enable_completion=enable_completion,
                progress_callback=_thread_progress,
            )
            _thread_progress(99, "Archiving LLM run")
            archive_dir = _archive_llm_run_to_saves(result)

            def _done_ok():
                _update_progress(100, "Done")
                _set_running(False)
                status_var.set("Done")
                if result.completion_plot_path and result.reference_plot_path:
                    completion_status_var.set(
                        "Trailing short cycle completed.\n"
                        f"Matched historical cycle: {result.completion_reference_cycle_id} "
                        f"(cluster {result.completion_reference_cluster})."
                    )
                    comparison_plot_btn.config(
                        command=lambda: _show_completion_chart_preview(
                            win,
                            "Completed vs reference cycle",
                            result.completion_data_csv,
                        ),
                    )
                else:
                    completion_status_var.set(
                        "No trailing short cycle was completed. "
                        "Either the CSV does not end with a short cycle or no compatible historical case was found."
                    )
                    comparison_plot_btn.config(
                        command=lambda: _show_info_message(win, "No comparison chart available for this run."),
                    )
                completion_lines = ""
                if result.completion_plot_path and result.reference_plot_path:
                    completion_lines = (
                        f"Completion matched cycle: {result.completion_reference_cycle_id} "
                        f"(cluster {result.completion_reference_cluster})\n"
                        f"Completion chart: {result.completion_plot_path}\n"
                        f"Reference chart: {result.reference_plot_path}\n"
                    )
                messagebox.showinfo(
                    "LLM Smart Meter",
                    "Completed.\n"
                    f"Appliance: {result.appliance_key}\n"
                    f"Source: {result.source_name}\n"
                    f"Chosen k: {result.chosen_k}\n"
                    f"Dominant cluster: {result.dominant_cluster} (n={result.dominant_cluster_n_cycles})\n"
                    f"Selected cluster: {result.selected_cluster}\n\n"
                    f"Selected cycle id: {result.selected_cycle_id}\n\n"
                    f"Cases CSV: {result.cases_eval_csv}\n"
                    f"Selected case CSV: {result.selected_case_csv}\n"
                    f"{completion_lines}"
                    f"Saved devices_k run: {archive_dir}",
                )

            win.after(0, _done_ok)
        except Exception as e:
            logger.exception("LLM Smart Meter failed")
            err_msg = str(e)

            def _done_err():
                _set_running(False)
                status_var.set("Error")
                messagebox.showerror("LLM Smart Meter", f"Execution failed:\n{err_msg}")

            win.after(0, _done_err)

    def _start():
        appliance_label = appliance_var.get().strip()
        csv_source = csv_var.get().strip()
        k_mode = k_mode_var.get().strip()
        params_mode = params_mode_var.get().strip()

        if appliance_label not in APPLIANCE_OPTIONS:
            messagebox.showerror("LLM Smart Meter", "Select a valid appliance.")
            return

        if not csv_source:
            messagebox.showerror("LLM Smart Meter", "Select a CSV file.")
            return

        if not Path(csv_source).is_file():
            messagebox.showerror("LLM Smart Meter", "Selected CSV file does not exist.")
            return

        custom_params_path = None
        if params_mode == "custom":
            custom_params_path = params_path_var.get().strip()
            if not custom_params_path:
                messagebox.showerror("LLM Smart Meter", "Select a custom parameters JSON file.")
                return
            if not Path(custom_params_path).is_file():
                messagebox.showerror("LLM Smart Meter", "Selected parameters JSON file does not exist.")
                return

        custom_k = None
        if k_mode == "custom":
            raw = custom_k_var.get().strip()
            try:
                custom_k = int(raw)
            except Exception:
                messagebox.showerror("LLM Smart Meter", "Custom k must be an integer.")
                return
            if custom_k < 2:
                messagebox.showerror("LLM Smart Meter", "Custom k must be >= 2.")
                return

        _set_running(True)
        _update_progress(0, "Starting LLM run")
        completion_status_var.set("Running completion analysis...")
        comparison_plot_btn.config(
            command=lambda: _show_info_message(win, "Wait for the current run to finish.")
        )
        th = threading.Thread(
            target=_run_worker,
            args=(appliance_label, csv_source, k_mode, custom_k, custom_params_path, bool(completion_var.get())),
            daemon=True,
        )
        th.start()

    start_btn.config(command=_start)
    _force_dark_tk_theme(win)
