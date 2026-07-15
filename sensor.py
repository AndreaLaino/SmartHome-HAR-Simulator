import tkinter as tk
from tkinter import simpledialog, messagebox
from tkinter import ttk
from utils import draw_sensor, calculate_distance, update_temperature_sensor_color
from device import devices
from datetime import datetime, timedelta
from consumption_profiles import get_device_consumption, consumption_profiles
from read import read_sensors as sensors_file
from read import read_devices as devices_file
import os, json
import math
import pickle
import re
from pathlib import Path
from bisect import bisect_right
from app.hardware.real_sensors import load_temp_by_gpio_any_csv, load_temp_by_label_any_csv
import numpy as np
import pandas as pd
from collections import deque
from typing import Optional
from models import Sensor, Device
from house_state import HouseState
from canvas_zoom import event_to_logical

_BASE_DIR = Path(__file__).resolve().parent
SENSOR_MAP_PATH = str(_BASE_DIR / "sensor_map.json")
CSV_DEVICE_EFFECT_SCALE = 0.25
CSV_SMOOTHING_TIME_CONSTANT_MIN = 3.0
DEVICE_EFFECT_TIME_CONSTANT_MIN = 30.0
DEVICE_HEAT_UP_TIME_CONSTANT_MIN = 10.0
DEVICE_HEAT_DOWN_TIME_CONSTANT_MIN = 30.0

TEMP_RECENT: dict[str, deque] = {}
TEMP_GREEN_UNTIL: dict[str, float] = {}
TEMP_BASELINE: dict[str, float] = {}
TEMP_CSV_BASELINE: dict[str, float] = {}
TEMP_DEVICE_OFFSET: dict[str, float] = {}
TEMP_DEVICE_HEAT: dict[str, float] = {}

# LLM runtime state (cleared on each reset, not persisted)
LLM_PROFILE_CATALOG_PATH = _BASE_DIR / "LLM" / "smartmeter" / "llm_smartmeter_profiles.json"
LLM_PROFILE_CATALOG_LEGACY_PATH = _BASE_DIR / "llm_smartmeter_profiles.json"
LLM_PROFILE_CATALOG_CACHE: Optional[dict] = None
LLM_PROFILE_CATALOG_MTIME: Optional[float] = None
LLM_PROFILE_CATALOG_ACTIVE_PATH: Optional[Path] = None
LLM_CYCLE_CURVE_CACHE: dict[tuple[str, str, int], Optional[tuple[list[float], list[float]]]] = {}
LLM_SENSOR_ON_START: dict[str, datetime] = {}
LLM_CLUSTER_CASES_CACHE: dict[tuple[str, str, int], Optional[pd.DataFrame]] = {}
LLM_SENSOR_ACTIVE_CYCLE_ID: dict[str, int] = {}
LLM_SENSOR_ACTIVE_COMPLETION: set[str] = set()
LLM_SENSOR_ACTIVE_COMPLETION_CURVE: dict[str, tuple[list[float], list[float]]] = {}
LLM_SENSOR_ACTIVE_PREDICTION: set[str] = set()
LLM_SENSOR_COMPLETED_CYCLES: set[str] = set()
LLM_WM_DAY_CURSOR: dict[str, dict] = {}
LLM_PROFILE_CYCLE_CURSOR: dict[str, dict] = {}
LLM_SENSOR_USED_CASES: dict[str, list[dict[str, int]]] = {}
LLM_SENSOR_GENERATION_EVENTS: dict[str, list[dict[str, object]]] = {}
LLM_SMARTMETER_MODE = "simulation"

LLM_AUTO_STOP_APPLIANCES = frozenset({"washing_machine", "refrigerator"})
LLM_CONTINUOUS_APPLIANCES = frozenset({"computer"})

sensors = []
add_point_enabled = False

# cache: per sensor -> (datetimes_list, values_list_C)
TEMP_SERIES: dict[str, Optional[tuple[list[datetime], list[float]]]] = {}
TEMP_SERIES_SIGNATURE: dict[str, tuple] = {}
# simulated time (in delta_seconds units) per sensor
TEMP_SIM_MIN: dict[str, float] = {}


def reset_llm_runtime_state() -> None:
    LLM_SENSOR_ON_START.clear()
    LLM_SENSOR_ACTIVE_CYCLE_ID.clear()
    LLM_SENSOR_ACTIVE_COMPLETION.clear()
    LLM_SENSOR_ACTIVE_COMPLETION_CURVE.clear()
    LLM_SENSOR_ACTIVE_PREDICTION.clear()
    LLM_SENSOR_COMPLETED_CYCLES.clear()
    LLM_WM_DAY_CURSOR.clear()
    LLM_PROFILE_CYCLE_CURSOR.clear()
    LLM_SENSOR_USED_CASES.clear()
    LLM_SENSOR_GENERATION_EVENTS.clear()


def _clear_active_llm_cycle(sensor_name: str) -> None:
    LLM_SENSOR_ON_START.pop(sensor_name, None)
    LLM_SENSOR_ACTIVE_CYCLE_ID.pop(sensor_name, None)
    LLM_SENSOR_ACTIVE_COMPLETION.discard(sensor_name)
    LLM_SENSOR_ACTIVE_COMPLETION_CURVE.pop(sensor_name, None)
    LLM_SENSOR_ACTIVE_PREDICTION.discard(sensor_name)


def reset_temperature_runtime_state(*, clear_series_cache: bool = True) -> None:
    """Reset temperature state between independent simulation runs."""
    TEMP_RECENT.clear()
    TEMP_GREEN_UNTIL.clear()
    TEMP_BASELINE.clear()
    TEMP_CSV_BASELINE.clear()
    TEMP_DEVICE_OFFSET.clear()
    TEMP_DEVICE_HEAT.clear()
    TEMP_SIM_MIN.clear()
    if clear_series_cache:
        TEMP_SERIES.clear()
        TEMP_SERIES_SIGNATURE.clear()


def set_llm_smartmeter_mode(mode: str | None) -> None:
    global LLM_SMARTMETER_MODE
    LLM_SMARTMETER_MODE = "realtime_dt" if mode == "realtime_dt" else "simulation"


def get_llm_smartmeter_mode() -> str:
    return LLM_SMARTMETER_MODE


def _bound_smartmeter_ip(sensor_name: str) -> Optional[str]:
    mapping = _load_sensor_map()
    cfg = mapping.get(sensor_name)
    if not isinstance(cfg, dict) or cfg.get("by") != "ip":
        return None
    ip = str(cfg.get("value") or "").strip()
    return ip or None


def is_smartmeter_bound_to_real_device(sensor_name: str) -> bool:
    return _bound_smartmeter_ip(sensor_name) is not None


def _should_use_realtime_dt(sensor_name: str) -> bool:
    return LLM_SMARTMETER_MODE == "realtime_dt" and is_smartmeter_bound_to_real_device(sensor_name)


class TemperatureSensorAdapter:
    """Pure adapter: translate HouseState runtime into compute_temperature inputs."""

    def update(
        self,
        state: HouseState,
        sensor: Sensor,
        *,
        heating_factor: float | None = None,
        delta_seconds: float | None = None,
        current_datetime=None,
        active_devices=None,
        render: bool = True,
    ):
        runtime = state.runtime_view(
            heating_factor=heating_factor,
            delta_seconds=delta_seconds,
            current_datetime=current_datetime,
            devices=active_devices,
        )
        heating_factor = float(runtime.get("heating_factor", 0.0) or 0.0)
        delta_seconds = float(runtime.get("delta_seconds", 1.0) or 1.0)
        current_datetime = runtime.get("current_datetime")
        active_devices = runtime.get("devices")

        new_state = compute_temperature(
            sensor,
            heating_factor,
            delta_seconds,
            current_datetime,
            active_devices,
        )
        return sensor.name, new_state


class PIRSensorAdapter:
    """Pure adapter for PIR state transitions."""

    def update(self, state: HouseState, sensor: Sensor, new_state=None, *, render: bool = True):
        current_state = float(sensor.state)
        resolved_state = (
            1.0 if current_state == 0.0 else 0.0
        ) if new_state is None else float(new_state)
        return sensor.name, resolved_state


class SmartMeterSensorAdapter:
    """Pure adapter: translate HouseState runtime into Smart Meter inputs."""

    def update(
        self,
        state: HouseState,
        sensor: Sensor,
        *,
        devices_list=None,
        delta_seconds: float | None = None,
        current_datetime=None,
        render: bool = True,
    ):
        runtime = state.runtime_view(
            devices=devices_list,
            delta_seconds=delta_seconds,
            current_datetime=current_datetime,
        )
        devices_list = runtime.get("devices")
        if devices_list is None:
            raise RuntimeError("SmartMeterSensorAdapter requires runtime['devices'] or an explicit devices_list")
        delta_seconds = float(runtime.get("delta_seconds", 1.0) or 1.0)
        current_datetime = runtime.get("current_datetime")
        cycles_store = state.active_cycles()

        consumption = compute_smartmeter_consumption(
            sensor,
            devices_list,
            delta_seconds,
            current_datetime,
            cycles_store,
        )
        return sensor.name, consumption


def is_temperature_sensor_changing(sensor_name: str) -> bool:
    sim_min = float(TEMP_SIM_MIN.get(sensor_name, 0.0))
    green_until = float(TEMP_GREEN_UNTIL.get(sensor_name, 0.0))
    return sim_min <= green_until


class WeightSensorAdapter:
    """Pure adapter for Weight sensor transitions."""

    def update(self, state: HouseState, sensor: Sensor, new_state, *, render: bool = True):
        return sensor.name, float(new_state)


class SwitchSensorAdapter:
    """Pure adapter for Switch sensor transitions."""

    def update(self, state: HouseState, sensor: Sensor, door_state, *, render: bool = True):
        return sensor.name, _normalize_switch_state(door_state)

def _load_sensor_map(path: str = SENSOR_MAP_PATH) -> dict:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[WARN] cannot load {path}: {e}")
    return {}


def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in (name or "").strip())


def _temperature_source_signature(sensor_name: str) -> tuple:
    """Return a cheap signature that changes when the relevant DHT files change."""
    devices_dir = Path(__file__).resolve().parent / "devices"
    label_path = devices_dir / f"dht_{_sanitize(sensor_name)}.csv"
    candidates = [label_path] if label_path.is_file() else sorted(devices_dir.glob("dht_*.csv"))

    signature = []
    for path in candidates:
        try:
            stat = path.stat()
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            continue
    return tuple(signature)

