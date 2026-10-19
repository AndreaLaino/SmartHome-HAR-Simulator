import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import read
import recognition_library as store
from activity_recognition import recognize_activities
from app.context import AppContext
from app.io.scenario import _serialize_scenario
from device import get_device_params
from models import Device, Sensor
from recognition_evaluation import (capture_frame, evaluate_datasets, evaluate_library,
                                    label_current_frame, record_trace, transfer_report)


def house(cooking="Oven", entertainment="TV"):
    devices = []
    for name, kind in (("heat appliance", cooking), ("media appliance", entertainment)):
        power = get_device_params(kind)["power"]
        devices.append(Device(name, 50, 50, kind, power, 0, power, power))
    sensors = [Sensor(f"sensor {index}", 50, 50, "Smart Meter", 0, 5000, 1, 0, associated_device=obj.name)
               for index, obj in enumerate(devices)]
    return AppContext(window=None, load_active=True, read_devices=devices, read_sensors=sensors)


class SharedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(store, "LIBRARY_DIR", self.root))
        self.shared = store.ensure_library()

    def connect(self, ctx):
        store.connect_house(ctx)
        return store.context_runtime(ctx)

    def test_library_is_independent_of_house_and_working_directory(self):
        self.assertTrue((self.root / "library.json").is_file())
        for folder in ("houses", "datasets", "reports"):
            self.assertTrue((self.root / folder).is_dir())
        self.assertEqual(store.load_library()["library_id"], self.shared["library_id"])
        self.assertIn("device:Microwave", self.shared["profiles"])

    def test_shared_profile_revision_updates_two_houses_without_overwriting_local_properties(self):
        first, other = house("Microwave"), house("Microwave", "PlayStation")
        self.connect(first)
        runtime = self.connect(other)
        other.read_devices[1].recognition_profile = "local"
        other.read_devices[1].properties = {"working": .9}
        updated = copy.deepcopy(self.shared)
        updated["profiles"]["device:Microwave"]["properties"] = {"cooking": .9, "heating": .6}
        store.save_library(updated, self.shared["revision"])
        for ctx in (first, other):
            store.synchronize_runtime(store.context_runtime(ctx))
            self.assertEqual(ctx.read_devices[0].properties["cooking"], .9)
        self.assertEqual(other.read_devices[1].properties, {"working": .9})
        self.assertEqual(runtime["recognition_library_link"]["revision"], 2)

    def test_any_device_type_can_use_a_shared_property_profile(self):
        ctx = house("Microwave", "PlayStation")
        runtime = self.connect(ctx)
        ctx.read_devices[0].recognition_profile = "device:Oven"
        store.synchronize_runtime(runtime)
        self.assertEqual(ctx.read_devices[0].type, "Microwave")
        self.assertEqual(ctx.read_devices[0].properties, self.shared["profiles"]["device:Oven"]["properties"])

    def test_invalid_binding_leaves_every_object_and_link_unchanged(self):
        ctx = house()
        runtime = self.connect(ctx)
        before = copy.deepcopy(ctx.read_devices[0].properties)
        ctx.read_devices[1].recognition_profile = "device:missing"
        updated = copy.deepcopy(self.shared)
        updated["profiles"]["device:Oven"]["properties"] = {"cooking": .1}
        store.save_library(updated, 1)
        with self.assertRaises(ValueError):
            store.synchronize_runtime(runtime)
        self.assertEqual(ctx.read_devices[0].properties, before)
        self.assertEqual(ctx.recognition_library_link["revision"], 1)

    def test_reference_rules_transfer_and_house_overrides_are_explicit(self):
        target = house("Microwave", "PlayStation")
        runtime = self.connect(target)
        updated = copy.deepcopy(self.shared)
        updated["recognition_config"]["rules"]["cooking"]["threshold"] = .9
        store.save_library(updated, 1)
        effective = store.synchronize_runtime(runtime)
        self.assertEqual(effective["rules"]["cooking"]["threshold"], .9)
        target.recognition_config["rules"] = {"cooking": {"threshold": .7}}
        self.assertEqual(store.synchronize_runtime(runtime)["rules"]["cooking"]["threshold"], .7)
        self.assertEqual(store.load_library()["recognition_config"]["rules"]["cooking"]["threshold"], .9)

    def test_concurrent_or_invalid_shared_writes_preserve_the_previous_file(self):
        changed = store.save_library(self.shared, 1)
        original = (self.root / "library.json").read_bytes()
        with self.assertRaises(ValueError):
            store.save_library(self.shared, 1)
        changed["profiles"]["device:Oven"]["properties"] = {"cooking": float("nan")}
        with self.assertRaises(ValueError):
            store.save_library(changed, 2)
        self.assertEqual((self.root / "library.json").read_bytes(), original)

    def test_disconnect_retains_effective_rules_and_properties_even_without_library(self):
        ctx = house()
        self.connect(ctx)
        before = copy.deepcopy(ctx.read_devices[0].properties)
        (self.root / "library.json").unlink()
        store.connect_house(ctx, enabled=False)
        self.assertFalse(ctx.recognition_library_link["enabled"])
        self.assertEqual(ctx.read_devices[0].properties, before)
        self.assertEqual(ctx.read_devices[0].recognition_profile, "local")
        self.assertIn("sleeping", ctx.recognition_config["rules"])

    def test_shared_links_and_local_profile_overrides_survive_csv_round_trip(self):
        ctx = house()
        self.connect(ctx)
        store.register_house(ctx, "Original")
        ctx.read_devices[1].recognition_profile = "local"
        with ExitStack() as stack:
            for name in ("coordinates", "read_walls", "read_sensors", "read_devices", "read_doors"):
                stack.enter_context(patch.object(read, name, []))
            for name in ("read_room_overrides", "read_recognition_config", "read_recognition_link"):
                stack.enter_context(patch.object(read, name, {}))
            path = self.root / "scenario.csv"
            path.write_text(_serialize_scenario(ctx), encoding="utf-8")
            _, _, sensors, devices, _ = read.read_coordinates_from_file(path)
            self.assertEqual(read.read_recognition_link["house_id"], ctx.recognition_library_link["house_id"])
            self.assertEqual(devices[0].recognition_profile, "device:Oven")
            self.assertEqual(devices[1].recognition_profile, "local")
            self.assertEqual(sensors[0].recognition_profile, "sensor:Smart Meter")

    def test_reference_and_target_with_different_devices_have_equal_activity_coverage(self):
        reference, target = house(), house("Microwave", "PlayStation")
        source = store.register_house(reference, "Reference", reference=True)
        destination = store.register_house(target, "Target", new=True)
        report = transfer_report(source, destination, self.shared["recognition_config"])
        combined = report["activities"]["eating while relaxing"]
        self.assertEqual(combined["property_coverage"], 1)
        self.assertTrue(combined["transferable"])
        self.assertTrue(combined["supported_in_reference"])
        self.assertEqual([item["property_match"] for item in report["device_equivalents"]], [1, 1])
        self.assertNotEqual(source["house_id"], destination["house_id"])

    def test_missing_observability_blocks_transfer_even_when_device_properties_match(self):
        source = store.register_house(house(), "Reference", reference=True)
        target = house("Microwave", "PlayStation")
        target.read_sensors.clear()
        report = transfer_report(source, store.register_house(target, "No sensors"), self.shared["recognition_config"])
        result = report["activities"]["eating while relaxing"]
        self.assertFalse(result["transferable"])
        self.assertIn("cooking_then_relaxing", result["missing"])

    def test_transfer_report_respects_confidence_gate_and_zero_weight_objects(self):
        source = store.register_house(house(), "Reference", reference=True)
        target = store.register_house(house("Microwave", "PlayStation"), "Target")
        source["devices"][0]["properties"] = {"cooking": 0}
        config = copy.deepcopy(self.shared["recognition_config"])
        config["minimum_confidence"] = .95
        report = transfer_report(source, target, config)
        combined = report["activities"]["eating while relaxing"]
        self.assertEqual(combined["property_coverage"], 1)
        self.assertEqual(combined["maximum_confidence"], .85)
        self.assertFalse(combined["transferable"])
        self.assertFalse(combined["supported_in_reference"])
        self.assertEqual(len(report["device_equivalents"]), 1)

    def test_confidence_is_distinct_from_support_and_can_abstain(self):
        ctx = house()
        runtime = store.context_runtime(ctx)
        states = {"sensor 0": {"state": [1]}, "sensor 1": {"state": [0]}}
        state = {}
        recognize_activities(states, runtime, state, 0)
        cooking = state["recognition_results"]["cooking"]
        self.assertAlmostEqual(cooking["score"], .8)
        self.assertAlmostEqual(cooking["confidence"], .64)
        previous_signature = state["recognition_model_signature"]
        ctx.recognition_config["minimum_confidence"] = .8
        self.assertNotIn("cooking", recognize_activities(states, runtime, state, 1))
        self.assertTrue(state["recognition_results"]["cooking"]["abstained"])
        self.assertNotEqual(state["recognition_model_signature"], previous_signature)
        self.assertEqual(state["semantic"]["started"]["cooking"], 1)

    def test_no_labelled_data_has_no_fabricated_accuracy(self):
        result = evaluate_library()
        self.assertIsNone(result["exact_match_accuracy"])
        self.assertIsNone(result["micro_f1"])
        self.assertEqual(result["labelled_samples"], 0)

    def test_wrong_labels_reduce_accuracy_and_negative_only_data_has_no_fake_f1(self):
        ctx = house()
        snapshot = store.register_house(ctx, "Target")
        runtime = store.context_runtime(ctx)
        frame = capture_frame(runtime, {"sensor 0": {"state": [1]}}, 0)
        frame["expected_activities"] = ["working"]
        dataset = {"house_id": snapshot["house_id"], "house": snapshot, "partition": "test", "run_id": "wrong", "frames": [frame]}
        result = evaluate_datasets([dataset], self.shared["recognition_config"])
        self.assertEqual(result["exact_match_accuracy"], 0)
        self.assertEqual(result["per_activity"]["cooking"]["fp"], 1)
        self.assertEqual(result["per_activity"]["working"]["fn"], 1)
        self.assertEqual(result["micro_f1"], 0)
        frame["sensor_states"] = {}
        frame["expected_activities"] = []
        result = evaluate_datasets([dataset], self.shared["recognition_config"])
        self.assertEqual(result["exact_match_accuracy"], 1)
        self.assertIsNone(result["micro_f1"])

    def test_evaluation_replays_current_shared_profiles_on_frozen_house_data(self):
        ctx = house("Microwave", "PlayStation")
        runtime = self.connect(ctx)
        snapshot = store.register_house(ctx, "Target")
        frame = capture_frame(runtime, {"sensor 0": {"state": [1]}}, 0)
        frame["expected_activities"] = ["cooking"]
        dataset = {"house_id": snapshot["house_id"], "house": snapshot, "partition": "test", "run_id": "shared", "frames": [frame]}
        result = evaluate_datasets([dataset], self.shared["recognition_config"], shared_library=self.shared)
        self.assertEqual(result["exact_match_accuracy"], 1)
        updated = copy.deepcopy(self.shared)
        updated["profiles"]["device:Microwave"]["properties"]["cooking"] = .2
        result = evaluate_datasets([dataset], updated["recognition_config"], shared_library=updated)
        self.assertEqual(result["exact_match_accuracy"], 0)
        self.assertEqual(snapshot["devices"][0]["properties"]["cooking"], 1)

    def test_held_out_accuracy_excludes_reference_and_unlabelled_temporal_context(self):
        reference = store.register_house(house(), "Reference", reference=True)
        target_ctx = house("Microwave", "PlayStation")
        target = store.register_house(target_ctx, "Target")
        runtime = store.context_runtime(target_ctx)
        cooking = capture_frame(runtime, {"sensor 0": {"state": [1]}, "sensor 1": {"state": [0]}}, 0)
        relaxing = capture_frame(runtime, {"sensor 0": {"state": [0]}, "sensor 1": {"state": [1]}}, 5)
        relaxing["expected_activities"] = ["eating while relaxing"]
        dataset = {"house_id": target["house_id"], "house": target, "partition": "test", "run_id": "target", "frames": [cooking, relaxing]}
        leaked_reference = {**copy.deepcopy(dataset), "house_id": reference["house_id"], "run_id": "reference",
                            "frames": [{**relaxing, "expected_activities": ["intentionally wrong"]}]}
        result = evaluate_datasets([dataset, leaked_reference], self.shared["recognition_config"], reference_house_id=reference["house_id"])
        self.assertEqual(result["exact_match_accuracy"], 1)
        self.assertEqual(result["labelled_samples"], 1)
        self.assertEqual(result["test_houses"], 1)
        self.assertEqual(result["excluded_reference_runs"], ["reference"])
        self.assertEqual(result["per_activity"]["eating while relaxing"]["support"], 1)

    def test_ground_truth_is_human_supplied_and_is_not_backfilled_from_predictions(self):
        ctx = house("Microwave", "PlayStation")
        store.register_house(ctx, "Target")
        runtime = store.context_runtime(ctx)
        state = ctx.house_state.activity_state()
        record_trace(state, capture_frame(runtime, {"sensor 0": {"state": [1]}}, 0))
        record_trace(state, capture_frame(runtime, {"sensor 0": {"state": [0]}, "sensor 1": {"state": [1]}}, 5))
        path = label_current_frame(ctx, ["eating while relaxing"])
        dataset = json.loads(path.read_text())
        self.assertNotIn("expected_activities", dataset["frames"][0])
        self.assertEqual(dataset["frames"][1]["expected_activities"], ["eating while relaxing"])
        record_trace(state, capture_frame(runtime, {}, 6))
        label_current_frame(ctx, [])
        dataset = json.loads(path.read_text())
        self.assertEqual(dataset["frames"][-1]["expected_activities"], [])
        self.assertEqual(evaluate_library()["labelled_samples"], 2)


if __name__ == "__main__":
    unittest.main()
