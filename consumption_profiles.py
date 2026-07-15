#consumption_profiles.py
from __future__ import annotations

from typing import Dict
import pandas as pd

consumption_profiles = {
    "Fridge": {
        "standby": 23, # on the left the value of the minutes, on the right the value of the power consumed
        "profile": {
            0: 74.7,
            16: 70.6,
            33: 70.6,
            49: 99.7,
            65: 99.7,
            81: 99.7,
            98: 74.8,
            114: 24.0,
            130: 24.0,
            146: 90.1,
            163: 90.1,
            179: 82.9
        }
    },

    "Washing_Machine": {
        "standby": 0,
        "profile": {
            0: 3.0,
            13: 687.6,
            26: 2094.3,
            39: 102.9,
            52: 100.3,
            65: 108.3,
            78: 138.7,
            91: 255.0
        }
    },

    "Oven": {
        "standby": 0,
        "profile": {
            0: 942.8,
            3: 995.3,
            6: 916.6,
            9: 947.7
        }
    },

    "Computer": {
        "standby": 40.0,
        "profile": {
            0: 65.0,
            15: 95.0,
            30: 140.0,
            45: 120.0,
            60: 160.0,
            75: 130.0,
            90: 110.0,
            105: 45.0,
        }
    },

    "Dishwasher": {
        "standby": 0,
        "profile": {
            0: 67.1,
            13: 1716.1,
            26: 151.2,
            39: 66.5,
            52: 1966.7,
            65: 7.8,
            78: 4.6
        }
    },

    "Coffee_Machine": {
        "standby": 0,
        "profile": {
            0: 1200.0,
            1: 700.0,
            2: 200.0
        }
    }
}


def interpolated_consumption(profile, minutes, standby):
    keys = sorted(profile)
    if not keys:
        return standby
    if minutes <= keys[0]:
        return profile[keys[0]]
    elif minutes >= keys[-1]:
        return profile[keys[-1]]
    else:
        for i in range(len(keys) - 1):
            t1, t2 = keys[i], keys[i + 1]
            if t1 <= minutes < t2:
                c1, c2 = profile[t1], profile[t2]
                factor = (minutes - t1) / (t2 - t1)
                return c1 + (c2 - c1) * factor
    return profile[keys[0]]



def consumption_step(profile: dict, minutes: float, standby: float, repeat: bool = False, start_from_standby: bool = True) -> float:
    """Get the consumption at the given minute from the profile, with optional looping."""
    if not profile:
        return standby
    keys = sorted(profile)
    duration = keys[-1]

    t = minutes % duration if repeat and duration > 0 else minutes

    if t < keys[0]:
        return profile[keys[0]]

    last_key = keys[0]
    for k in keys:
        if t < k:
            return profile[last_key]
        last_key = k
    return profile[keys[-1]] if repeat else profile[keys[-1]]

def add_noise(value: float, rel_std: float = 0.02, abs_std: float = 3.0) -> float:
    """Small gaussian noise to avoid perfectly flat synthetic signals."""
    import random
    std = max(abs_std, abs(value) * rel_std)
    try:
        return max(0.0, random.gauss(float(value), std))
    except Exception:
        return float(value)


def profile_value_linear(profile: dict, minutes: float, standby: float) -> float:
    """Linear interpolation on the consumption profile."""
    return float(interpolated_consumption(profile, minutes, standby))


def _active_cycle_parts(entry):
    if isinstance(entry, dict):
        return entry.get("start_time"), entry.get("cycle_type"), entry.get("llm")
    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
        return entry[0], entry[1], None
    return None, None, None



def get_device_consumption(device_name, device_type, current_timestamp, active_cycles, device_state=1, *, add_random_noise: bool = True) -> float:
    """Get the power consumption (W) of a device at the given timestamp."""
    if device_state == 0:
        return 0.0

    # choose base profile dict depending on device_type
    # NOTE: use only predefined datasets here.
    # Real CSV replay for Smart Meter is handled in sensor.changeSmartMeter.
    base = consumption_profiles.get(device_type)

    if not base:
        return 0.0

    standby = float(base.get("standby", 0.0))
    prof_det = base.get("profile", {}) or {}

    # which types loop
    repeat_by_type = {
        "Fridge": True,
        "Washing_Machine": False,
        "Dishwasher": False,
        "Coffee_Machine": False,
        "Oven": False,
        "Computer": False,
    }
    repeat = bool(repeat_by_type.get(device_type, False))

    # If in an active cycle, evaluate profile at elapsed minutes
    if device_name in active_cycles:
        start_time, _cycle_type, llm_meta = _active_cycle_parts(active_cycles[device_name])
        if start_time is None:
            return standby
        elapsed_min = (current_timestamp - start_time).total_seconds() / 60.0

        llm_duration = None
        if isinstance(llm_meta, dict):
            try:
                llm_duration = float(llm_meta.get("duration_minutes"))
            except Exception:
                llm_duration = None

        # handle looping / bounds
        keys = sorted(prof_det) if prof_det else []
        duration = float(keys[-1]) if keys else 0.0
        if llm_duration is not None and llm_duration > 0:
            duration = llm_duration

        t = elapsed_min
        if repeat and duration > 0:
            t = elapsed_min % duration
        elif duration > 0:
            t = max(0.0, min(elapsed_min, duration))

        value = profile_value_linear(prof_det, t, standby)
        return add_noise(value) if add_random_noise else float(value)

    # Not in cycle: device is ON but idle -> standby
    return standby