def _load_temp_series_for_sensor(sensor_name: str):
    """
    Load the DHT temperature series for this sensor.
    Uses load_temp_by_label_any_csv(sensor_name), the same mechanism
    used for the "real" graphs.

    Returns:
        (times, values)
    where:
        times  = source datetimes
        values = temperature in C (float)
    or None if no data is available.
    """
    if not sensor_name:
        TEMP_SERIES[sensor_name] = None
        return None

    source_signature = _temperature_source_signature(sensor_name)

    # Reuse the cache only while the source CSV files are unchanged.
    if sensor_name in TEMP_SERIES and TEMP_SERIES_SIGNATURE.get(sensor_name) == source_signature:
        return TEMP_SERIES[sensor_name]

    # 1) try by label
    df = None
    try:
        df = load_temp_by_label_any_csv(sensor_name)
    except Exception as e:
        print(f"[TEMP] load_temp_by_label_any_csv failed for {sensor_name}: {e}")

    # 2) fallback to GPIO
    if (df is None or df.empty or "value" not in df.columns):
        mapping = _load_sensor_map()
        cfg = mapping.get(sensor_name, {})
        if isinstance(cfg, dict) and cfg.get("by") == "dht":
            gpio = cfg.get("gpio")
            if gpio is not None:
                try:
                    df = load_temp_by_gpio_any_csv(int(gpio))
                except Exception as e:
                    print(f"[TEMP] load_temp_by_gpio_any_csv failed for {sensor_name}: {e}")

    if df is None or df.empty or "value" not in df.columns:
        print(f"[TEMP] no series found for {sensor_name}")
        TEMP_SERIES[sensor_name] = None
        TEMP_SERIES_SIGNATURE[sensor_name] = source_signature
        return None

    # keep only valid values (drop unreadable entries)
    df = df.dropna(subset=["value"])
    if df.empty:
        print(f"[TEMP] only NaN values for {sensor_name}")
        TEMP_SERIES[sensor_name] = None
        TEMP_SERIES_SIGNATURE[sensor_name] = source_signature
        return None

    # ensure time ordering
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df.index = pd.to_datetime(df.index, errors="coerce")
        except Exception as e:
            print(f"[TEMP] cannot convert index to datetime for {sensor_name}: {e}")
    df = df.sort_index()

    if df.empty:
        TEMP_SERIES[sensor_name] = None
        TEMP_SERIES_SIGNATURE[sensor_name] = source_signature
        return None

    # Store actual datetime objects (aligned to real timestamps, not relative minutes)
    datetimes = df.index.to_list()
    values = df["value"].astype(float).to_list()

    TEMP_SERIES[sensor_name] = (datetimes, values)
    TEMP_SERIES_SIGNATURE[sensor_name] = source_signature
    print(
        f"[TEMP] {sensor_name}: {len(datetimes)} samples "
        f"from {datetimes[0]} to {datetimes[-1]}"
    )
    return TEMP_SERIES[sensor_name]

def _interpolate_reference_day(datetimes, values, indices, target_minute: float) -> float:
    """Interpolate one daily profile, including a smooth overnight bridge."""
    points = [
        (datetimes[index].hour * 60 + datetimes[index].minute + datetimes[index].second / 60.0, float(values[index]))
        for index in indices
    ]
    points.sort(key=lambda item: item[0])
    if len(points) == 1:
        return points[0][1]

    minutes = [point[0] for point in points]
    temperatures = [point[1] for point in points]
    first_minute, last_minute = minutes[0], minutes[-1]

    if first_minute <= target_minute <= last_minute:
        right = bisect_right(minutes, target_minute)
        if right == 0:
            return temperatures[0]
        if right >= len(points):
            return temperatures[-1]
        left = right - 1
        span = minutes[right] - minutes[left]
        if span <= 0:
            return temperatures[left]
        ratio = (target_minute - minutes[left]) / span
        return temperatures[left] + ratio * (temperatures[right] - temperatures[left])

    # Join the last sample to the first sample of the repeated next day.
    overnight_span = (first_minute + 24 * 60) - last_minute
    if overnight_span <= 0:
        return temperatures[-1]
    elapsed = target_minute - last_minute
    if target_minute < first_minute:
        elapsed += 24 * 60
    ratio = max(0.0, min(1.0, elapsed / overnight_span))
    return temperatures[-1] + ratio * (temperatures[0] - temperatures[-1])


def get_replay_temperature(sensor_name: str, current_datetime: Optional[datetime] = None):
    """
    Temperature replay based on historical data, aligned to real time.

    Logic:
    - use data from the requested date when available;
    - otherwise use the closest previous available date;
    - interpolate by time of day and bridge uncovered overnight hours smoothly.
    
    Args:
        sensor_name: name of the sensor
        current_datetime: current datetime to look up (uses date + time of day)
    
    Returns:
        Temperature value or None if no data is available
    """
    # Handle None or missing datetime -> fallback to model-only
    if current_datetime is None:
        return None
    
    series = _load_temp_series_for_sensor(sensor_name)
    if not series:
        return None

    datetimes, values = series
    if not datetimes or not values:
        return None

    # Convert all to timezone-naive for comparison
    current_dt = current_datetime
    if current_dt.tzinfo is not None:
        current_dt = current_dt.replace(tzinfo=None)

    target_date = current_dt.date()
    available_dates = sorted({timestamp.date() for timestamp in datetimes})
    if target_date in available_dates:
        reference_date = target_date
    else:
        previous_dates = [day for day in available_dates if day <= target_date]
        reference_date = previous_dates[-1] if previous_dates else available_dates[0]

    reference_indices = [index for index, timestamp in enumerate(datetimes) if timestamp.date() == reference_date]
    target_minute = current_dt.hour * 60 + current_dt.minute + current_dt.second / 60.0
    result = _interpolate_reference_day(datetimes, values, reference_indices, target_minute)
    return max(15.0, min(40.0, float(result)))


def _normalize_switch_state(door_state) -> float:
    try:
        return float(door_state)
    except (TypeError, ValueError):
        if isinstance(door_state, str):
            lowered = door_state.lower()
            if lowered == "open":
                return 1.0
            if lowered == "close":
                return 0.0
    return 0.0

def get_sensor_params(sensor_type):
    params = {
        "PIR": {"min": 0.0, "max": 1.0, "step": 1.0, "state": 0.0, "direction": 0, "consumption": None},
        "Temperature": {"min": 18.0, "max": 50.0, "step": 0.5, "state": 18.0, "direction": None, "consumption": None},
        "Switch": {"min": 0, "max": 1, "step": 1, "state": 0, "direction": None, "consumption": None},
        "Smart Meter": {"min": 0.0, "max": 5000.0, "step": 10.0, "state": 0.0, "direction": None, "consumption": 0.0},
        "Weight": {"min": 0.0, "max": 1.0, "step": 1.0, "state": 0.0, "direction": None, "consumption": None},
    }
    return params.get(
        sensor_type,
        {"min": 0.0, "max": 1.0, "step": 1.0, "state": 0.0, "direction": None, "consumption": None},
    )


def _last_slope_deg_per_min(series) -> float:
    """Return the final slope in C/min (last difference)."""
    if not series:
        return 0.0
    times, vals = series
    if not times or not vals or len(times) < 2:
        return 0.0
    t2, t1 = float(times[-1]), float(times[-2])
    v2, v1 = float(vals[-1]), float(vals[-2])
    dt = (t2 - t1)
    if dt <= 0:
        return 0.0
    return (v2 - v1) / dt


def add_sensor(canvas, event, load_active, on_changed=None):
    global add_point_enabled
    if add_point_enabled:
        return

    logical_x, logical_y = event_to_logical(canvas, event)
    x = int(logical_x)
    y = int(logical_y)

    # Build device candidates from both data structures and what is currently drawn.
    device_names = set()
    for dev in devices or []:
        if hasattr(dev, "name") and dev.name:
            device_names.add(str(dev.name).strip())
    for dev in devices_file or []:
        if hasattr(dev, "name") and dev.name:
            device_names.add(str(dev.name).strip())

    try:
        for item_id in canvas.find_withtag("device"):
            tags = canvas.gettags(item_id)
            if tags:
                # First tag is the device name in draw_device().
                name_tag = str(tags[0]).strip()
                if name_tag and name_tag != "device":
                    device_names.add(name_tag)
    except Exception:
        pass

    dialog = SensorDialog(canvas.master, "Add sensor", device_names=sorted(n for n in device_names if n))
    if dialog.result:
        name, type, min_val, max_val, step, state, direction, consumption, associated_device = dialog.result
        sensor = Sensor(
            name=name,
            x=x,
            y=y,
            type=type,
            min_val=float(min_val),
            max_val=float(max_val),
            step=float(step),
            state=float(state),
            direction=direction,
            consumption=consumption,
            associated_device=associated_device,
        )

        # write to the right list according to load_active
        if load_active:
            sensors_file.append(sensor)
        else:
            sensors.append(sensor)

        draw_sensor(canvas, sensor)
        if callable(on_changed):
            on_changed()


def get_last_real_temperature(sensor_name: str, window_minutes: int = 10):
    if not sensor_name:
        return None

    mapping = _load_sensor_map()
    cfg = mapping.get(sensor_name, {})

    df = None

    # 1) if bound to DHT via GPIO, try GPIO
    if isinstance(cfg, dict) and cfg.get("by") == "dht":
        gpio = cfg.get("gpio")
        if gpio is not None:
            try:
                df = load_temp_by_gpio_any_csv(int(gpio))
            except Exception as e:
                print(f"[WARN] load_temp_by_gpio_any_csv failed for {sensor_name}: {e}")

    # 2) otherwise try by label
    if df is None or df.empty:
        try:
            df = load_temp_by_label_any_csv(sensor_name)
        except Exception as e:
            print(f"[WARN] load_temp_by_label_any_csv failed for {sensor_name}: {e}")
            df = None

    if df is None or df.empty or "value" not in df.columns:
        return None

    # keep only valid numeric values
    df_valid = df.dropna(subset=["value"])
    if df_valid.empty:
        return None

    # latest value (i.e., at that minute)
    try:
        latest = float(df_valid["value"].iloc[-1])
        return latest
    except Exception:
        return None
    
      
