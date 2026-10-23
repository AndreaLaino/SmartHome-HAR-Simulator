import copy
import json
import unittest
from collections import Counter
from unittest.mock import patch

from har_benchmark import (GROUND_TRUTH_PROVENANCE, SOURCE_HOUSE_ID,
                           build_synthetic_benchmark)
from recognition_library import runtime_from_snapshot
from reference_transfer import match_reference_roles


class SyntheticHarBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.benchmark = build_synthetic_benchmark(seed=17, sessions_per_house=5)

    def test_reproducible_json_data_without_global_files_or_predicted_labels(self):
        with patch("recognition_library.atomic_json", side_effect=AssertionError("must not write")), \
                patch("recognition_library.load_library", side_effect=AssertionError("must not read user model")), \
                patch("activity_recognition.recognize_activities", side_effect=AssertionError("labels must be independent")), \
                patch("activity_recognition.collect_evidence", side_effect=AssertionError("labels must be independent")):
            repeated = build_synthetic_benchmark(seed=17, sessions_per_house=5)
        self.assertEqual(repeated, self.benchmark)
        self.assertEqual(json.loads(json.dumps(repeated)), repeated)

    def test_only_source_oven_and_tv_have_known_activity_associations(self):
        config = self.benchmark["config"]
        devices = [binding for binding in config["reference_bindings"].values()
                   if binding["kind"] == "device"]
        self.assertEqual({(binding["reference"]["type"], binding["feature"]) for binding in devices},
                         {("Oven", "cooking"), ("TV", "relaxing")})
        self.assertTrue(all(binding["reference"]["house_id"] == SOURCE_HOUSE_ID for binding in devices))
        self.assertTrue(config["require_kitchen_presence"])
        self.assertEqual(config["minimum_confidence"], .5)

    def test_source_target_partitions_are_disjoint_independent_sessions(self):
        datasets = self.benchmark["datasets"]
        self.assertEqual(len(datasets), 10)
        self.assertEqual(len({item["run_id"] for item in datasets}), 10)
        source = [item for item in datasets if item["partition"] == "reference"]
        target = [item for item in datasets if item["partition"] == "test"]
        self.assertEqual(len(source), 5)
        self.assertEqual(len(target), 5)
        self.assertEqual({item["house_id"] for item in source}, {self.benchmark["source_house_id"]})
        self.assertFalse({item["house_id"] for item in source} & {item["house_id"] for item in target})
        for dataset in datasets:
            self.assertLess(dataset["frames"][0]["time_minutes"], 5)
            self.assertEqual(dataset["frames"][0]["expected_activities"], [])
            self.assertLess(len(dataset["frames"]), 350)
            labels = {tuple(frame["expected_activities"]) for frame in dataset["frames"]}
            self.assertEqual(labels, {(), ("cooking",), ("relaxing",),
                                      ("eating", "relaxing", "eating while relaxing")})
            counts = Counter(tuple(frame["expected_activities"]) for frame in dataset["frames"])
            self.assertLess(max(counts.values()) / min(counts.values()), 1.5)

    def test_target_uses_physical_capabilities_without_activity_label_leakage(self):
        source = self.benchmark["datasets"][0]["house"]
        target = self.benchmark["datasets"][-1]["house"]
        self.assertEqual({item["type"] for item in target["devices"]},
                         {"Microwave", "PlayStation", "Hair_Dryer"})
        activity_names = {"cooking", "relaxing", "eating", "eating_while_relaxing", "gaming", "grooming"}
        for device in target["devices"]:
            self.assertFalse(activity_names & set(device["properties"]))
        self.assertFalse({item["name"] for item in source["devices"]}
                         & {item["name"] for item in target["devices"]})
        self.assertFalse({item["name"] for item in source["sensors"]}
                         & {item["name"] for item in target["sensors"]})
        runtime = runtime_from_snapshot(target)
        matches = {device.type: match_reference_roles(device, "device", self.benchmark["config"]["reference_bindings"],
                                                      house_id=target["house_id"])
                   for device in runtime["devices"]}
        self.assertEqual(matches["Microwave"][0]["feature"], "cooking")
        self.assertEqual(matches["PlayStation"][0]["feature"], "relaxing")
        self.assertEqual(matches["Hair_Dryer"], [])
        self.assertTrue(all(match["relation"] == "substitution" for name in ("Microwave", "PlayStation")
                            for match in matches[name]))
        self.assertLess(matches["Microwave"][0]["similarity"], 1)

    def test_frames_represent_matched_power_and_explicit_context(self):
        for dataset in self.benchmark["datasets"]:
            house = dataset["house"]
            meters = [sensor for sensor in house["sensors"] if sensor["type"] == "Smart Meter"]
            times = [frame["time_minutes"] for frame in dataset["frames"]]
            self.assertEqual(times, sorted(set(times)))
            for frame in dataset["frames"]:
                self.assertGreaterEqual(frame["time_of_day_minutes"], 0)
                self.assertLess(frame["time_of_day_minutes"], 1440)
                for meter in meters:
                    observed = frame["sensor_states"][meter["name"]]
                    device = frame["device_states"][meter["associated_device"]]
                    self.assertEqual(observed["state"], [device["state"]])
                    self.assertEqual(observed["consumption"], [device["current_consumption"]])
                    if not device["state"]:
                        self.assertEqual(device["current_consumption"], 0)
                if frame["expected_activities"] == ["cooking"]:
                    self.assertTrue(frame["person_inside"])
                    pir = next(sensor for sensor in house["sensors"] if sensor["type"] == "PIR")
                    self.assertEqual(frame["sensor_states"][pir["name"]]["state"], [1])

    def test_negative_examples_and_sequence_intents_are_scripted_explicitly(self):
        for dataset in self.benchmark["datasets"]:
            frames = dataset["frames"]
            standby = [frame for frame in frames if frame["scenario_intent"] == "powered appliance in standby"]
            self.assertTrue(standby)
            for frame in standby:
                self.assertEqual(frame["expected_activities"], [])
                on = [device for device in frame["device_states"].values() if device["state"]]
                self.assertEqual(len(on), 1)
                self.assertGreater(on[0]["current_consumption"], 0)
                self.assertLess(on[0]["current_consumption"], .5)
                self.assertTrue(any(data["state"] == [1] for data in frame["sensor_states"].values()
                                    if data["type"] == "PIR"))
            unattended = [frame for frame in frames if frame["scenario_intent"].startswith("oven preheating")]
            self.assertTrue(unattended)
            for frame in unattended:
                self.assertEqual(frame["expected_activities"], [])
                self.assertTrue(frame["person_inside"])
                self.assertIsNotNone(frame["avatar_position"])
                self.assertTrue(all(data["state"] == [0] for data in frame["sensor_states"].values()
                                    if data["type"] == "PIR"))
            before = next(index for index, frame in enumerate(frames)
                          if frame["scenario_intent"] == "relaxation before any cooking")
            first_cook = next(index for index, frame in enumerate(frames)
                              if frame["expected_activities"] == ["cooking"])
            first_meal = next(index for index, frame in enumerate(frames)
                              if "eating while relaxing" in frame["expected_activities"])
            self.assertLess(before, first_cook)
            self.assertLess(first_cook, first_meal)
            self.assertFalse(any("eating while relaxing" in frame["expected_activities"] for frame in frames[:first_cook]))

    def test_standby_with_kitchen_presence_cannot_bypass_meter_threshold(self):
        from har_features import extract_feature_rows

        for dataset in (self.benchmark["datasets"][0], self.benchmark["datasets"][-1]):
            extracted = extract_feature_rows(dataset, self.benchmark["config"], mode="reference")
            kitchen_index = extracted["feature_names"].index("role:kitchen_presence:latest")
            cooking_index = extracted["feature_names"].index("role:cooking:latest")
            standby_rows = [(frame, row) for frame, row in zip(dataset["frames"], extracted["rows"])
                            if frame["scenario_intent"] == "powered appliance in standby"]
            self.assertTrue(standby_rows)
            for frame, row in standby_rows:
                self.assertEqual(row["rule_prediction"], [])
                self.assertEqual(row["features"][kitchen_index], 1)
                self.assertEqual(row["features"][cooking_index], 0)

    def test_properties_and_labels_are_isolated_between_sessions_and_returned_model(self):
        data = copy.deepcopy(self.benchmark)
        data["datasets"][0]["house"]["devices"][0]["properties"]["heats_food"] = 0
        self.assertEqual(data["datasets"][1]["house"]["devices"][0]["properties"]["heats_food"], 1)
        binding = data["config"]["reference_bindings"]["source-oven-cooking"]
        self.assertEqual(binding["reference"]["properties"]["heats_food"], 1)
        data["datasets"][0]["frames"][0]["expected_activities"].append("invented")
        self.assertNotIn("invented", data["datasets"][0]["frames"][1]["expected_activities"])

    def test_synthetic_scope_and_label_provenance_are_unambiguous(self):
        self.assertEqual(self.benchmark["scope"], "synthetic")
        self.assertEqual(self.benchmark["ground_truth_provenance"], GROUND_TRUTH_PROVENANCE)
        self.assertIn("do not measure real-house accuracy", self.benchmark["description"])
        for dataset in self.benchmark["datasets"]:
            self.assertEqual(dataset["scope"], "synthetic")
            self.assertEqual(dataset["ground_truth_provenance"], GROUND_TRUTH_PROVENANCE)
            self.assertTrue(all(frame["ground_truth_provenance"] == GROUND_TRUTH_PROVENANCE
                                for frame in dataset["frames"]))

    def test_seed_changes_observations_without_changing_target_model(self):
        other = build_synthetic_benchmark(seed=18, sessions_per_house=5)
        self.assertEqual(other["config"], self.benchmark["config"])
        self.assertEqual(other["datasets"][-1]["house"], self.benchmark["datasets"][-1]["house"])
        self.assertNotEqual(other["datasets"][-1]["frames"], self.benchmark["datasets"][-1]["frames"])

    def test_invalid_session_counts_and_seeds_are_rejected(self):
        for count in (0, 4, 501, -1, 5.0, "5", True, None):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "integer between 5 and 500"):
                build_synthetic_benchmark(sessions_per_house=count)
        for seed in (True, None, 1.5, "42"):
            with self.subTest(seed=seed), self.assertRaisesRegex(ValueError, "seed must be an integer"):
                build_synthetic_benchmark(seed=seed, sessions_per_house=5)


if __name__ == "__main__":
    unittest.main()
