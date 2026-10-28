"""Reproducible synthetic source/target observations for trying learned HAR.

Labels describe the scripted scenario intent. They are never produced by the
recognizer and these data do not measure recognition of real human behaviour.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import random

from har.catalogue import default_catalogue


def _frame(rng, target, now, *, cooking=False, relaxing=False, unattended=False, standby=False, labels=None):
    food_name, media_name = ("target_microwave", "target_console") if target else ("source_oven", "source_tv")
    food_type, media_type = ("Microwave", "PlayStation") if target else ("Oven", "TV")
    food_rating, media_rating = (1100, 180) if target else (2000, 140)
    food_on = cooking or unattended or standby
    food_power = food_rating * (rng.uniform(.7, .95) if cooking or unattended else .003 if standby else 0)
    media_power = media_rating * rng.uniform(.7, .95) if relaxing else 0
    kitchen_presence = int(cooking or standby or (not food_on and rng.random() < .4))
    living_presence = int(relaxing or rng.random() < .3)
    prefix = "target" if target else "source"
    devices = []
    for name, kind, x, rated, state, watts, categories in (
            (food_name, food_type, 90, food_rating, food_on, food_power,
             ["Device_Cooking", "Device_Defrosting"] if target else ["Device_Cooking"]),
            (media_name, media_type, 390, media_rating, relaxing, media_power,
             ["Device_Entertainment", "Device_Interactive"] if target else ["Device_Entertainment"])):
        devices.append({"name": name, "type": kind, "x": x, "y": 80, "state": int(state),
                        "power": rated, "min_consumption": 0, "max_consumption": rated,
                        "current_consumption": watts, "categories": categories})
    sensors, observations = [], {}
    for device, location in zip(devices, ("kitchen", "living")):
        name = f"{prefix}_{location}_power"
        sensors.append({"name": name, "type": "Smart Meter", "x": device["x"], "y": 80,
                        "min_val": 0, "max_val": device["power"], "state": device["current_consumption"],
                        "consumption": device["current_consumption"], "associated_device": device["name"],
                        "categories": ["Sensor_Power"]})
        observations[name] = {"state": [device["current_consumption"]], "consumption": [device["current_consumption"]]}
    for location, x, value in (("kitchen", 100, kitchen_presence), ("living", 400, living_presence)):
        name = f"{prefix}_{location}_motion"
        sensors.append({"name": name, "type": "PIR", "x": x, "y": 100, "min_val": 0, "max_val": 1,
                        "state": value, "consumption": None, "associated_device": None,
                        "categories": ["Sensor_Presence"]})
        observations[name] = {"state": [value]}
    return {"time_seconds": now, "devices": devices, "sensors": sensors, "sensor_states": observations,
            "rooms": [{"room_type": "Kitchen", "polygon": [[0, 0], [250, 0], [250, 250], [0, 250]]},
                      {"room_type": "Living room", "polygon": [[300, 0], [550, 0], [550, 250], [300, 250]]}],
            "labels": labels}


def demo_sessions(sessions_per_house=5, seed=42):
    """Stable IDs make re-adding this explicit demo safe and reproducible."""
    if not isinstance(sessions_per_house, int) or sessions_per_house < 3:
        raise ValueError("The demo needs at least three independent sessions per house.")
    result = []
    for target in (False, True):
        house_id = "demo-target" if target else "demo-source"
        dictionary = default_catalogue()
        if target:
            dictionary["device_categories"].update(Device_Defrosting="Equipment capable of defrosting food.",
                                                    Device_Interactive="Equipment with interactive controls.")
        for index in range(sessions_per_house):
            rng = random.Random(seed + index * 101 + (10000 if target else 0))
            phases = [(False, False, False, False), (True, False, False, False),
                      (False, True, False, False), (False, False, True, False),
                      (False, False, False, True), (True, True, False, False)] * 2
            rng.shuffle(phases)
            frames, now = [], 0.0
            for cooking, relaxing, unattended, standby in phases:
                labels = (["cooking"] if cooking else []) + (["relaxing"] if relaxing else [])
                for tick in range(rng.randint(10, 15)):
                    now += rng.uniform(25, 40)
                    frames.append(_frame(rng, target, now, cooking=cooking, relaxing=relaxing,
                                         unattended=unattended, standby=standby,
                                         labels=labels if tick >= 2 else None))
            result.append({"schema_version": 1, "session_id": f"{house_id}-{seed}-{index}",
                           "simulation_session_id": f"{house_id}-sim-{seed}-{index}",
                           "house_id": house_id, "house_label": "Demo: Microwave / PlayStation" if target else "Demo: Oven / TV",
                           "partition": "target" if target else "source", "provenance": "scripted-synthetic",
                           "catalogue": copy.deepcopy(dictionary), "frames": frames})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from har.learning import evaluate_model, train_source
    from har.storage import atomic_json
    sessions = demo_sessions(args.sessions, args.seed)
    model = train_source(sessions, "demo-source", seed=args.seed)
    report = evaluate_model(model, sessions)
    args.output.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output / "sessions.json", {"sessions": sessions})
    atomic_json(args.output / "model.json", model)
    atomic_json(args.output / "report.json", report)
    print(json.dumps({"scope": "scripted-synthetic", "source_test_accuracy": model["source_test_metrics"]["exact_match_accuracy"],
                      "target_accuracy": report["exact_match_accuracy"], "target_micro_f1": report["micro_f1"],
                      "target_labelled_samples": report["labelled_samples"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