def infer_room_state(sensor_name: str, window_minutes: int = 20) -> str:
    if not sensor_name:
        return "unknown"

    mapping = _load_sensor_map()
    cfg = mapping.get(sensor_name, {})

    df = None
    if isinstance(cfg, dict) and cfg.get("by") == "dht":
        gpio = cfg.get("gpio")
        if gpio is not None:
            df = load_temp_by_gpio_any_csv(int(gpio))

    if df is None or df.empty:
        df = load_temp_by_label_any_csv(sensor_name)

    if df is None or df.empty or "value" not in df.columns:
        return "unknown"

    tail = df.tail(window_minutes)
    if len(tail) < 2:
        return "unknown"

    t0 = float(tail["value"].iloc[0])
    t1 = float(tail["value"].iloc[-1])
    delta = t1 - t0
    slope = delta / max(1, len(tail) - 1)  # °C for minute

    if slope > 0.15 and t1 >= 26:
        return "cooking"      
    elif slope > 0.05:
        return "heating"     
    elif slope < -0.05:
        return "cooling"    
    else:
        return "stable"


def compute_temperature(sensor, heating_factor, delta_seconds, current_datetime=None, active_devices=None):
    """Compute next Temperature sensor state with no UI side effects."""
    name, x, y = sensor.name, sensor.x, sensor.y
    max_val = float(sensor.max_val)
    current_state = float(sensor.state)

    # 1 real second = 1 simulated minute
    prev_sim_min = float(TEMP_SIM_MIN.get(name, 0.0))
    delta_sim_min = float(delta_seconds or 0.0)

    # Clamp delta to reasonable range (0.1 to 120 minutes per step)
    delta_sim_min = max(0.1, min(120.0, delta_sim_min))

    sim_min = prev_sim_min + delta_sim_min
    TEMP_SIM_MIN[name] = sim_min

    # Keep a recent buffer
    recent = TEMP_RECENT.get(name)
    if recent is None:
        recent = deque(maxlen=30)  # last 30 minutes
        TEMP_RECENT[name] = recent
    recent.append(float(current_state))

    # Preload CSV target (if any) so we can decide whether to apply daily cycle.
    csv_target = get_replay_temperature(name, current_datetime)

    # Without a CSV, keep the scenario's initial temperature as the baseline.
    if csv_target is None:
        if name not in TEMP_BASELINE:
            TEMP_BASELINE[name] = float(current_state)
        base_temp = TEMP_BASELINE[name]
    else:
        base_temp = float(csv_target)

    # Device heat contribution
    device_heat = 0.0
    heat_by_type = {
        "Oven": 3.5,
        "Computer": 0.6,
        "Washing_Machine": 1.2,
        "Coffee_Machine": 1.0,
        "Dishwasher": 1.2,
        "Fridge": 0.3,
    }
    radius_by_type = {
        "Oven": 220.0,
        "Computer": 140.0,
        "Washing_Machine": 160.0,
        "Coffee_Machine": 120.0,
        "Dishwasher": 160.0,
        "Fridge": 120.0,
    }
    default_heat = 0.8
    default_radius = 140.0
    active_oven_counted = False

    if active_devices:
        for dev in active_devices:
            if not isinstance(dev, Device):
                continue

            if dev.state == 1:
                dist = ((float(dev.x) - float(x)) ** 2 + (float(dev.y) - float(y)) ** 2) ** 0.5
                max_heat = heat_by_type.get(dev.type, default_heat)
                radius = radius_by_type.get(dev.type, default_radius)
                if dist < radius:
                    device_heat += max_heat * (1.0 - dist / radius)
                    if dev.type == "Oven":
                        active_oven_counted = True

    # The legacy heating factor describes the same nearby oven already present
    # in active_devices. Keep it only as a fallback to avoid counting the oven twice.
    external_heat = 0.0 if active_oven_counted else float(heating_factor or 0.0) * 0.5
    model_heat = device_heat + external_heat

    device_alpha = 1.0 - math.exp(-delta_sim_min / DEVICE_EFFECT_TIME_CONSTANT_MIN)

    if csv_target is not None:
        TEMP_DEVICE_HEAT.pop(name, None)
        # Start from the CSV value immediately, then smooth only changes between
        # consecutive targets. This avoids both an artificial initial ramp from
        # the scenario default and visible steps from quantized DHT samples.
        previous_csv = float(TEMP_CSV_BASELINE.get(name, csv_target))
        csv_alpha = 1.0 - math.exp(-delta_sim_min / CSV_SMOOTHING_TIME_CONSTANT_MIN)
        smoothed_csv = previous_csv + csv_alpha * (float(csv_target) - previous_csv)
        TEMP_CSV_BASELINE[name] = smoothed_csv

        # Keep the simulated device contribution independent from the measured
        # baseline and let it grow or decay gradually.
        previous_offset = float(TEMP_DEVICE_OFFSET.get(name, 0.0))
        target_offset = CSV_DEVICE_EFFECT_SCALE * model_heat
        device_offset = previous_offset + device_alpha * (target_offset - previous_offset)
        TEMP_DEVICE_OFFSET[name] = device_offset
        new_state = smoothed_csv + device_offset
    else:
        TEMP_CSV_BASELINE.pop(name, None)
        previous_heat = float(TEMP_DEVICE_HEAT.get(name, 0.0))
        heat_time_constant = (
            DEVICE_HEAT_UP_TIME_CONSTANT_MIN
            if model_heat > previous_heat
            else DEVICE_HEAT_DOWN_TIME_CONSTANT_MIN
        )
        heat_alpha = 1.0 - math.exp(-delta_sim_min / heat_time_constant)
        effective_heat = previous_heat + heat_alpha * (model_heat - previous_heat)
        TEMP_DEVICE_HEAT[name] = effective_heat

        # Keep an unrounded offset so small heating/cooling steps are not lost
        # when the displayed sensor state is rounded to two decimal places. The
        # room follows the device's residual heat instead of its binary state.
        previous_offset = float(TEMP_DEVICE_OFFSET.get(name, current_state - base_temp))
        device_offset = previous_offset + device_alpha * (effective_heat - previous_offset)
        TEMP_DEVICE_OFFSET[name] = device_offset
        new_state = base_temp + device_offset

    effective_min = 0.0
    effective_max = max_val
    if csv_target is not None:
        try:
            effective_min = min(effective_min, float(csv_target))
            effective_max = max(effective_max, float(csv_target))
        except Exception:
            pass
    new_state = max(effective_min, min(effective_max, new_state))
    new_state = round(new_state, 2)

    recent.append(float(new_state))

    if abs(new_state - current_state) > 0.0:
        TEMP_GREEN_UNTIL[name] = sim_min + 5.0

    return new_state

def _device_type_to_appliance_key(dev_type: Optional[str]) -> Optional[str]:
    mapping = {
        "computer": "computer",
        "coffee_machine": "coffee_machine",
        "dishwasher": "dishwasher",
        "fridge": "refrigerator",
        "refrigerator": "refrigerator",
        "washing_machine": "washing_machine",
    }
    if not dev_type:
        return None
    return mapping.get(str(dev_type).strip().lower())


def _llm_catalog_path_variants(path: Path) -> list[Path]:
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


def _resolve_llm_catalog_path(value: str | Path | None) -> Path:
    if value is None:
        return Path()

    raw = str(value).replace("\\", "/")
    path = Path(raw)
    candidates = [path] if path.is_absolute() else [
        LLM_PROFILE_CATALOG_PATH.parent / path,
        LLM_PROFILE_CATALOG_LEGACY_PATH.parent / path,
    ]
    for candidate in candidates:
        for variant in _llm_catalog_path_variants(candidate):
            if variant.exists():
                return variant

    return candidates[0]


def _load_llm_profile_catalog() -> dict:
    global LLM_PROFILE_CATALOG_CACHE, LLM_PROFILE_CATALOG_MTIME, LLM_PROFILE_CATALOG_ACTIVE_PATH
    try:
        catalog_path = LLM_PROFILE_CATALOG_PATH
        if not catalog_path.exists() and LLM_PROFILE_CATALOG_LEGACY_PATH.exists():
            catalog_path = LLM_PROFILE_CATALOG_LEGACY_PATH

        if not catalog_path.exists():
            LLM_PROFILE_CATALOG_CACHE = {}
            LLM_PROFILE_CATALOG_MTIME = None
            LLM_PROFILE_CATALOG_ACTIVE_PATH = None
            return {}

        mtime = float(catalog_path.stat().st_mtime)
        if (
            LLM_PROFILE_CATALOG_CACHE is not None
            and LLM_PROFILE_CATALOG_MTIME == mtime
            and LLM_PROFILE_CATALOG_ACTIVE_PATH == catalog_path
        ):
            return LLM_PROFILE_CATALOG_CACHE

        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        LLM_CYCLE_CURVE_CACHE.clear()
        LLM_CLUSTER_CASES_CACHE.clear()
        LLM_PROFILE_CATALOG_CACHE = data
        LLM_PROFILE_CATALOG_MTIME = mtime
        LLM_PROFILE_CATALOG_ACTIVE_PATH = catalog_path
        return data
    except Exception:
        return {}


def _normalize_llm_profile_token(value: str | None) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return cleaned.strip("_")


def _profile_source_token(profile: dict) -> str:
    source_name = str(profile.get("source_name") or "").strip()
    if source_name:
        return _normalize_llm_profile_token(source_name)
    output_dir = str(profile.get("output_dir") or "").strip()
    if output_dir:
        name = Path(output_dir).name
        if name.endswith("_output"):
            name = name[: -len("_output")]
        return _normalize_llm_profile_token(name)
    return ""


def _profile_candidates_for_sensor(sensor_name: str, associated_device: Optional[str]) -> set[str]:
    raw_values = {
        sensor_name,
        associated_device or "",
        f"smartmeter_{sensor_name}",
        f"smartmeter_{associated_device}" if associated_device else "",
    }
    candidates = {
        _normalize_llm_profile_token(value)
        for value in raw_values
        if _normalize_llm_profile_token(value)
    }

    # The scenario uses ``fr`` for the device and ``sm_fr`` for its meter,
    # while the historical dataset/profile uses ``re`` (refrigerator).
    # Accept both naming conventions when selecting the runtime profile.
    aliases = {
        "fr": {"fr", "re", "fridge", "refrigerator"},
        "re": {"fr", "re", "fridge", "refrigerator"},
        "fridge": {"fr", "re", "fridge", "refrigerator"},
        "refrigerator": {"fr", "re", "fridge", "refrigerator"},
    }
    for value in (sensor_name, associated_device):
        token = _normalize_llm_profile_token(value)
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


def _profile_match_score(profile: dict, candidates: set[str]) -> int:
    source_token = _profile_source_token(profile)
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


