"""PIR context preserves spatial meaning without IDs, rooms, or activity rules."""

from copy import deepcopy
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from har import storage
from har.catalogue import default_catalogue
from har.features import FeatureEncoder, primitive_names, primitives
from har.learning import evaluate_model, predict_vector, train_comparison, train_source, validate_model
from har.pir_context import validate_context
from har.runtime import capture_frame, current_runtime, process_frame
from house_state import HouseState
from models import Device, Door, Fixture, Sensor, Wall
from utils import PIR_MAX_DISTANCE, PIR_FOV_ANGLE


def scene(*, target=False):
    objects = [Device("food-b" if target else "food-a", 80, 0, "Microwave" if target else "Oven", 1000, 0, 0, 1000),
               Fixture("seat-b" if target else "seat-a", 520, 0, "Chair" if target else "Sofa")]
    sensors = [Sensor("motion-b1" if target else "motion-a1", 0, 0, "PIR", 0, 1, 1, 0, direction=0),
               Sensor("motion-b2" if target else "motion-a2", 600, 0, "PIR", 0, 1, 1, 0, direction=180)]
    polygon = [(-50, -50), (650, -50), (650, 100), (-50, 100)]
    if target:
        for obj in [*objects, *sensors]:
            obj.x, obj.y = 1000 - obj.y, 2000 + obj.x
        for sensor in sensors:
            sensor.direction = (sensor.direction + 90) % 360
        polygon = [(1000 - y, 2000 + x) for x, y in polygon]
    return {"devices": objects, "sensors": sensors, "walls": [], "doors": [],
            "rooms": [SimpleNamespace(room_type="Kitchen", polygon=polygon)]}


def frame(runtime, first=1, second=0, *, time=0, catalogue=None):
    readings = {sensor.name: {"state": [value]} for sensor, value in zip(runtime["sensors"], (first, second))}
    return capture_frame(readings, runtime, time, catalogue or default_catalogue())


def sessions():
    result = []
    for index in range(4):
        target = index == 3
        runtime = scene(target=target)
        rng = random.Random(42 + index)
        observations, now = [], 0
        for _cycle in range(14):
            phases = [(0, 0), (1, 0), (0, 1), (1, 1)]
            rng.shuffle(phases)
            for first, second in phases:
                now += rng.randint(40, 70)
                item = frame(runtime, first, second, time=now)
                item["labels"] = (["preparazione"] if first else []) + (["pausa"] if second else [])
                observations.append(item)
        result.append({"schema_version": 1, "session_id": f"pir-session-{index}",
                       "simulation_session_id": f"pir-simulation-{index}", "house_id": "target" if target else "source",
                       "house_label": "Target" if target else "Source", "partition": "target" if target else "source",
                       "provenance": "scripted-synthetic", "catalogue": default_catalogue(), "frames": observations})
    return result


