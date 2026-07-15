from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple, Dict

from consumption_profiles import get_device_consumption


@dataclass(frozen=True)
class PredictedSample:
    timestamp: datetime
    value: float


def predict_device_consumption(
    device_name: str,
    device_type: str,
    current_datetime: datetime,
    active_cycles: dict,
    device_state: int = 1,
    *,
    horizon_seconds: int = 300,
    step_seconds: int = 60,
    add_random_noise: bool = False,
) -> List[PredictedSample]:
    """
    Predict (simulate forward) device consumption for the next horizon.

    This is intentionally lightweight and deterministic by default,
    so you can use it both for UI preview and for generating datasets.
    """
    if horizon_seconds <= 0 or step_seconds <= 0:
        return []

    out: List[PredictedSample] = []
    steps = max(1, int(horizon_seconds // step_seconds))

    for i in range(steps + 1):
        ts = current_datetime + timedelta(seconds=i * step_seconds)
        w = get_device_consumption(
            device_name,
            device_type,
            ts,
            active_cycles,
            device_state,
            add_random_noise=add_random_noise,
        )
        out.append(PredictedSample(timestamp=ts, value=float(w)))

    return out


def predict_smart_meter_for_associated_device(
    associated_device_name: str,
    device_type: str,
    current_datetime: datetime,
    active_cycles: dict,
    device_state: int = 1,
    *,
    horizon_seconds: int = 300,
    step_seconds: int = 60,
) -> List[PredictedSample]:
    """Alias for smart-meter sensors: prediction is the device's power draw."""
    return predict_device_consumption(
        associated_device_name,
        device_type,
        current_datetime,
        active_cycles,
        device_state=device_state,
        horizon_seconds=horizon_seconds,
        step_seconds=step_seconds,
        add_random_noise=False,
    )