def _profile_for_sensor(
    catalog: dict,
    appliance_key: str,
    sensor_name: str,
    associated_device: Optional[str],
) -> Optional[dict]:
    candidates = _profile_candidates_for_sensor(sensor_name, associated_device)
    by_source = catalog.get("by_source")
    appliance_profiles: list[dict] = []
    if isinstance(by_source, dict):
        for profile in by_source.values():
            if isinstance(profile, dict) and str(profile.get("appliance_key") or "") == appliance_key:
                appliance_profiles.append(profile)

    scored = [
        (_profile_match_score(profile, candidates), profile)
        for profile in appliance_profiles
    ]
    scored = [(score, profile) for score, profile in scored if score > 0]
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    profile = catalog.get(appliance_key)
    if not isinstance(profile, dict):
        return None
    if _profile_source_token(profile) and _profile_match_score(profile, candidates) <= 0:
        return None
    return profile


def _load_llm_cycle_curve(profile: dict) -> Optional[tuple[list[float], list[float]]]:
    """Load selected cycle (minutes, power_W) from LLM runtime pkl by cycle_id."""
    return _load_llm_cycle_curve_for_cycle_id(profile)


def _load_llm_cycle_curve_for_cycle_id(
    profile: dict,
    cycle_id_override: Optional[int] = None,
) -> Optional[tuple[list[float], list[float]]]:
    """Load selected cycle (minutes, power_W) from LLM runtime pkl by cycle_id."""
    try:
        appliance_key = str(profile.get("appliance_key") or "").strip()
        cycle_id = int(profile.get("selected_cycle_id") if cycle_id_override is None else cycle_id_override)
        pkl_path = _resolve_llm_catalog_path(str(profile.get("pkl_path") or "").strip())
    except Exception:
        return None

    cache_key = (appliance_key, str(pkl_path), cycle_id)
    if cache_key in LLM_CYCLE_CURVE_CACHE:
        return LLM_CYCLE_CURVE_CACHE[cache_key]

    if not pkl_path.is_file():
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    try:
        with open(pkl_path, "rb") as fp:
            cycles = pickle.load(fp)
    except Exception:
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    if not isinstance(cycles, list):
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    selected = None
    for c in cycles:
        try:
            if int(c.get("cycle_id")) == cycle_id:
                selected = c
                break
        except Exception:
            continue

    if not selected:
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    df = pd.DataFrame(selected.get("data") or [])
    if df.empty or "time" not in df.columns or "value" not in df.columns:
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
    if len(df) < 2:
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    t_min = (df["time"] - df["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float) / 60.0
    vals = df["value"].to_numpy(dtype=float)
    if len(t_min) < 2 or float(t_min[-1]) <= 0.0:
        LLM_CYCLE_CURVE_CACHE[cache_key] = None
        return None

    out = (t_min.tolist(), vals.tolist())
    LLM_CYCLE_CURVE_CACHE[cache_key] = out
    return out


def _load_llm_cluster_cases(profile: dict) -> Optional[pd.DataFrame]:
    try:
        appliance_key = str(profile.get("appliance_key") or "").strip()
        chosen_k = int(profile.get("chosen_k"))
        output_dir = _resolve_llm_catalog_path(str(profile.get("output_dir") or "").strip())
    except Exception:
        return None

    cache_key = (appliance_key, str(output_dir), chosen_k)
    if cache_key in LLM_CLUSTER_CASES_CACHE:
        return LLM_CLUSTER_CASES_CACHE[cache_key]

    clusters_path = output_dir / f"results_k{chosen_k}" / "clusters.csv"
    if not clusters_path.is_file():
        LLM_CLUSTER_CASES_CACHE[cache_key] = None
        return None

    try:
        df = pd.read_csv(clusters_path)
    except Exception:
        LLM_CLUSTER_CASES_CACHE[cache_key] = None
        return None

    required_cols = {"cycle_id", "start_time", "end_time", "cluster"}
    if df.empty or not required_cols.issubset(df.columns):
        LLM_CLUSTER_CASES_CACHE[cache_key] = None
        return None

    work = df.copy()
    work["cycle_id"] = pd.to_numeric(work["cycle_id"], errors="coerce")
    work["cluster"] = pd.to_numeric(work["cluster"], errors="coerce")
    work["start_time"] = pd.to_datetime(work["start_time"], errors="coerce")
    work["end_time"] = pd.to_datetime(work["end_time"], errors="coerce")
    work = work.dropna(subset=["cycle_id", "cluster", "start_time", "end_time"]).reset_index(drop=True)
    if work.empty:
        LLM_CLUSTER_CASES_CACHE[cache_key] = None
        return None

    work["cycle_id"] = work["cycle_id"].astype(int)
    work["cluster"] = work["cluster"].astype(int)
    work["weekday"] = work["start_time"].dt.weekday
    LLM_CLUSTER_CASES_CACHE[cache_key] = work
    return work


def _completion_summary_for_profile(profile: dict) -> dict:
    summary = {
        "matched_cycle_id": profile.get("completion_reference_cycle_id"),
        "matched_cluster": profile.get("completion_reference_cluster"),
    }
    try:
        output_dir = _resolve_llm_catalog_path(str(profile.get("output_dir") or "").strip())
        summary_path = output_dir / "completed_last_cluster_summary.json"
        if summary_path.is_file():
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                summary.update({k: v for k, v in loaded.items() if v is not None})
    except Exception:
        pass
    return summary


def _load_llm_completion_curve(profile: dict) -> Optional[tuple[list[float], list[float]]]:
    try:
        output_dir = _resolve_llm_catalog_path(str(profile.get("output_dir") or "").strip())
        data_path = output_dir / "completed_last_cluster_data.csv"
    except Exception:
        return None
    if not data_path.is_file():
        return None

    try:
        df = pd.read_csv(data_path)
    except Exception:
        return None
    if df.empty or not {"time", "value", "segment"}.issubset(df.columns):
        return None

    segments = set(df["segment"].dropna().astype(str))
    if "initial_partial" not in segments or "added_completion" not in segments:
        return None

    completed = df[df["segment"] == "completed_cycle"].copy()
    if completed.empty:
        completed = df[df["segment"].isin(["initial_partial", "added_completion"])].copy()
    if completed.empty:
        return None

    completed["time"] = pd.to_datetime(completed["time"], errors="coerce")
    completed["value"] = pd.to_numeric(completed["value"], errors="coerce")
    completed = completed.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
    if len(completed) < 2:
        return None

    partial = df[df["segment"] == "initial_partial"].copy()
    added = df[df["segment"] == "added_completion"].copy()
    partial["time"] = pd.to_datetime(partial["time"], errors="coerce")
    added["time"] = pd.to_datetime(added["time"], errors="coerce")
    partial = partial.dropna(subset=["time"]).sort_values("time")
    added = added.dropna(subset=["time"]).sort_values("time")
    if partial.empty or added.empty:
        return None
    if added["time"].iloc[0] <= partial["time"].iloc[-1]:
        return None
    completed_duration = (
        completed["time"].iloc[-1] - completed["time"].iloc[0]
    ).total_seconds()
    partial_duration = (
        partial["time"].iloc[-1] - partial["time"].iloc[0]
    ).total_seconds()
    if completed_duration <= partial_duration:
        return None

    minutes = (completed["time"] - completed["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float) / 60.0
    values = completed["value"].to_numpy(dtype=float)
    if len(minutes) < 2 or float(minutes[-1]) <= 0.0:
        return None
    return minutes.tolist(), values.tolist()


def _active_completion_curve(sensor_name: str, profile: dict) -> Optional[tuple[list[float], list[float]]]:
    curve = LLM_SENSOR_ACTIVE_COMPLETION_CURVE.get(sensor_name)
    if curve:
        return curve
    return _load_llm_completion_curve(profile)


def _load_llm_default_params(appliance_key: str) -> dict:
    params_path = _BASE_DIR / "LLM" / "smartmeter" / "common" / "default_parameters.json"
    try:
        data = json.loads(params_path.read_text(encoding="utf-8"))
        cfg = data.get(appliance_key)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _positive_config_float(cfg: dict, key: str) -> Optional[float]:
    try:
        value = float(cfg.get(key))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0.0:
        return None
    return value


def _filter_llm_cases_for_selection(cases_df: pd.DataFrame, params: dict) -> tuple[pd.DataFrame, bool]:
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


def _standardize_realtime_power_df(raw: pd.DataFrame) -> Optional[pd.DataFrame]:
    if raw is None or raw.empty:
        return None

    time_col = next((c for c in ("time", "timestamp", "timestamp_iso", "datetime") if c in raw.columns), None)
    value_col = next((c for c in ("value", "power_W", "power", "consumption") if c in raw.columns), None)
    if not time_col or not value_col:
        return None

    df = pd.DataFrame({
        "time": pd.to_datetime(raw[time_col], errors="coerce"),
        "value": pd.to_numeric(raw[value_col], errors="coerce"),
    })
    df = df.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
    return df if len(df) >= 2 else None


def _load_bound_realtime_power_df(sensor_name: str) -> Optional[pd.DataFrame]:
    ip = _bound_smartmeter_ip(sensor_name)
    if not ip:
        return None

    devices_dir = _BASE_DIR / "devices"
    exact = devices_dir / f"smartmeter_{_sanitize(sensor_name)}.csv"
    candidates = [exact]
    candidates.extend(sorted(devices_dir.glob("smartmeter_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True))

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)

        try:
            raw = pd.read_csv(path)
        except Exception:
            continue
        if raw.empty:
            continue

        if "ip" in raw.columns:
            raw = raw[raw["ip"].astype(str).str.strip() == ip]
            if raw.empty:
                continue

        df = _standardize_realtime_power_df(raw)
        if df is not None:
            return df
    return None


def _load_time_value_csv_for_realtime(
    sensor_name: str,
    associated_device: Optional[str],
    source_name: Optional[str],
) -> Optional[pd.DataFrame]:
    return _load_bound_realtime_power_df(sensor_name)


def _extract_realtime_trailing_short_cycle(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    cycle_cfg = params.get("cycle", {}) if isinstance(params, dict) else {}
    threshold = float(cycle_cfg.get("threshold_watts", 0.0))
    min_duration_minutes = float(cycle_cfg.get("min_duration_minutes", 0.0))
    min_off_minutes = float(cycle_cfg.get("min_off_minutes", 0.0))
    max_idle_gap_minutes = cycle_cfg.get("max_idle_gap_minutes", 5.0)

    work = df[["time", "value"]].copy()
    work = work.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
    if work.empty:
        return pd.DataFrame(columns=["time", "value"])

    values = work["value"].to_numpy(dtype=float)
    times = pd.to_datetime(work["time"]).to_numpy(dtype="datetime64[ns]")
    above = values > threshold
    max_idle_gap_seconds = None
    if max_idle_gap_minutes is not None and float(max_idle_gap_minutes) > 0:
        max_idle_gap_seconds = float(max_idle_gap_minutes) * 60.0

    in_cycle = False
    start_idx = -1
    last_above_idx = -1
    off_start_idx = -1
    trailing_range: tuple[int, int] | None = None

    for i in range(len(work)):
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
        if off_minutes >= min_off_minutes:
            in_cycle = False
            start_idx = -1
            last_above_idx = -1
            off_start_idx = -1

    if in_cycle and start_idx >= 0 and last_above_idx >= start_idx:
        start_time = pd.Timestamp(times[start_idx])
        end_time = pd.Timestamp(times[last_above_idx])
        duration_minutes = (end_time - start_time).total_seconds() / 60.0
        if duration_minutes < min_duration_minutes:
            trailing_range = (start_idx, last_above_idx)

    if trailing_range is None:
        return pd.DataFrame(columns=["time", "value"])

    start_idx, end_idx = trailing_range
    return work.iloc[start_idx : end_idx + 1].copy().reset_index(drop=True)


def _completion_curve_from_partial_and_reference(
    partial_df: pd.DataFrame,
    reference_df: pd.DataFrame,
) -> Optional[tuple[list[float], list[float]]]:
    partial = partial_df.copy().reset_index(drop=True)
    reference = reference_df.copy().reset_index(drop=True)
    partial["time"] = pd.to_datetime(partial["time"])
    reference["time"] = pd.to_datetime(reference["time"])
    partial["value"] = pd.to_numeric(partial["value"], errors="coerce")
    reference["value"] = pd.to_numeric(reference["value"], errors="coerce")
    partial = partial.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
    reference = reference.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
    if len(partial) < 2 or len(reference) < 2:
        return None

    partial_duration_seconds = max(
        0.0,
        (partial["time"].iloc[-1] - partial["time"].iloc[0]).total_seconds(),
    )
    ref_rel_seconds = (reference["time"] - reference["time"].iloc[0]).dt.total_seconds()
    suffix = reference.loc[ref_rel_seconds > partial_duration_seconds, ["time", "value"]].copy()
    if not suffix.empty:
        shifted_seconds = (
            (pd.to_datetime(suffix["time"]) - reference["time"].iloc[0]).dt.total_seconds()
            - partial_duration_seconds
        )
        suffix["time"] = partial["time"].iloc[-1] + pd.to_timedelta(shifted_seconds, unit="s")
        suffix = suffix[suffix["time"] > partial["time"].iloc[-1]].reset_index(drop=True)
        completed = pd.concat([partial[["time", "value"]], suffix[["time", "value"]]], ignore_index=True)
    else:
        completed = partial[["time", "value"]].copy()

    completed = completed.sort_values("time").reset_index(drop=True)
    minutes = (completed["time"] - completed["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float) / 60.0
    values = completed["value"].to_numpy(dtype=float)
    if len(minutes) < 2 or float(minutes[-1]) <= 0.0:
        return None
    return minutes.tolist(), values.tolist()


def _select_realtime_completion_match(
    profile: dict,
    appliance_key: str,
    sensor_name: str,
    associated_device: Optional[str],
) -> Optional[dict[str, object]]:
    source_name = str(profile.get("source_name") or "").strip()
    df = _load_time_value_csv_for_realtime(sensor_name, associated_device, source_name)
    if df is None or df.empty:
        return None

    params = _load_llm_default_params(appliance_key)
    partial_df = _extract_realtime_trailing_short_cycle(df, params)
    if len(partial_df) < 2:
        return None

    try:
        chosen_k = int(profile.get("chosen_k"))
        output_dir = _resolve_llm_catalog_path(str(profile.get("output_dir") or "").strip())
        clusters_df = pd.read_csv(output_dir / f"results_k{chosen_k}" / "clusters.csv")
        pkl_path = _resolve_llm_catalog_path(str(profile.get("pkl_path") or "").strip())
        with open(pkl_path, "rb") as fp:
            stored_cycles = pickle.load(fp)
    except Exception:
        return None

    if clusters_df.empty or not isinstance(stored_cycles, list):
        return None

    cycle_by_id = {
        int(cycle["cycle_id"]): cycle
        for cycle in stored_cycles
        if isinstance(cycle, dict) and cycle.get("cycle_id") is not None
    }
    if not cycle_by_id:
        return None

    feature_cfg = params.get("features", {}) if isinstance(params, dict) else {}
    target_len = max(40, int(feature_cfg.get("target_len", 200)))
    partial_duration_seconds = max(
        0.0,
        (partial_df["time"].iloc[-1] - partial_df["time"].iloc[0]).total_seconds(),
    )
    if partial_duration_seconds <= 0:
        return None

    partial_curve = _normalize_curve_values(_resample_realtime_values(partial_df, target_len))
    if partial_curve.size == 0 or np.isnan(partial_curve).all():
        return None
    partial_max = float(partial_df["value"].max())
    partial_mean = float(partial_df["value"].mean())

    ranked: list[tuple[float, pd.Series, pd.DataFrame]] = []
    for _, row in clusters_df.iterrows():
        try:
            cycle_id = int(row["cycle_id"])
        except Exception:
            continue
        cycle = cycle_by_id.get(cycle_id)
        if not cycle:
            continue
        reference_df = pd.DataFrame(cycle.get("data") or [])
        if reference_df.empty or "time" not in reference_df.columns or "value" not in reference_df.columns:
            continue
        reference_df["time"] = pd.to_datetime(reference_df["time"], errors="coerce")
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

        ref_curve = _normalize_curve_values(_resample_realtime_values(ref_prefix, target_len))
        if ref_curve.size == 0 or np.isnan(ref_curve).all():
            continue

        curve_rmse = float(np.sqrt(np.nanmean((partial_curve - ref_curve) ** 2)))
        ref_max = float(ref_prefix["value"].max())
        ref_mean = float(ref_prefix["value"].mean())
        power_penalty = abs(partial_max - ref_max) / max(1.0, partial_max, ref_max)
        mean_penalty = abs(partial_mean - ref_mean) / max(1.0, partial_mean, ref_mean)
        score = curve_rmse + 0.30 * power_penalty + 0.20 * mean_penalty
        ranked.append((score, row, reference_df))

    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0])
    score, matched_row, reference_df = ranked[0]
    try:
        cycle_id = int(matched_row["cycle_id"])
    except Exception:
        return None
    try:
        cluster_id = int(matched_row["cluster"])
    except Exception:
        cluster_id = _cluster_for_cycle_id(profile, cycle_id)

    curve = _completion_curve_from_partial_and_reference(partial_df, reference_df)
    if not curve:
        return None
    return {
        "cycle_id": cycle_id,
        "cluster": cluster_id,
        "curve": curve,
        "duration_minutes": float(curve[0][-1]),
        "score": float(score),
    }


def _realtime_csv_candidates(
    sensor_name: str,
    associated_device: Optional[str],
    source_name: Optional[str] = None,
) -> list[Path]:
    devices_dir = _BASE_DIR / "devices"
    tokens = [source_name or "", associated_device or "", sensor_name]
    candidates: list[tuple[int, Path]] = []
    for priority, token in enumerate(tokens):
        clean = str(token or "").strip()
        if not clean:
            continue
        paths = [
            devices_dir / f"{clean}.csv",
            devices_dir / f"smartmeter_{clean}.csv",
        ]
        paths.extend(sorted(devices_dir.glob(f"{clean}*.csv")))
        paths.extend(sorted(devices_dir.glob(f"smartmeter_{clean}*.csv")))
        candidates.extend((priority, path) for path in paths)

    seen = set()
    unique: list[tuple[int, Path]] = []
    for priority, path in candidates:
        key = str(path)
        if key not in seen and path.is_file():
            seen.add(key)
            unique.append((priority, path))
    unique.sort(key=lambda item: (item[0], -item[1].stat().st_mtime))
    return [path for _priority, path in unique]


def _load_realtime_partial_curve(
    sensor_name: str,
    associated_device: Optional[str],
    source_name: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    work = _load_bound_realtime_power_df(sensor_name)
    if work is None or work.empty:
        return None

    active_idx = work.index[work["value"] > 0].tolist()
    if len(active_idx) < 2:
        return None

    start_idx = active_idx[-1]
    for idx in reversed(active_idx[:-1]):
        next_time = work.loc[start_idx, "time"]
        this_time = work.loc[idx, "time"]
        gap_min = (next_time - this_time).total_seconds() / 60.0
        if idx != start_idx - 1 and gap_min > 20.0:
            break
        if work.loc[idx + 1:start_idx - 1, "value"].le(0).any():
            break
        start_idx = idx

    segment = work.loc[start_idx:active_idx[-1]].copy()
    segment = segment[segment["value"] > 0].reset_index(drop=True)
    if len(segment) >= 2:
        return segment
    return None


def _resample_realtime_values(df: pd.DataFrame, target_len: int) -> Optional[np.ndarray]:
    if df is None or len(df) < 2:
        return None
    rel_sec = (df["time"] - df["time"].iloc[0]).dt.total_seconds().to_numpy(dtype=float)
    values = pd.to_numeric(df["value"], errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(rel_sec) & np.isfinite(values)
    rel_sec = rel_sec[valid]
    values = values[valid]
    if len(values) < 2 or rel_sec[-1] <= 0:
        return None
    xp = rel_sec / rel_sec[-1]
    return np.interp(np.linspace(0.0, 1.0, target_len), xp, values)


def _normalize_curve_values(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    mean = float(np.nanmean(arr))
    std = float(np.nanstd(arr))
    if not np.isfinite(std) or std < 1e-9:
        return arr - mean
    return (arr - mean) / std


def _select_realtime_prediction_cycle(
    profile: dict,
    sensor_name: str,
    associated_device: Optional[str],
) -> Optional[int]:
    source_name = str(profile.get("source_name") or "").strip()
    partial_df = _load_realtime_partial_curve(sensor_name, associated_device, source_name)
    if partial_df is None or partial_df.empty:
        return None

    try:
        pkl_path = _resolve_llm_catalog_path(str(profile.get("pkl_path") or "").strip())
        with open(pkl_path, "rb") as fp:
            cycles = pickle.load(fp)
    except Exception:
        return None
    if not isinstance(cycles, list):
        return None

    partial_duration_sec = max(
        0.0,
        (partial_df["time"].iloc[-1] - partial_df["time"].iloc[0]).total_seconds(),
    )
    if partial_duration_sec <= 0:
        return None

    target_len = 80
    partial_values = _resample_realtime_values(partial_df, target_len)
    if partial_values is None:
        return None
    partial_norm = _normalize_curve_values(partial_values)
    partial_max = float(partial_df["value"].max())
    partial_mean = float(partial_df["value"].mean())

    best_score = None
    best_cycle_id = None
    for cycle in cycles:
        try:
            cycle_id = int(cycle.get("cycle_id"))
            ref_df = pd.DataFrame(cycle.get("data") or [])
        except Exception:
            continue
        if ref_df.empty or "time" not in ref_df.columns or "value" not in ref_df.columns:
            continue
        ref_df["time"] = pd.to_datetime(ref_df["time"], errors="coerce")
        ref_df["value"] = pd.to_numeric(ref_df["value"], errors="coerce")
        ref_df = ref_df.dropna(subset=["time", "value"]).sort_values("time").reset_index(drop=True)
        if len(ref_df) < 2:
            continue
        ref_rel_sec = (ref_df["time"] - ref_df["time"].iloc[0]).dt.total_seconds()
        if float(ref_rel_sec.iloc[-1]) <= partial_duration_sec:
            continue
        ref_prefix = ref_df[ref_rel_sec <= partial_duration_sec].copy()
        if len(ref_prefix) < 2:
            ref_prefix = ref_df.iloc[:2].copy()
        ref_values = _resample_realtime_values(ref_prefix, target_len)
        if ref_values is None:
            continue

        ref_norm = _normalize_curve_values(ref_values)
        shape_rmse = float(np.sqrt(np.nanmean((partial_norm - ref_norm) ** 2)))
        ref_max = float(ref_prefix["value"].max())
        ref_mean = float(ref_prefix["value"].mean())
        max_penalty = abs(partial_max - ref_max) / max(1.0, partial_max, ref_max)
        mean_penalty = abs(partial_mean - ref_mean) / max(1.0, partial_mean, ref_mean)
        score = shape_rmse + 0.30 * max_penalty + 0.20 * mean_penalty
        if best_score is None or score < best_score:
            best_score = score
            best_cycle_id = cycle_id

    return best_cycle_id


def _select_washing_machine_cycle_id(profile: dict, sensor_name: str, now_dt: datetime) -> Optional[int]:
    cases_df = _load_llm_cluster_cases(profile)
    if cases_df is None or cases_df.empty:
        return None

    sim_date = now_dt.date().isoformat()
    weekday = int(now_dt.weekday())
    weekday_cases = cases_df[cases_df["weekday"] == weekday].copy()
    if weekday_cases.empty:
        return None

    counts = (
        weekday_cases.groupby("cluster", as_index=False)
        .agg(n_cycles=("cycle_id", "count"))
        .sort_values(["n_cycles", "cluster"], ascending=[False, True], kind="mergesort")
        .reset_index(drop=True)
    )
    if counts.empty:
        return None

    chosen_cluster = int(counts.iloc[0]["cluster"])
    weekday_cluster_cases = (
        weekday_cases[weekday_cases["cluster"] == chosen_cluster]
        .sort_values(["start_time", "end_time", "cycle_id"], kind="mergesort")
        .reset_index(drop=True)
    )
    if weekday_cluster_cases.empty:
        return None

    cycle_ids = [int(v) for v in weekday_cluster_cases["cycle_id"].tolist()]
    # A weekday can contain only one historical case. Keep that case first, then
    # continue through the configured cluster instead of repeating it forever.
    if len(cycle_ids) < 2:
        try:
            fallback_cluster = int(
                profile.get("selected_cluster", profile.get("dominant_cluster"))
            )
        except Exception:
            fallback_cluster = chosen_cluster
        fallback_cases = (
            cases_df[cases_df["cluster"] == fallback_cluster]
            .sort_values(["start_time", "end_time", "cycle_id"], kind="mergesort")
        )
        for cycle_id in fallback_cases["cycle_id"].astype(int).tolist():
            if cycle_id not in cycle_ids:
                cycle_ids.append(cycle_id)

    cursor = LLM_WM_DAY_CURSOR.get(sensor_name)

    if (
        not isinstance(cursor, dict)
        or cursor.get("sim_date") != sim_date
        or int(cursor.get("weekday", -1)) != weekday
        or int(cursor.get("cluster", -1)) != chosen_cluster
        or list(cursor.get("cycle_ids") or []) != cycle_ids
    ):
        cursor = {
            "sim_date": sim_date,
            "weekday": weekday,
            "cluster": chosen_cluster,
            "cycle_ids": cycle_ids,
            "next_index": 0,
        }

    next_index = int(cursor.get("next_index", 0))
    if next_index < 0:
        next_index = 0

    selected_cycle_id = cycle_ids[next_index % len(cycle_ids)]
    cursor["next_index"] = (next_index + 1) % len(cycle_ids)
    LLM_WM_DAY_CURSOR[sensor_name] = cursor
    return selected_cycle_id


def _cluster_for_cycle_id(profile: dict, cycle_id: int) -> Optional[int]:
    cases_df = _load_llm_cluster_cases(profile)
    if cases_df is None or cases_df.empty:
        return None
    match = cases_df[cases_df["cycle_id"] == int(cycle_id)]
    if match.empty:
        return None
    try:
        return int(match.iloc[0]["cluster"])
    except Exception:
        return None


def _record_llm_used_case(
    sensor_name: str,
    cycle_id: int,
    cluster_id: Optional[int],
    case_type: str = "normal",
) -> None:
    used = LLM_SENSOR_USED_CASES.setdefault(sensor_name, [])
    used.append({
        "cycle_id": int(cycle_id),
        "cluster": int(cluster_id) if cluster_id is not None else -1,
        "case_type": case_type,
    })


def get_llm_used_cases(sensor_name: str) -> list[dict[str, object]]:
    return [dict(item) for item in LLM_SENSOR_USED_CASES.get(sensor_name, [])]


def _record_llm_generation_event(
    sensor_name: str,
    profile: dict,
    cycle_id: int,
    cluster_id: Optional[int],
    start_dt: datetime,
    duration_minutes: Optional[float],
    event_type: str = "dt_prediction",
) -> None:
    events = LLM_SENSOR_GENERATION_EVENTS.setdefault(sensor_name, [])
    output_dir = str(profile.get("output_dir") or "")
    source_name = str(profile.get("source_name") or "").strip()
    if not source_name and output_dir:
        output_name = Path(output_dir).name
        source_name = output_name[: -len("_output")] if output_name.endswith("_output") else output_name
    try:
        chosen_k = int(profile.get("chosen_k"))
    except Exception:
        chosen_k = None
    events.append({
        "source_name": source_name or "?",
        "output_dir": output_dir,
        "chosen_k": chosen_k,
        "cycle_id": int(cycle_id),
        "cluster": int(cluster_id) if cluster_id is not None else None,
        "started_at": start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_minutes": float(duration_minutes) if duration_minutes is not None else None,
        "event_type": event_type,
    })


def get_llm_generation_events(sensor_name: str) -> list[dict[str, object]]:
    return [dict(item) for item in LLM_SENSOR_GENERATION_EVENTS.get(sensor_name, [])]


def _duration_minutes_for_cycle_id(profile: dict, cycle_id: int) -> Optional[float]:
    cases_df = _load_llm_cluster_cases(profile)
    if cases_df is not None and not cases_df.empty and "duration_minutes" in cases_df.columns:
        match = cases_df[cases_df["cycle_id"] == int(cycle_id)]
        if not match.empty:
            try:
                duration = float(match.iloc[0]["duration_minutes"])
                if duration > 0:
                    return duration
            except Exception:
                pass

    curve = _load_llm_cycle_curve_for_cycle_id(profile, cycle_id_override=cycle_id)
    if not curve:
        return None
    minutes_axis, _values_axis = curve
    if not minutes_axis:
        return None
    try:
        duration = float(minutes_axis[-1])
        return duration if duration > 0 else None
    except Exception:
        return None


def _profile_cursor_key(profile: dict, appliance_key: str, sensor_name: Optional[str]) -> str:
    source_name = str(profile.get("source_name") or "").strip()
    output_dir = str(profile.get("output_dir") or "").strip()
    chosen_k = str(profile.get("chosen_k") or "")
    sensor_key = str(sensor_name or "").strip() or "_global"
    return "|".join([sensor_key, appliance_key, source_name, output_dir, chosen_k])


def _center_cycle_id(cases_df: pd.DataFrame) -> Optional[int]:
    if cases_df.empty or "cycle_id" not in cases_df.columns:
        return None

    work = cases_df.copy().reset_index(drop=True)
    feat_cols = ["duration_minutes", "max_power", "mean_power", "energy_kwh", "time_of_peak_norm"]
    for col in feat_cols:
        if col not in work.columns:
            work[col] = np.nan
        work[col] = pd.to_numeric(work[col], errors="coerce")

    center = work[feat_cols].mean(skipna=True)
    std = work[feat_cols].std(ddof=0).replace(0, 1).fillna(1)

    def _center_dist(row: pd.Series) -> float:
        acc = 0.0
        used = 0
        for col in feat_cols:
            value = row.get(col)
            if pd.isna(value) or pd.isna(center[col]):
                continue
            z = (float(value) - float(center[col])) / float(std[col])
            acc += z * z
            used += 1
        return math.sqrt(acc / max(1, used))

    work["selection_center_distance"] = work.apply(_center_dist, axis=1)
    work["cycle_id"] = pd.to_numeric(work["cycle_id"], errors="coerce")
    work = work.dropna(subset=["cycle_id"]).sort_values(
        ["selection_center_distance", "duration_minutes", "cycle_id"],
        ascending=[True, True, True],
        kind="mergesort",
    )
    if work.empty:
        return None
    try:
        return int(work.iloc[0]["cycle_id"])
    except Exception:
        return None


def _cycle_ids_for_llm_profile(profile: dict, appliance_key: str) -> tuple[list[int], Optional[int]]:
    try:
        configured_cycle_id = int(profile.get("selected_cycle_id"))
    except Exception:
        configured_cycle_id = None

    params = _load_llm_default_params(appliance_key)
    cases_df = _load_llm_cluster_cases(profile)
    if cases_df is None or cases_df.empty:
        return ([configured_cycle_id], configured_cycle_id) if configured_cycle_id is not None else ([], None)

    work = cases_df.copy()
    work["cycle_id"] = pd.to_numeric(work.get("cycle_id"), errors="coerce")
    work["cluster"] = pd.to_numeric(work.get("cluster"), errors="coerce")
    work = work.dropna(subset=["cycle_id", "cluster"]).copy()
    work["cycle_id"] = work["cycle_id"].astype(int)
    work["cluster"] = work["cluster"].astype(int)
    if work.empty:
        return ([configured_cycle_id], configured_cycle_id) if configured_cycle_id is not None else ([], None)

    selectable_df, selection_policy_active = _filter_llm_cases_for_selection(work, params)
    if selection_policy_active and not selectable_df.empty:
        work = selectable_df.copy()

    configured_match = (
        work[work["cycle_id"] == configured_cycle_id]
        if configured_cycle_id is not None
        else pd.DataFrame()
    )
    if not configured_match.empty:
        target_cluster = int(configured_match.iloc[0]["cluster"])
        preferred_cycle_id = configured_cycle_id
    else:
        target_cluster = None
        if not selection_policy_active:
            for key in ("selected_cluster", "dominant_cluster"):
                try:
                    candidate_cluster = int(profile.get(key))
                except Exception:
                    continue
                if not work[work["cluster"] == candidate_cluster].empty:
                    target_cluster = candidate_cluster
                    break

        if target_cluster is None:
            counts = (
                work.groupby("cluster", as_index=False)
                .agg(n_cycles=("cycle_id", "count"))
                .sort_values(["n_cycles", "cluster"], ascending=[False, True], kind="mergesort")
                .reset_index(drop=True)
            )
            if counts.empty:
                return ([configured_cycle_id], configured_cycle_id) if configured_cycle_id is not None else ([], None)
            target_cluster = int(counts.iloc[0]["cluster"])
        preferred_cycle_id = None

    cluster_cases = work[work["cluster"] == target_cluster].copy().reset_index(drop=True)
    if cluster_cases.empty:
        return ([configured_cycle_id], configured_cycle_id) if configured_cycle_id is not None else ([], None)

    if preferred_cycle_id is None and selection_policy_active:
        preferred_cycle_id = _center_cycle_id(cluster_cases)

    sort_cols = [col for col in ("start_time", "end_time", "cycle_id") if col in cluster_cases.columns]
    if not sort_cols:
        sort_cols = ["cycle_id"]
    cluster_cases = cluster_cases.sort_values(sort_cols, kind="mergesort")
    cycle_ids = [int(v) for v in cluster_cases["cycle_id"].tolist()]
    return cycle_ids, preferred_cycle_id


def _select_llm_profile_cycle_id(
    profile: dict,
    appliance_key: str,
    sensor_name: Optional[str] = None,
) -> Optional[int]:
    cycle_ids, preferred_cycle_id = _cycle_ids_for_llm_profile(profile, appliance_key)
    cycle_ids = [int(cycle_id) for cycle_id in cycle_ids if cycle_id is not None]
    if not cycle_ids:
        return None

    cursor_key = _profile_cursor_key(profile, appliance_key, sensor_name)
    cursor = LLM_PROFILE_CYCLE_CURSOR.get(cursor_key)
    if not isinstance(cursor, dict) or list(cursor.get("cycle_ids") or []) != cycle_ids:
        start_index = 0
        if preferred_cycle_id in cycle_ids:
            start_index = cycle_ids.index(int(preferred_cycle_id))
        cursor = {
            "cycle_ids": cycle_ids,
            "next_index": start_index,
        }

    next_index = int(cursor.get("next_index", 0))
    if next_index < 0:
        next_index = 0
    selected_cycle_id = cycle_ids[next_index % len(cycle_ids)]
    cursor["next_index"] = (next_index + 1) % len(cycle_ids)
    LLM_PROFILE_CYCLE_CURSOR[cursor_key] = cursor
    return selected_cycle_id


def prime_llm_cycle_for_sensor(
    sensor_name: str,
    dev_type: Optional[str],
    start_dt: Optional[datetime],
    associated_device: Optional[str] = None,
    force_new_prediction: bool = False,
) -> Optional[dict]:
    appliance_key = _device_type_to_appliance_key(dev_type)
    if not appliance_key:
        return None

    catalog = _load_llm_profile_catalog()
    profile = _profile_for_sensor(catalog, appliance_key, sensor_name, associated_device) if isinstance(catalog, dict) else None
    if not isinstance(profile, dict):
        return None

    now_dt = start_dt or datetime.now()
    active_start = LLM_SENSOR_ON_START.get(sensor_name)
    active_cycle_id = LLM_SENSOR_ACTIVE_CYCLE_ID.get(sensor_name)
    force_new_cycle = bool(force_new_prediction)
    if active_start is not None and active_cycle_id is not None:
        if sensor_name in LLM_SENSOR_ACTIVE_COMPLETION:
            active_curve = _active_completion_curve(sensor_name, profile)
            active_duration = float(active_curve[0][-1]) if active_curve else None
        else:
            active_duration = _duration_minutes_for_cycle_id(profile, int(active_cycle_id))
        if active_duration is not None and not force_new_cycle:
            active_elapsed_min = max(0.0, (now_dt - active_start).total_seconds() / 60.0)
            if active_elapsed_min < active_duration:
                active_cluster = _cluster_for_cycle_id(profile, int(active_cycle_id))
                return {
                    "sensor_name": sensor_name,
                    "appliance_key": appliance_key,
                    "source_name": str(profile.get("source_name") or ""),
                    "output_dir": str(profile.get("output_dir") or ""),
                    "cycle_id": int(active_cycle_id),
                    "cluster": int(active_cluster) if active_cluster is not None else None,
                    "duration_minutes": float(active_duration),
                    "continued": True,
                }

        _clear_active_llm_cycle(sensor_name)

    cycle_id: Optional[int] = None
    use_completion_curve = False
    completion_curve_override: Optional[tuple[list[float], list[float]]] = None
    runtime_match: Optional[dict[str, object]] = None
    realtime_prediction = False
    realtime_dt_enabled = _should_use_realtime_dt(sensor_name)
    if appliance_key == "washing_machine":
        if realtime_dt_enabled:
            runtime_match = _select_realtime_completion_match(
                profile,
                appliance_key,
                sensor_name,
                associated_device,
            )
            if isinstance(runtime_match, dict):
                try:
                    cycle_id = int(runtime_match["cycle_id"])
                except Exception:
                    cycle_id = None
                completion_curve_override = runtime_match.get("curve")
                use_completion_curve = True
                realtime_prediction = True
            else:
                cycle_id = _select_realtime_prediction_cycle(profile, sensor_name, associated_device)
                realtime_prediction = cycle_id is not None
        elif LLM_SMARTMETER_MODE == "realtime_dt":
            completion_curve_override = _load_llm_completion_curve(profile)
            completion_summary = _completion_summary_for_profile(profile)
            try:
                cycle_id = int(completion_summary.get("matched_cycle_id"))
            except Exception:
                cycle_id = None
            if cycle_id is not None and completion_curve_override:
                use_completion_curve = True
                realtime_prediction = True
        if cycle_id is None:
            cycle_id = _select_washing_machine_cycle_id(profile, sensor_name, now_dt)
    if cycle_id is None:
        cycle_id = _select_llm_profile_cycle_id(profile, appliance_key, sensor_name)
    if cycle_id is None:
        return None

    if use_completion_curve:
        if isinstance(runtime_match, dict) and runtime_match.get("cluster") is not None:
            try:
                cluster_id = int(runtime_match["cluster"])
            except Exception:
                cluster_id = _cluster_for_cycle_id(profile, int(cycle_id))
        else:
            cluster_id = _cluster_for_cycle_id(profile, int(cycle_id))
        completion_curve = completion_curve_override
        duration_minutes = float(completion_curve[0][-1]) if completion_curve else None
    else:
        cluster_id = _cluster_for_cycle_id(profile, int(cycle_id))
        duration_minutes = _duration_minutes_for_cycle_id(profile, int(cycle_id))

    LLM_SENSOR_ON_START[sensor_name] = now_dt
    LLM_SENSOR_ACTIVE_CYCLE_ID[sensor_name] = int(cycle_id)
    if use_completion_curve:
        LLM_SENSOR_ACTIVE_COMPLETION.add(sensor_name)
        if completion_curve_override:
            LLM_SENSOR_ACTIVE_COMPLETION_CURVE[sensor_name] = completion_curve_override
    else:
        LLM_SENSOR_ACTIVE_COMPLETION.discard(sensor_name)
        LLM_SENSOR_ACTIVE_COMPLETION_CURVE.pop(sensor_name, None)
    if realtime_prediction:
        LLM_SENSOR_ACTIVE_PREDICTION.add(sensor_name)
    else:
        LLM_SENSOR_ACTIVE_PREDICTION.discard(sensor_name)
    if not realtime_prediction:
        _record_llm_used_case(sensor_name, int(cycle_id), cluster_id, case_type="normal")
    if realtime_prediction:
        _record_llm_generation_event(
            sensor_name,
            profile,
            int(cycle_id),
            cluster_id,
            now_dt,
            duration_minutes,
            event_type="completion_match" if use_completion_curve else "dt_prediction",
        )

    return {
        "sensor_name": sensor_name,
        "appliance_key": appliance_key,
        "source_name": str(profile.get("source_name") or ""),
        "output_dir": str(profile.get("output_dir") or ""),
        "cycle_id": int(cycle_id),
        "cluster": int(cluster_id) if cluster_id is not None else None,
        "duration_minutes": float(duration_minutes) if duration_minutes is not None else None,
        "case_type": "completed" if use_completion_curve else (
            "predicted" if realtime_prediction else "normal"
        ),
        "event_type": (
            "completion_match"
            if use_completion_curve
            else "dt_prediction"
            if realtime_prediction
            else "normal"
        ),
    }


def _interp_cycle_value(
    minutes_axis: list[float],
    values_axis: list[float],
    elapsed_min: float,
    *,
    repeat: bool = True,
) -> float:
    if not minutes_axis or not values_axis:
        return 0.0
    if len(minutes_axis) == 1:
        return max(0.0, float(values_axis[0]))

    total = float(minutes_axis[-1])
    if total <= 0.0:
        return max(0.0, float(values_axis[-1]))

    if repeat:
        x = float(elapsed_min) % total
    elif float(elapsed_min) > total:
        return 0.0
    else:
        x = float(elapsed_min)
    i = bisect_right(minutes_axis, x)
    if i <= 0:
        return max(0.0, float(values_axis[0]))
    if i >= len(minutes_axis):
        return max(0.0, float(values_axis[-1]))

    x0 = float(minutes_axis[i - 1])
    x1 = float(minutes_axis[i])
    y0 = float(values_axis[i - 1])
    y1 = float(values_axis[i])
    if x1 <= x0:
        return max(0.0, y0)
    alpha = (x - x0) / (x1 - x0)
    return max(0.0, y0 + alpha * (y1 - y0))


def _get_llm_smartmeter_consumption(
    sensor_name: str,
    dev_type: Optional[str],
    dev_state: int,
    current_datetime: Optional[datetime],
    associated_device: Optional[str] = None,
) -> Optional[float]:
    appliance_key = _device_type_to_appliance_key(dev_type)
    if not appliance_key:
        return None

    catalog = _load_llm_profile_catalog()
    profile = _profile_for_sensor(catalog, appliance_key, sensor_name, associated_device) if isinstance(catalog, dict) else None
    if not isinstance(profile, dict):
        return None

    if dev_state != 1:
        return 0.0

    now_dt = current_datetime or datetime.now()
    start_dt = LLM_SENSOR_ON_START.get(sensor_name)
    if start_dt is None:
        primed = prime_llm_cycle_for_sensor(
            sensor_name,
            dev_type,
            now_dt,
            associated_device=associated_device,
            force_new_prediction=_should_use_realtime_dt(sensor_name),
        )
        start_dt = LLM_SENSOR_ON_START.get(sensor_name, now_dt)
        if primed is None and appliance_key != "washing_machine":
            LLM_SENSOR_ON_START[sensor_name] = now_dt
            start_dt = now_dt

    cycle_id_override = LLM_SENSOR_ACTIVE_CYCLE_ID.get(sensor_name)
    transition_count = 0
    while start_dt is not None and cycle_id_override is not None:
        if sensor_name in LLM_SENSOR_ACTIVE_COMPLETION:
            completion_curve = _active_completion_curve(sensor_name, profile)
            duration_minutes = float(completion_curve[0][-1]) if completion_curve else None
        else:
            duration_minutes = _duration_minutes_for_cycle_id(profile, int(cycle_id_override))

        if duration_minutes is None or duration_minutes <= 0:
            break

        elapsed_min = max(0.0, (now_dt - start_dt).total_seconds() / 60.0)
        if elapsed_min < duration_minutes:
            break

        if appliance_key in LLM_AUTO_STOP_APPLIANCES:
            _clear_active_llm_cycle(sensor_name)
            LLM_SENSOR_COMPLETED_CYCLES.add(sensor_name)
            return 0.0

        if appliance_key not in LLM_CONTINUOUS_APPLIANCES:
            break

        # A computer can remain on longer than any single historical profile.
        # Continue from the next case without inserting an artificial zero-power
        # interval, preserving any elapsed time beyond the previous profile.
        next_start_dt = start_dt + timedelta(minutes=float(duration_minutes))
        _clear_active_llm_cycle(sensor_name)
        primed = prime_llm_cycle_for_sensor(
            sensor_name,
            dev_type,
            next_start_dt,
            associated_device=associated_device,
        )
        if primed is None:
            return None

        start_dt = LLM_SENSOR_ON_START.get(sensor_name, next_start_dt)
        cycle_id_override = LLM_SENSOR_ACTIVE_CYCLE_ID.get(sensor_name)
        transition_count += 1
        if transition_count >= 32:
            # Protect the update loop from malformed zero-duration catalogs.
            break

    if sensor_name in LLM_SENSOR_ACTIVE_COMPLETION:
        curve = _active_completion_curve(sensor_name, profile)
    else:
        curve = _load_llm_cycle_curve_for_cycle_id(profile, cycle_id_override=cycle_id_override)
    if not curve:
        return None

    elapsed_min = max(0.0, (now_dt - start_dt).total_seconds() / 60.0)
    minutes_axis, values_axis = curve
    return _interp_cycle_value(minutes_axis, values_axis, elapsed_min, repeat=False)


def _find_associated_device(dev_list, wanted_name):
    if not dev_list or not wanted_name:
        return None
    return next((d for d in dev_list if d.name == wanted_name), None)


def compute_smartmeter_consumption(sensor, devices, delta_seconds, current_datetime, active_cycles_store=None):
    name = sensor.name
    associated_device = sensor.associated_device

    new_consumption = 0.0

    if associated_device:
        associated_dev = _find_associated_device(devices, associated_device)
        if not associated_dev:
            associated_dev = _find_associated_device(devices_file, associated_device)

        dev_name = None
        dev_type = None
        dev_state = 0
        if associated_dev:
            if isinstance(associated_dev, Device):
                dev_name = associated_dev.name
                dev_type = associated_dev.type
                dev_state = associated_dev.state

        llm_consumption = _get_llm_smartmeter_consumption(
            name,
            dev_type,
            int(dev_state),
            current_datetime,
            associated_device=associated_device,
        )
        if name in LLM_SENSOR_COMPLETED_CYCLES:
            LLM_SENSOR_COMPLETED_CYCLES.discard(name)
            if associated_dev is not None:
                associated_dev.state = 0
                associated_dev.current_consumption = 0.0
                associated_dev.consumption_direction = 0
            if isinstance(active_cycles_store, dict) and dev_name is not None:
                active_cycles_store.pop(dev_name, None)
            return 0.0
        if llm_consumption is not None:
            new_consumption = max(0.0, float(llm_consumption))
        elif dev_name is not None and dev_type is not None:
            # Fallback only if no LLM profile exists for this appliance type.
            if dev_state == 1:
                cycles_ref = active_cycles_store if isinstance(active_cycles_store, dict) else {}
                new_consumption = get_device_consumption(
                    dev_name, dev_type, current_datetime, cycles_ref, dev_state
                )
            else:
                new_consumption = 0.0

    return float(new_consumption)


class SensorDialog(simpledialog.Dialog):
    def __init__(self, parent, title=None, device_names=None):
        self._preloaded_device_names = list(device_names or [])
        super().__init__(parent, title)

    def _device_names_for_association(self) -> list[str]:
        names = set()

        for name in self._preloaded_device_names:
            if name:
                names.add(str(name).strip())

        for dev in devices or []:
            if hasattr(dev, "name") and dev.name:
                names.add(str(dev.name).strip())

        for dev in devices_file or []:
            if hasattr(dev, "name") and dev.name:
                names.add(str(dev.name).strip())

        return sorted(n for n in names if n)

    def body(self, master):
        tk.Label(master, text="Sensor name:").grid(row=0)
        tk.Label(master, text="Sensor type:").grid(row=1)

        self.sensor_name = tk.Entry(master)
        self.sensor_name.grid(row=0, column=1)

        self.sensor_type = ttk.Combobox(
            master,
            values=["PIR", "Temperature", "Switch", "Smart Meter", "Weight"],
            state="readonly",
        )
        self.sensor_type.grid(row=1, column=1)
        self.sensor_type.current(0)

        self.direction_label = tk.Label(master, text="Direction (degrees):")
        self.direction_entry = tk.Entry(master)
        self.direction_entry.insert(0, "0")

        self.associated_device_label = tk.Label(master, text="Associated device:")

        devices_names = self._device_names_for_association()

        self.associated_device_combobox = ttk.Combobox(master, values=devices_names, state="readonly")

        self.sensor_type.bind("<<ComboboxSelected>>", self.on_sensor_type_selected)
        self.on_sensor_type_selected(None)

        return self.sensor_name
    # Show 'direction' for PIR or 'associated device' for Smart Meter only.
    def on_sensor_type_selected(self, event):
        type = self.sensor_type.get()

        if type == "PIR":
            self.direction_label.grid(row=2, column=0)
            self.direction_entry.grid(row=2, column=1)

            self.associated_device_label.grid_remove()
            self.associated_device_combobox.grid_remove()

        elif type == "Smart Meter":
            self.direction_label.grid_remove()
            self.direction_entry.grid_remove()

            devices_names = self._device_names_for_association()
            if devices_names:
                self.associated_device_combobox.configure(values=devices_names, state="readonly")
            else:
                self.associated_device_combobox.configure(values=["No devices available"], state="disabled")

            self.associated_device_label.grid(row=2, column=0)
            self.associated_device_combobox.grid(row=2, column=1)

            if devices_names:
                self.associated_device_combobox.current(0)
            else:
                self.associated_device_combobox.current(0)

        else:
            self.direction_label.grid_remove()
            self.direction_entry.grid_remove()
            self.associated_device_label.grid_remove()
            self.associated_device_combobox.grid_remove()

    # Check for empty/duplicate name; require direction (PIR) or device (Smart Meter).
    def validate(self):
        name = self.sensor_name.get().strip()
        if not name:
            messagebox.showwarning("Input not valid", "Sensor name cannot be empty.")
            return False

        # Check both runtime sensors and file sensors
        for s in sensors:
            if name == s.name:
                messagebox.showwarning("Input not valid", "Sensor name already exists.")
                return False
        
        for s in sensors_file:
            if name == s.name:
                messagebox.showwarning("Input not valid", "Sensor name already exists.")
                return False

        if self.sensor_type.get() == "PIR" and not self.direction_entry.get().strip():
            messagebox.showwarning("Input not valid", "Pir sensor direction cannot be empty.")
            return False

        if self.sensor_type.get() == "Smart Meter" and not self.associated_device_combobox.get():
            messagebox.showwarning(
                "Input not valid",
                "Select a device to associate with the Smart Meter.",
            )
            return False

        if self.sensor_type.get() == "Smart Meter" and self.associated_device_combobox.get() == "No devices available":
            messagebox.showwarning(
                "Input not valid",
                "Add at least one device before adding a Smart Meter.",
            )
            return False

        return True

    def apply(self):
        name = self.sensor_name.get()
        type = self.sensor_type.get()
        params = get_sensor_params(type)

        if type == "PIR":
            direction = float(self.direction_entry.get())
            params["direction"] = direction

        associated_device = None
        if type == "Smart Meter":
            associated_device = self.associated_device_combobox.get()

        if type == "Temperature":
            # If a CSV series exists, use its first value as the initial state.
            series = _load_temp_series_for_sensor(name)
            if series:
                _, vals = series
                if vals:
                    real_temp = float(vals[0])
                    params["state"] = min(params["max"], real_temp)

        self.result = (
            name,
            type,
            params["min"],
            params["max"],
            params["step"],
            params["state"],
            params.get("direction", None),
            params["consumption"],
            associated_device,
        )
