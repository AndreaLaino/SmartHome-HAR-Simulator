import copy
import unittest
from unittest.mock import patch

from har_features import extract_feature_rows
from models import Device, Room, Sensor
from recognition_evaluation import capture_frame
from recognition_library import house_snapshot
import recognition_library as library


def configuration():
    return {"minimum_confidence": .5, "reference_bindings": {
        "oven cooks": {
            "feature": "cooking", "kind": "device",
            "reference": {"name": "source oven", "type": "Oven", "properties": {"heats_food": 1}},
            "property_weights": {"heats_food": 1}, "required_properties": ["heats_food"],
            "minimum_similarity": .5, "strength": 1, "weight": 1,
        },
        "PIR presence": {
            "feature": "presence", "kind": "sensor",
            "reference": {"name": "source PIR", "type": "PIR", "properties": {"presence": 1}},
            "property_weights": {"presence": 1}, "required_properties": ["presence"],
            "minimum_similarity": .5, "strength": 1, "weight": 1,
        },
    }}


def dataset(*, device_name="target microwave", sensor_name="target meter", properties=None):
    obj = Device(device_name, 20, 20, "Microwave", 1000, 0, 0, 1000,
                 properties={"heats_food": 1} if properties is None else properties)
    meter = Sensor(sensor_name, 20, 20, "Smart Meter", 0, 3000, 1, 0, associated_device=obj.name)
    pir = Sensor("presence", 50, 50, "PIR", 0, 1, 1, 0)
    room = Room("kitchen", [(0, 0), (100, 0), (100, 100), (0, 100)], manual_type="Kitchen")
    runtime = {"devices": [obj], "sensors": [meter, pir], "rooms": [room], "recognition_config": configuration()}
    frames = []
    for time, power, present in ((0, 1000, 1), (5, 0, 0), (15, 500, 1)):
        frame = capture_frame(runtime, {sensor_name: {"state": [int(power > 0)], "consumption": [power]},
                                        "presence": {"state": [present]}}, time)
        frame["expected_activities"] = ["cooking"] if power else []
        frames.append(frame)
    return {"house_id": "target", "house": house_snapshot(runtime, "target", "Target"), "frames": frames}


