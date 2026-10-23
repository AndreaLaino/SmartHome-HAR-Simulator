"""Independently scripted, reproducible source/target recognition examples.

These examples are synthetic experiments, not observations of people. Activity
labels come from a fixed scenario script; the recognizer is never consulted to
create them. The source model knows an Oven and a TV. A target house contains a
Microwave and a PlayStation with physical capabilities but no activity labels.
"""

import copy
import math
import random

from activity_recognition import recognition_settings
from models import Device, Room, Sensor
from recognition_library import house_snapshot
from reference_transfer import default_reference_bindings


GROUND_TRUTH_PROVENANCE = "scripted scenario intent, not recognizer outputs"
SOURCE_HOUSE_ID = "synthetic-source"
TARGET_HOUSE_ID = "synthetic-target"


def _model():
    bindings = {key: value for key, value in default_reference_bindings().items()
                if value["kind"] == "sensor"}
    for key, feature, name, kind, properties, weights in (
            ("source-oven-cooking", "cooking", "A17", "Oven",
             {"heats_food": 1, "food_preparation": 1, "produces_heat": .8, "cooking": 1},
             {"heats_food": .7, "food_preparation": .3}),
            ("source-tv-relaxing", "relaxing", "B42", "TV",
             {"audiovisual_entertainment": 1, "visual_display": 1, "relaxing": 1},
             {"audiovisual_entertainment": .8, "visual_display": .2})):
        bindings[key] = {
            "kind": "device", "feature": feature,
            "reference": {"name": name, "type": kind, "house_id": SOURCE_HOUSE_ID,
                          "properties": properties, "recognition_weight": 1},
            "property_weights": weights, "required_properties": list(weights),
            "minimum_similarity": .7, "strength": 1, "weight": 1,
        }
    config = recognition_settings({"reference_bindings": bindings, "minimum_confidence": .5})
    # The experiment follows the paper's oven-power AND Kitchen-presence rule.
    # This does not alter defaults in existing user scenarios.
    config["require_kitchen_presence"] = True
    return config


def _house(config, target):
    if target:
        house_id, label = TARGET_HOUSE_ID, "Synthetic target: Microwave and PlayStation"
        food = Device("C09", 150, 120, "Microwave", 1100, 0, 1100, 1100,
                      properties={"heats_food": .9, "food_preparation": 1,
                                  "produces_heat": .6, "defrosts_food": 1})
        screen = Device("D81", 570, 150, "PlayStation", 180, 0, 180, 180,
                        properties={"audiovisual_entertainment": .95, "visual_display": 1,
                                    "networked": 1, "interactive_controls": 1})
        distractor = Device("E65", 550, 250, "Hair_Dryer", 1500, 0, 1500, 1500,
                            properties={"produces_heat": 1, "air_flow": 1})
        kitchen = [[0, 0], [320, 0], [320, 300], [0, 300]]
        living = [[350, 0], [730, 0], [730, 300], [350, 300]]
        kitchen_position, living_position = (200, 160), (600, 180)
        meter_names, pir_name = ("Q28", "R73", "S04"), "T59"
    else:
        house_id, label = SOURCE_HOUSE_ID, "Synthetic source: Oven and TV"
        food = Device("A17", 80, 80, "Oven", 2200, 0, 2200, 2200,
                      properties={"heats_food": 1, "food_preparation": 1,
                                  "produces_heat": .8, "cooking": 1})
        screen = Device("B42", 380, 80, "TV", 75, 0, 75, 75,
                        properties={"audiovisual_entertainment": 1, "visual_display": 1,
                                    "relaxing": 1})
        distractor = Device("F33", 400, 150, "Hair_Dryer", 1500, 0, 1500, 1500,
                            properties={"produces_heat": 1, "air_flow": 1})
        kitchen = [[0, 0], [240, 0], [240, 210], [0, 210]]
        living = [[270, 0], [520, 0], [520, 210], [270, 210]]
        kitchen_position, living_position = (110, 110), (410, 110)
        meter_names, pir_name = ("M14", "N07", "P36"), "H52"
    devices = [food, screen, distractor]
    sensors = [Sensor(name, device.x, device.y, "Smart Meter", 0, 4000, 1, 0,
                      consumption=0, associated_device=device.name,
                      properties={"energy": 1}, recognition_profile="local")
               for name, device in zip(meter_names, devices)]
    sensors.append(Sensor(pir_name, *kitchen_position, "PIR", 0, 1, 1, 0,
                          properties={"presence": 1}, recognition_profile="local"))
    for device in devices:
        device.recognition_profile = "local"
    runtime = {"house_id": house_id, "devices": devices, "sensors": sensors,
               "rooms": [Room("kitchen", kitchen, manual_type="Kitchen"),
                         Room("living", living, manual_type="Living room")],
               "walls": [], "doors": [], "points": [],
               "recognition_config": copy.deepcopy(config)}
    return runtime, house_snapshot(runtime, house_id, label), kitchen_position, living_position