class PIRContextGeometryTests(unittest.TestCase):
    def channels(self, item, *, catalogue=None, version=2):
        dictionary = catalogue or default_catalogue()
        return dict(zip(primitive_names(dictionary, schema_version=version),
                        primitives(item, dictionary, schema_version=version)))

    def test_same_room_pirs_retain_their_different_object_context(self):
        runtime = scene()
        first, second = frame(runtime, 1, 0), frame(runtime, 0, 1)
        self.assertEqual(primitives(first, default_catalogue(), schema_version=1),
                         primitives(second, default_catalogue(), schema_version=1))
        a, b = self.channels(first), self.channels(second)
        for category, expected in (("Device_Cooking", (1, 0)), ("Device_Seating", (0, 1))):
            key = f"pir_context/{category}/normalized"
            self.assertEqual((a[key], b[key]), expected)
            self.assertEqual(a[f"pir_context/{category}/observed"], 1)
        self.assertEqual(a["pir_context/geometry_observed"], 1)

    def test_renaming_rotating_translating_and_replacing_objects_preserves_meaning(self):
        source, target = frame(scene()), frame(scene(target=True))
        self.assertEqual(primitives(source, default_catalogue()), primitives(target, default_catalogue()))
        self.assertFalse(any("food-a" in name or "motion-a1" in name for name in primitive_names(default_catalogue())))

    def test_range_direction_and_angle_use_the_simulator_field_of_view(self):
        runtime = scene()
        runtime["sensors"] = runtime["sensors"][:1]
        cases = ((100, 0, True), (PIR_MAX_DISTANCE + 1, 0, False), (-30, 0, False), (100, 100, False))
        for x, y, visible in cases:
            runtime["devices"][0].x, runtime["devices"][0].y = x, y
            snapshot = frame(runtime)["pir_context"]
            with self.subTest(x=x, y=y):
                self.assertEqual("Device_Cooking" in snapshot["coverage"]["motion-a1"], visible)
                self.assertEqual(snapshot["max_distance"], PIR_MAX_DISTANCE)
                self.assertEqual(snapshot["fov_angle"], PIR_FOV_ANGLE)
        runtime["devices"][0].x, runtime["devices"][0].y = 0, 100
        runtime["sensors"][0].direction = 90
        self.assertIn("Device_Cooking", frame(runtime)["pir_context"]["coverage"]["motion-a1"])

    def test_walls_and_closed_doors_block_context_but_open_doors_do_not(self):
        runtime = scene()
        runtime["walls"] = [Wall(40, -50, 40, 50)]
        self.assertEqual(frame(runtime)["pir_context"]["coverage"]["motion-a1"], [])
        runtime["walls"] = []
        door = Door(40, -50, 40, 50, "close")
        runtime["doors"] = [door]
        self.assertEqual(frame(runtime)["pir_context"]["coverage"]["motion-a1"], [])
        closed = frame(runtime)
        door.state = "open"
        self.assertEqual(frame(runtime)["pir_context"]["coverage"]["motion-a1"], ["Device_Cooking"])
        self.assertEqual(closed["pir_context"]["coverage"]["motion-a1"], [])

    def test_overlapping_contexts_keep_ambiguity_and_do_not_duplicate_same_category(self):
        runtime = scene()
        runtime["devices"][1].x = 90
        self.assertEqual(frame(runtime)["pir_context"]["coverage"]["motion-a1"], ["Device_Cooking", "Device_Seating"])
        runtime["devices"][1].categories = ["Device_Cooking"]
        for index in range(5):
            runtime["devices"].append(Fixture(f"extra-{index}", 95 + index, 0, "Chair", categories=["Device_Cooking"]))
        runtime["devices"].append(Fixture("at-second-pir", 520, 0, "Chair", categories=["Device_Cooking"]))
        values = self.channels(frame(runtime, 1, 0))
        self.assertEqual(values["pir_context/Device_Cooking/normalized"], .5)

    def test_missing_geometry_old_frames_and_missing_readings_stay_distinct_from_zero(self):
        runtime = scene()
        item = frame(runtime, 0, 0)
        self.assertEqual(self.channels(item)["pir_context/Device_Cooking/observed"], 1)
        item["sensors"][0]["state"] = 1  # Stale cached ON must not replace missing data.
        item["sensor_states"]["motion-a1"]["state"] = []
        values = self.channels(item)
        self.assertEqual(values["pir_context/Device_Cooking/normalized"], 0)
        self.assertEqual(values["pir_context/Device_Cooking/observed"], 0)
        runtime["sensors"][0].direction = None
        self.assertIsNone(frame(runtime)["pir_context"]["coverage"]["motion-a1"])
        runtime.pop("walls")
        self.assertEqual(self.channels(frame(runtime))["pir_context/geometry_observed"], 0)
        incomplete = scene()
        incomplete["devices"][0].x = None
        self.assertEqual(self.channels(frame(incomplete))["pir_context/geometry_observed"], 0)
        item.pop("pir_context")
        self.assertEqual(self.channels(item)["pir_context/recorded"], 0)
        self.assertEqual(self.channels(item)["pir_context/Device_Cooking/available"], 0)

    def test_extra_target_categories_and_legacy_category_spelling_use_frozen_schema(self):
        runtime = scene()
        dictionary = default_catalogue()
        dictionary["device_categories"]["Device_Extra"] = "Target-only capability."
        runtime["devices"][0].categories = ["Device_Cooking", "Device_Extra"]
        item = frame(runtime, catalogue=dictionary)
        self.assertEqual(primitives(item, default_catalogue()), primitives(frame(scene()), default_catalogue()))
        runtime["devices"][0].categories = ["DeviceCooking"]
        runtime["sensors"][0].categories = ["SensorPresence"]
        self.assertEqual(primitives(frame(runtime), default_catalogue()), primitives(frame(scene()), default_catalogue()))

    def test_explicitly_unclassified_objects_and_pirs_add_no_context_features(self):
        runtime = scene()
        runtime["devices"][0].categories = []
        self.assertEqual(frame(runtime)["pir_context"]["coverage"]["motion-a1"], [])
        runtime = scene()
        runtime["sensors"][0].categories = []
        self.assertEqual(self.channels(frame(runtime))["pir_context/Device_Cooking/available"], 0)

    def test_snapshot_validation_rejects_invented_or_incomplete_relations(self):
        item = frame(scene())
        for field, value in (("schema_version", True), ("schema_version", 9), ("max_distance", 0),
                             ("fov_angle", float("inf")), ("coverage", {}),
                             ("coverage", {"motion-a1": ["Device_Bed"], "motion-a2": []}),
                             ("coverage", {"motion-a1": ["Device_Cooking", "Device_Cooking"], "motion-a2": []})):
            changed = deepcopy(item)
            changed["pir_context"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_context(changed, default_catalogue())

    def test_manual_ui_capture_uses_current_geometry(self):
        runtime = scene()
        ctx = SimpleNamespace(_manual_runtime_sources=runtime, load_active=True, rooms=runtime["rooms"], har_config={})
        self.assertIs(current_runtime(ctx)["walls"], runtime["walls"])
        self.assertIs(current_runtime(ctx)["doors"], runtime["doors"])


class PIRContextLearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sessions = sessions()
        cls.model = train_comparison(cls.sessions, "source", window_seconds=30)

    def test_both_algorithms_learn_unmetered_context_in_one_room_and_transfer(self):
        self.assertEqual(self.model["feature_schema_version"], 2)
        self.assertEqual(self.model["pir_context_training_samples"], self.model["training_samples"])
        report = evaluate_model(self.model, self.sessions)
        for algorithm, scores in report["algorithms"].items():
            self.assertGreater(scores["macro_f1"], .95, algorithm)
            self.assertEqual(scores["scope"], "synthetic")
        self.assertTrue(all(sensor["type"] == "PIR" for item in self.sessions for sensor in item["frames"][0]["sensors"]))

    def test_session_snapshot_round_trip_legacy_models_and_live_inference(self):
        legacy = train_source(self.sessions, "source", window_seconds=30, feature_schema_version=1)
        legacy.pop("feature_schema_version")  # Original saved models have no version field.
        self.assertEqual(legacy["pir_context_training_samples"], 0)
        validate_model(legacy)
        with tempfile.TemporaryDirectory() as directory, patch.object(storage, "DATA_DIR", Path(directory)), \
                patch("har.runtime.load_catalogue", return_value=default_catalogue()):
            path = storage.save_session(self.sessions[0])
            reloaded = storage.read_json(path)
            self.assertEqual(reloaded["frames"][0]["pir_context"], self.sessions[0]["frames"][0]["pir_context"])
            storage.save_model(self.model)
            storage.save_model(legacy)
            runtime = scene(target=True)
            runtime["har_config"] = {"house_id": "target", "model_id": self.model["model_id"]}
            state = HouseState()
            readings = {sensor.name: {"state": [value]} for sensor, value in zip(runtime["sensors"], (1, 0))}
            result = process_frame(readings, runtime, state.activity_state(), 0)
            self.assertEqual(result["comparisons"]["random_forest"]["activities"], ["preparazione"])
            self.assertEqual(result["comparisons"]["gradient_boosting"]["activities"], ["preparazione"])
            runtime["har_config"]["model_id"] = legacy["model_id"]
            actual = process_frame(readings, runtime, state.activity_state(), 1)
            expected = predict_vector(legacy, FeatureEncoder(legacy["catalogue"], 30, schema_version=1).update(
                capture_frame(readings, runtime, 1, default_catalogue())))
            self.assertEqual(actual, expected)
            self.assertEqual(state.activity_state()["har_inference"]["encoder"].schema_version, 1)

    def test_labels_hidden_device_state_and_future_context_are_not_features(self):
        original = deepcopy(self.sessions[0]["frames"][0])
        altered = deepcopy(original)
        altered["labels"] = ["another-label"]
        altered["devices"][0].update(state=1, current_consumption=999)
        self.assertEqual(primitives(original, default_catalogue()), primitives(altered, default_catalogue()))
        encoder = FeatureEncoder(default_catalogue(), 30)
        before = encoder.update(original)
        history = deepcopy(encoder.history)
        future = deepcopy(original)
        future["time_seconds"] += 100
        future["pir_context"]["coverage"] = {name: [] for name in future["pir_context"]["coverage"]}
        encoder.update(future)
        fresh = FeatureEncoder(default_catalogue(), 30)
        self.assertEqual(fresh.update(original), before)
        self.assertEqual(fresh.history, history)