class CausalFeatureTests(unittest.TestCase):
    def value(self, result, row, name):
        return result["rows"][row]["features"][result["feature_names"].index(name)]

    def test_target_labels_never_enter_features_or_predictions(self):
        first = dataset()
        second = copy.deepcopy(first)
        for frame in second["frames"]:
            frame["expected_activities"] = ["invented future class", "sleeping"]
        self.assertEqual(extract_feature_rows(first, configuration()), extract_feature_rows(second, configuration()))

    def test_future_frames_do_not_change_any_earlier_row(self):
        full = dataset()
        prefix = {**full, "frames": full["frames"][:2]}
        first = extract_feature_rows(prefix, configuration())
        longer = extract_feature_rows(full, configuration())
        self.assertEqual(first["rows"], longer["rows"][:2])

    def test_reference_deletion_removes_semantic_cooking_evidence(self):
        data = dataset()
        present = extract_feature_rows(data, configuration())
        removed = configuration()
        del removed["reference_bindings"]["oven cooks"]
        absent = extract_feature_rows(data, removed)
        self.assertEqual(self.value(present, 0, "role:cooking:latest"), 1)
        self.assertNotIn("role:cooking:latest", absent["feature_names"])
        self.assertNotIn("cooking", absent["rows"][0]["rule_prediction"])

    def test_extra_properties_and_renamed_devices_preserve_semantic_features(self):
        first = extract_feature_rows(dataset(), configuration())
        other = extract_feature_rows(dataset(device_name="brand new name", sensor_name="unseen meter",
                                             properties={"heats_food": 1, "irrelevant": .7}), configuration())
        self.assertEqual(first["feature_names"], other["feature_names"])
        self.assertEqual([row["features"] for row in first["rows"]], [row["features"] for row in other["rows"]])
        self.assertEqual([row["rule_prediction"] for row in first["rows"]], [row["rule_prediction"] for row in other["rows"]])

    def test_direct_property_baseline_does_not_infer_capability_associations(self):
        data = dataset()
        direct = extract_feature_rows(data, configuration(), mode="direct")
        self.assertEqual(self.value(direct, 0, "role:cooking:latest"), 0)
        data = dataset(properties={"cooking": 1, "heats_food": 1})
        direct = extract_feature_rows(data, configuration(), mode="direct")
        self.assertEqual(self.value(direct, 0, "role:cooking:latest"), 1)
        self.assertEqual(direct["rows"][0]["reference_matches"], [])

    def test_unlabelled_context_is_processed_but_does_not_become_a_feature(self):
        data = dataset()
        del data["frames"][0]["expected_activities"]
        result = extract_feature_rows(data, configuration())
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(self.value(result, 1, "role:cooking:since_active_minutes"), 5)
        self.assertFalse(any("expected" in name or "prediction" in name or "confidence" in name for name in result["feature_names"]))

    def test_new_run_has_no_previous_cooking_memory(self):
        extract_feature_rows(dataset(), configuration())
        fresh = dataset()
        fresh["frames"] = fresh["frames"][1:2]
        result = extract_feature_rows(fresh, configuration())
        self.assertEqual(self.value(result, 0, "role:cooking:since_active_minutes"), 61)
        self.assertEqual(self.value(result, 0, "elapsed_window_minutes"), 0)

    def test_snapshot_final_state_does_not_leak_into_initial_observations(self):
        data = dataset()
        data["house"]["devices"][0]["state"] = 1
        data["house"]["devices"][0]["current_consumption"] = 1000
        data["house"]["sensors"][0]["state"] = 1
        data["frames"] = [{"time_minutes": 0, "sensor_states": {}, "device_states": {}}]
        result = extract_feature_rows(data, configuration(), strict_cooking=False)
        self.assertEqual(self.value(result, 0, "role:cooking:latest"), 0)

    def test_time_weighted_window_statistics_use_only_past_duration(self):
        result = extract_feature_rows(dataset(), configuration(), window_minutes=10)
        self.assertEqual(self.value(result, 0, "elapsed_window_minutes"), 0)
        self.assertEqual(self.value(result, 1, "role:cooking:latest"), 0)
        self.assertEqual(self.value(result, 1, "role:cooking:mean"), 1)
        self.assertEqual(self.value(result, 1, "role:cooking:changes"), 1)
        self.assertEqual(self.value(result, 2, "elapsed_window_minutes"), 10)
        self.assertEqual(self.value(result, 2, "role:cooking:mean"), 0)

    def test_snapshot_final_door_state_does_not_leak_through_partial_observations(self):
        data = dataset()
        # Without a meter, the appliance needs nearby PIR evidence and an
        # unobstructed path. Only an explicitly observed open door may allow it.
        data["house"]["sensors"] = [sensor for sensor in data["house"]["sensors"] if sensor["type"] == "PIR"]
        data["house"]["doors"] = [{"x1": 35, "y1": 0, "x2": 35, "y2": 100, "state": "open"}]
        data["frames"] = [
            {"time_minutes": 0, "sensor_states": {"presence": {"state": [1]}},
             "device_states": {"target microwave": {"state": 1, "current_consumption": 1000}}},
            {"time_minutes": 1, "sensor_states": {"presence": {"state": [1]}}, "door_states": ["open"]},
        ]
        opened = extract_feature_rows(data, configuration())
        data["house"]["doors"][0]["state"] = "close"
        closed = extract_feature_rows(data, configuration())
        self.assertEqual(opened, closed)
        self.assertEqual(self.value(opened, 0, "role:cooking:latest"), 0)
        self.assertEqual(self.value(opened, 1, "role:cooking:latest"), 1)

    def test_time_of_day_is_explicit_and_never_inferred_from_elapsed_time(self):
        data = dataset()
        with self.assertRaisesRegex(ValueError, "explicit"):
            extract_feature_rows(data, configuration(), use_time_of_day=True)
        for frame in data["frames"]:
            frame["time_of_day_minutes"] = 360
        result = extract_feature_rows(data, configuration(), use_time_of_day=True)
        self.assertAlmostEqual(self.value(result, 0, "time_of_day_sin"), 1)
        self.assertAlmostEqual(self.value(result, 0, "time_of_day_cos"), 0)
        for frame in data["frames"]:
            del frame["time_of_day_minutes"]
            frame["simulated_time"] = "2026-10-20T18:00:00"
        result = extract_feature_rows(data, configuration(), use_time_of_day=True)
        self.assertAlmostEqual(self.value(result, 0, "time_of_day_sin"), -1)

    def test_invalid_windows_and_duplicate_frame_times_are_rejected(self):
        for window in (0, -1, float("nan"), float("inf")):
            with self.subTest(window=window), self.assertRaises(ValueError):
                extract_feature_rows(dataset(), configuration(), window_minutes=window)
        data = dataset()
        data["frames"].append(copy.deepcopy(data["frames"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            extract_feature_rows(data, configuration())

    def test_frozen_profiles_apply_once_without_live_library_reads_or_mutation(self):
        data = dataset()
        frozen = library.default_library()
        frozen["profiles"]["device:Microwave"]["properties"] = {"heats_food": .7}
        data["house"]["devices"][0]["recognition_profile"] = "device:Microwave"
        data["house"]["library_link"] = {"enabled": True, "library_id": frozen["library_id"]}
        original_data, original_model = copy.deepcopy(data), copy.deepcopy(frozen)
        with patch.object(library, "load_library", side_effect=AssertionError("Live library read")), \
                patch.object(library, "synchronize_runtime", wraps=library.synchronize_runtime) as synchronize:
            result = extract_feature_rows(data, configuration(), profile_library=frozen)
        # Synchronize is also called by the engine, but detached calls never
        # reload profiles; only its first call has a supplied frozen catalogue.
        self.assertEqual(sum(call.args[1:] == (frozen,) for call in synchronize.call_args_list), 1)
        self.assertAlmostEqual(self.value(result, 0, "role:cooking:latest"), .7)
        self.assertEqual(data, original_data)
        self.assertEqual(frozen, original_model)

    def test_house_rule_overrides_do_not_replace_supplied_frozen_rules(self):
        data = dataset()
        data["house"]["recognition_config"]["reference_bindings"] = {}
        result = extract_feature_rows(data, configuration())
        self.assertEqual(self.value(result, 0, "role:cooking:latest"), 1)
        self.assertIn("cooking", result["rows"][0]["rule_prediction"])

    def test_abstention_metadata_never_changes_observation_feature_vector(self):
        data = dataset()
        data["house"]["devices"][0]["properties"] = {"heats_food": .7}
        default = extract_feature_rows(data, configuration())
        strict_confidence = configuration()
        strict_confidence["minimum_confidence"] = .9
        conservative = extract_feature_rows(data, strict_confidence)
        self.assertEqual([row["features"] for row in default["rows"]], [row["features"] for row in conservative["rows"]])
        self.assertFalse(default["rows"][0]["abstained"])
        self.assertTrue(conservative["rows"][0]["abstained"])
        self.assertNotIn("cooking", conservative["rows"][0]["rule_prediction"])

    def test_raw_source_schema_uses_zero_for_unseen_target_object_identities(self):
        source = extract_feature_rows(dataset(), configuration(), mode="raw")
        target = extract_feature_rows(dataset(device_name="unseen appliance", sensor_name="unseen meter"),
                                      configuration(), mode="raw", raw_schema=source["raw_schema"])
        self.assertEqual(source["feature_names"], target["feature_names"])
        columns = [index for index, name in enumerate(target["feature_names"])
                   if name.startswith("raw:device:target microwave:") or name.startswith("raw:sensor:target meter:")]
        self.assertTrue(columns)
        self.assertTrue(all(row["features"][index] == 0 for row in target["rows"] for index in columns))
        altered = copy.deepcopy(configuration())
        altered["reference_bindings"] = {}
        self.assertEqual(target, extract_feature_rows(dataset(device_name="unseen appliance", sensor_name="unseen meter"),
                                                     altered, mode="raw", raw_schema=source["raw_schema"]))

    def test_strict_cooking_requires_active_kitchen_pir_and_not_avatar_alone(self):
        data = dataset()
        data["frames"][0]["sensor_states"]["presence"]["state"] = [0]
        data["frames"][0]["avatar_position"] = [20, 20]
        broad = extract_feature_rows(data, configuration(), strict_cooking=False)
        strict = extract_feature_rows(data, configuration())
        self.assertEqual(self.value(broad, 0, "role:cooking:latest"), 1)
        self.assertEqual(self.value(strict, 0, "role:cooking:latest"), 0)
        self.assertEqual(self.value(strict, 1, "role:cooking:since_active_minutes"), 61)
        self.assertEqual(self.value(strict, 2, "role:cooking:latest"), 1)
        data["house"]["rooms"][0]["manual_type"] = "Living Room"
        strict = extract_feature_rows(data, configuration())
        self.assertTrue(all(self.value(strict, index, "role:cooking:latest") == 0 for index in range(3)))

    def test_only_primitive_roles_enter_features_without_hand_derived_meal_conclusions(self):
        result = extract_feature_rows(dataset(), configuration())
        for derived in ("recent_cooking", "cooking_then_relaxing", "eating"):
            self.assertFalse(any(name.startswith(f"role:{derived}:") for name in result["feature_names"]))
        self.assertEqual(self.value(result, 0, "role:cooking:since_active_minutes"), 0)
        self.assertEqual(self.value(result, 1, "role:cooking:since_active_minutes"), 5)
        self.assertEqual(self.value(result, 2, "role:cooking:since_active_minutes"), 0)

    def test_explicit_eating_role_requires_an_observation_instead_of_inferred_recent_cooking(self):
        data = dataset()
        config = configuration()
        eating = copy.deepcopy(config["reference_bindings"]["oven cooks"])
        eating.update({"feature": "eating", "reference": {"name": "dining seat", "type": "Dining_Table",
                       "properties": {"supports_eating": 1}}, "property_weights": {"supports_eating": 1},
                       "required_properties": ["supports_eating"]})
        config["reference_bindings"]["observed dining seat"] = eating
        data["frames"][1]["sensor_states"]["presence"]["state"] = [1]
        result = extract_feature_rows(data, config)
        # The rule engine can infer eating after cooking+presence, but that
        # inferred conclusion must not become a classifier observation.
        self.assertIn("role:eating:latest", result["feature_names"])
        self.assertEqual(self.value(result, 1, "role:eating:latest"), 0)
        self.assertEqual(self.value(result, 1, "role:eating:since_active_minutes"), 61)
        self.assertIn("eating", result["rows"][1]["rule_prediction"])
        data["house"]["devices"][0]["properties"]["supports_eating"] = 1
        observed = extract_feature_rows(data, config)
        self.assertEqual(self.value(observed, 0, "role:eating:latest"), 1)
        self.assertEqual(self.value(observed, 0, "role:eating:since_active_minutes"), 0)

    def test_rule_thresholds_weights_and_extra_derived_rules_cannot_change_primitive_vectors(self):
        data = dataset()
        first = extract_feature_rows(data, configuration())
        config = configuration()
        config["rules"] = {"cooking": {"threshold": .99, "weight": .2},
                           "test conclusion": {"weights": {"invented_derived_conclusion": 1}, "threshold": .3}}
        second = extract_feature_rows(data, config)
        self.assertEqual(first["feature_names"], second["feature_names"])
        self.assertEqual([row["features"] for row in first["rows"]], [row["features"] for row in second["rows"]])

    def test_since_active_age_is_causal_and_capped_after_long_inactivity(self):
        data = dataset()
        data["frames"][-1]["time_minutes"] = 1000
        data["frames"][-1]["sensor_states"]["target meter"] = {"state": [0], "consumption": [0]}
        data["frames"][-1]["sensor_states"]["presence"] = {"state": [0]}
        result = extract_feature_rows(data, configuration(), window_minutes=10)
        self.assertEqual(self.value(result, 2, "role:cooking:since_active_minutes"), 61)
        longer_window = extract_feature_rows(data, configuration(), window_minutes=120)
        self.assertEqual(self.value(longer_window, 2, "role:cooking:since_active_minutes"), 121)


if __name__ == "__main__":
    unittest.main()