def _session(runtime, snapshot, positions, session_index, rng, partition):
    """Render the scenario intent as observations without predicting labels."""
    kitchen_position, living_position = positions
    devices = runtime["devices"]
    sensors = runtime["sensors"]
    elapsed = 0.0
    start_of_day = rng.randint(7 * 60, 18 * 60)
    frames = []

    def emit(intent, labels, duration, *, food=False, screen=False, heat=False,
             kitchen_presence=False, location=None, person_inside=True, standby=False,
             step=None):
        nonlocal elapsed
        step = rng.uniform(.8, 1.8) if step is None else step
        observations = max(1, math.ceil(duration / step))
        interval = duration / observations
        for _ in range(observations):
            elapsed += interval
            active = (food or standby, screen, heat)
            powers = []
            for device, on in zip(devices, active):
                power = (rng.uniform(.05, .35) if standby and device is devices[0]
                         else device.power * rng.uniform(.87, 1.08) if on else 0.0)
                powers.append(round(power, 3))
            states = {sensor.name: {"type": "Smart Meter", "associated_device": device.name,
                                    "state": [int(on)], "consumption": [power]}
                      for sensor, device, on, power in zip(sensors[:3], devices, active, powers)}
            states[sensors[3].name] = {"type": "PIR", "state": [int(kitchen_presence)]}
            frames.append({
                "time_minutes": round(elapsed, 6),
                "time_of_day_minutes": round((start_of_day + elapsed) % 1440, 6),
                "device_states": {device.name: {"state": int(on), "current_consumption": power}
                                  for device, on, power in zip(devices, active, powers)},
                "sensor_states": states, "door_states": [],
                "avatar_position": (list(kitchen_position) if location == "kitchen"
                                    else list(living_position) if location == "living" else None),
                "person_inside": bool(person_inside), "expected_activities": list(labels),
                "scenario_intent": intent, "ground_truth_provenance": GROUND_TRUTH_PROVENANCE,
            })

    # These labels express the experimenter's scripted actions. They are not
    # derived from properties, thresholds, role matching or recognition outputs.
    emit("empty house", [], rng.uniform(3, 5), person_inside=False)
    emit("powered appliance in standby", [], rng.uniform(3, 5), standby=True,
         kitchen_presence=True, location="kitchen")
    emit("generic heat without food preparation", [], rng.uniform(3, 5), heat=True,
         location="living")
    emit("oven preheating unattended while occupant is elsewhere", [], rng.uniform(3, 5),
         food=True, location="living")
    # Positive phases use one common sampling interval and comparable total
    # duration per class. Long away intervals retain temporal context without
    # flooding the benchmark with easy idle observations.
    emit("relaxation before any cooking", ["relaxing"], rng.uniform(12, 16), screen=True,
         location="living", step=.5)
    emit("away between independent actions", [], 62, person_inside=False, step=5)
    emit("preparing food without subsequent entertainment", ["cooking"], rng.uniform(12, 16),
         food=True, kitchen_presence=True, location="kitchen", step=.5)
    emit("away after preparing food", [], 62, person_inside=False, step=5)
    emit("preparing the meal", ["cooking"], rng.uniform(12, 16), food=True,
         kitchen_presence=True, location="kitchen", step=.5)
    emit("moving meal to living room", [], rng.uniform(.5, 1), person_inside=False)
    # Eating is the stated behavioural assumption following cooking, rather than
    # an eating sensor or a direct property attached to the target devices.
    emit("eating the prepared meal while using entertainment", ["eating", "relaxing", "eating while relaxing"],
         rng.uniform(24, 32), screen=True, location="living", step=.5)
    emit("away until the meal context expires", [], 62, person_inside=False, step=5)
    emit("unrelated later entertainment", ["relaxing"], rng.uniform(12, 16), screen=True,
         location="living", step=.5)
    emit("end of session", [], rng.uniform(3, 5), person_inside=False)
    return {"schema_version": 1, "run_id": f"{snapshot['house_id']}-session-{session_index:03d}",
            "session_id": f"{snapshot['house_id']}-session-{session_index:03d}",
            "session_index": session_index, "house_id": snapshot["house_id"],
            "partition": partition, "house": copy.deepcopy(snapshot), "frames": frames,
            "scope": "synthetic", "ground_truth_provenance": GROUND_TRUTH_PROVENANCE}


def build_synthetic_benchmark(*, seed=42, sessions_per_house=20):
    """Build an in-memory controlled transfer benchmark; never write user data.

    Each independent source and target session contains the same scripted action
    categories with independently sampled times and powers. Source sessions may
    be split for training/tuning; every target session remains held out. Seeded
    variation is for reproducibility, not evidence of real-world generalisation.
    """
    if isinstance(sessions_per_house, bool) or not isinstance(sessions_per_house, int) or not 5 <= sessions_per_house <= 500:
        raise ValueError("sessions_per_house must be an integer between 5 and 500.")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer.")
    rng = random.Random(seed)
    config = _model()
    datasets = []
    for target in (False, True):
        runtime, snapshot, kitchen, living = _house(config, target)
        for session in range(sessions_per_house):
            datasets.append(_session(runtime, snapshot, (kitchen, living), session, rng,
                                     "test" if target else "reference"))
    return {"config": config, "datasets": datasets, "source_house_id": SOURCE_HOUSE_ID,
            "scope": "synthetic", "ground_truth_provenance": GROUND_TRUTH_PROVENANCE,
            "description": (
                "Controlled, independently scripted Oven/TV source-house and Microwave/PlayStation "
                "target-house sessions. Target devices have physical capabilities and no activity "
                "labels. Includes idle, standby, unrelated heat, unattended preheating, relaxation "
                "before cooking, cooking, and cooking followed by eating while relaxing. Labels "
                "represent scenario intent; eating after cooking is an explicit behavioural "
                "assumption. These synthetic examples do not measure real-house accuracy.")}
