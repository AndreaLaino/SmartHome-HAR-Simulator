import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import read
import recognition_library as library
from activity_recognition import recognize_activities, recognition_settings
from app.context import AppContext
from app.io.scenario import _serialize_scenario
from models import Device, Sensor
from recognition_evaluation import capture_frame, evaluate_datasets, transfer_report
from reference_transfer import (anchor_reference_bindings, default_reference_bindings,
                                compatible_reference_activities, import_compatible_references,
                                match_reference_roles, validate_reference_bindings)


def reference_binding():
    return {"known oven": {"feature": "cooking", "kind": "device",
                           "reference": {"name": "Reference oven", "type": "Oven", "house_id": "source",
                                         "properties": {"heats_food": 1, "produces_heat": .8}, "recognition_weight": 1},
                           "property_weights": {"heats_food": .9, "produces_heat": .1},
                           "required_properties": ["heats_food"], "minimum_similarity": .5,
                           "strength": 1, "weight": 1}}


def target_runtime(properties=None):
    microwave = Device("new appliance", 10, 10, "Microwave", 1000, 0, 1000, 1000,
                       properties=properties or {"heats_food": 1, "produces_heat": .8, "defrosts": 1})
    sensor = Sensor("target meter", 10, 10, "Smart Meter", 0, 5000, 1, 0, associated_device=microwave.name)
    return {"devices": [microwave], "sensors": [sensor], "house_id": "target",
            "recognition_config": {"reference_bindings": reference_binding(), "minimum_confidence": .5}}


OBSERVATION = {"target meter": {"state": [1], "consumption": [1000]}}


class GuidedReferenceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.runtime = target_runtime()
        self.source = self.runtime["recognition_config"]
        self.target = self.runtime["devices"][0]
        self.destination = {"reference_bindings": {}, "minimum_confidence": .5}

    def options(self):
        return compatible_reference_activities(self.source, self.target, self.destination)

    def test_only_compatible_known_activities_are_offered_before_selection(self):
        options = self.options()
        self.assertEqual(set(options), {"cooking"})
        self.assertEqual(options["cooking"]["similarity"], 1)
        self.target.properties = {"produces_heat": 1, "cooking": 1}
        self.assertEqual(self.options(), {})

    def test_partial_match_uses_existing_weights_and_minimum_similarity(self):
        self.target.properties = {"heats_food": .6, "produces_heat": .8}
        self.assertAlmostEqual(self.options()["cooking"]["similarity"], .64)
        self.source["reference_bindings"]["known oven"]["minimum_similarity"] = .65
        self.assertEqual(self.options(), {})

    def test_disabled_reference_target_or_activity_is_not_selectable(self):
        for field in ("weight", "strength", "reference_weight", "target_weight", "source_rule", "target_rule"):
            with self.subTest(field=field):
                source, destination = copy.deepcopy(self.source), copy.deepcopy(self.destination)
                target = copy.deepcopy(self.target)
                binding = source["reference_bindings"]["known oven"]
                if field in {"weight", "strength"}:
                    binding[field] = 0
                elif field == "reference_weight":
                    binding["reference"]["recognition_weight"] = 0
                elif field == "target_weight":
                    target.recognition_weight = 0
                else:
                    (source if field == "source_rule" else destination)["rules"] = {"cooking": {"weight": 0}}
                self.assertEqual(compatible_reference_activities(source, target, destination), {})

    def test_multifunction_source_filters_each_role_and_imports_only_selected_ones(self):
        bindings = self.source["reference_bindings"]
        for binding in bindings.values():
            binding["reference"]["properties"]["screen_interaction"] = 1
        working = copy.deepcopy(bindings["known oven"])
        working.update(feature="working", property_weights={"screen_interaction": 1},
                       required_properties=["screen_interaction"])
        bindings["known working"] = working
        self.assertEqual(set(self.options()), {"cooking"})
        self.target.properties["screen_interaction"] = 1
        self.assertEqual(set(self.options()), {"cooking", "working"})
        before = copy.deepcopy(self.target.properties)
        imported = import_compatible_references(self.source, self.target, self.destination, ["working"])
        self.assertEqual(set(imported), {"known working"})
        self.assertEqual(self.target.properties, before)
        self.assertEqual(self.destination["reference_bindings"], {})

    def test_save_rechecks_stale_incompatible_or_empty_selections_without_mutations(self):
        self.assertIn("cooking", self.options())
        self.target.properties.pop("heats_food")
        before = copy.deepcopy(self.destination)
        for selected in (["cooking"], ["relaxing"], []):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                import_compatible_references(self.source, self.target, self.destination, selected)
        self.assertEqual(self.destination, before)

    def test_import_preserves_colliding_local_reference_and_source_provenance(self):
        existing = copy.deepcopy(self.source["reference_bindings"]["known oven"])
        existing["reference"]["properties"] = {"unrelated": 1}
        existing.update(property_weights={"unrelated": 1}, required_properties=["unrelated"])
        self.destination["reference_bindings"]["known oven"] = existing
        imported = import_compatible_references(self.source, self.target, self.destination, ["cooking"])
        self.assertEqual(imported["known oven"], existing)
        added = [binding for key, binding in imported.items() if key != "known oven"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["reference"]["house_id"], "source")
        self.destination["reference_bindings"] = imported
        self.assertEqual(import_compatible_references(self.source, self.target, self.destination, ["cooking"]), imported)

    def test_import_still_needs_observation_and_keeps_reference_property_matching(self):
        self.destination["reference_bindings"] = import_compatible_references(
            self.source, self.target, self.destination, ["cooking"])
        self.runtime["recognition_config"] = self.destination
        self.assertEqual(recognize_activities({}, self.runtime, {}, 0), set())
        self.assertIn("cooking", recognize_activities(OBSERVATION, self.runtime, {}, 1))
        self.assertNotIn("cooking", self.target.properties)

    def test_composite_activities_are_not_directly_offered_for_device_transfer(self):
        self.source = {"reference_bindings": {"tv": default_reference_bindings()["device:TV:relaxing"]}}
        self.target.properties = {"relaxing": 1}
        self.assertEqual(set(self.options()), {"relaxing"})

    def test_sleep_role_is_compatible_without_claiming_context_is_satisfied(self):
        self.source = {"reference_bindings": {"bed": default_reference_bindings()["device:Bed:sleeping"]}}
        self.target.properties = {"sleeping": 1}
        self.assertEqual(set(self.options()), {"sleeping"})
        self.destination["reference_bindings"] = import_compatible_references(
            self.source, self.target, self.destination, ["sleeping"])
        self.runtime["recognition_config"] = self.destination
        self.assertNotIn("sleeping", recognize_activities(OBSERVATION, self.runtime, {}, 20))


class ReferenceTransferTests(unittest.TestCase):
    def test_only_oven_is_known_but_microwave_fills_its_cooking_role(self):
        runtime = target_runtime()
        self.assertNotIn("cooking", runtime["devices"][0].properties)
        self.assertEqual(len(runtime["recognition_config"]["reference_bindings"]), 1)
        state = {}
        self.assertEqual(recognize_activities(OBSERVATION, runtime, state, 0), {"cooking"})
        result = state["recognition_results"]["cooking"]
        match = result["reference_matches"][0]
        self.assertEqual((match["reference"], match["target"]), ("Reference oven", "new appliance"))
        self.assertEqual(match["relation"], "substitution")
        self.assertEqual(match["similarity"], 1)
        self.assertEqual(match["matched_properties"], {"heats_food": 1, "produces_heat": 1})
        self.assertAlmostEqual(result["confidence"], .64)

    def test_default_cooking_has_one_oven_association_and_no_microwave_association(self):
        cooking = [binding for binding in default_reference_bindings().values() if binding["feature"] == "cooking"]
        self.assertEqual([binding["reference"]["type"] for binding in cooking], ["Oven"])

    def test_cooking_property_alone_cannot_bypass_a_missing_reference_association(self):
        runtime = target_runtime({"cooking": 1})
        runtime["recognition_config"]["reference_bindings"] = {}
        self.assertEqual(recognize_activities(OBSERVATION, runtime, {}, 0), set())
        self.assertEqual(recognition_settings(runtime["recognition_config"])["reference_bindings"], {})

    def test_deleting_only_the_oven_role_blocks_cooking_but_preserves_other_roles(self):
        obj = Device("microwave", 0, 0, "Microwave", 1000, 0, 1000, 1000)
        runtime = {"devices": [obj], "sensors": [], "recognition_config": recognition_settings()}
        observation = {"m": {"type": "Smart Meter", "associated_device": obj.name, "state": [1]}}
        self.assertIn("cooking", recognize_activities(observation, runtime, {}, 0))
        del runtime["recognition_config"]["reference_bindings"]["device:Oven:cooking"]
        state = {}
        self.assertNotIn("cooking", recognize_activities(observation, runtime, state, 1))
        self.assertIn("cooking", state["recognition_results"]["cooking"]["missing"])

    def test_raw_sensor_activity_properties_do_not_bypass_reference_matching(self):
        sensor = Sensor("s", 0, 0, "Switch", 0, 1, 1, 0, properties={"cooking": 1})
        runtime = {"devices": [], "sensors": [sensor]}
        self.assertNotIn("cooking", recognize_activities({"s": {"state": [1]}}, runtime, {}, 0))

    def test_matching_properties_require_a_real_observation(self):
        runtime = target_runtime()
        self.assertEqual(recognize_activities({"target meter": {"state": [0]}}, runtime, {}, 0), set())

    def test_activity_named_property_cannot_replace_required_reference_capabilities(self):
        runtime = target_runtime({"cooking": 1})
        self.assertNotIn("cooking", recognize_activities(OBSERVATION, runtime, {}, 0))

    def test_generic_heating_and_misleading_names_cannot_match_food_preparation(self):
        runtime = target_runtime({"produces_heat": 1, "grooming": 1})
        runtime["devices"][0].name = "Reference oven"
        runtime["devices"][0].type = "Oven"
        runtime["sensors"][0].associated_device = "Reference oven"
        self.assertNotIn("cooking", recognize_activities(OBSERVATION, runtime, {}, 0))

    def test_extra_target_properties_do_not_reduce_role_similarity(self):
        runtime = target_runtime()
        baseline = match_reference_roles(runtime["devices"][0], "device", validate_reference_bindings(reference_binding()))
        runtime["devices"][0].properties.update({"gaming": .8, "unknown": 1})
        other = match_reference_roles(runtime["devices"][0], "device", validate_reference_bindings(reference_binding()))
        self.assertEqual(baseline[0]["similarity"], other[0]["similarity"])

    def test_partial_similarity_reduces_confidence_and_match_threshold_can_reject_it(self):
        runtime = target_runtime({"heats_food": .9, "produces_heat": .4})
        state = {}
        self.assertIn("cooking", recognize_activities(OBSERVATION, runtime, state, 0))
        result = state["recognition_results"]["cooking"]
        self.assertAlmostEqual(result["reference_matches"][0]["similarity"], .86)
        self.assertLess(result["confidence"], .64)
        runtime["recognition_config"]["reference_bindings"]["known oven"]["minimum_similarity"] = .9
        self.assertNotIn("cooking", recognize_activities(OBSERVATION, runtime, {}, 0))

    def test_reference_binding_and_observation_weights_can_each_disable_transfer(self):
        for target in ("weight", "strength", "reference_weight", "device_weight", "sensor_weight"):
            with self.subTest(target=target):
                runtime = target_runtime()
                binding = runtime["recognition_config"]["reference_bindings"]["known oven"]
                if target in {"weight", "strength"}:
                    binding[target] = 0
                elif target == "reference_weight":
                    binding["reference"]["recognition_weight"] = 0
                elif target == "device_weight":
                    runtime["devices"][0].recognition_weight = 0
                else:
                    runtime["sensors"][0].recognition_weight = 0
                self.assertNotIn("cooking", recognize_activities(OBSERVATION, runtime, {}, 0))

    def test_reference_house_identity_distinguishes_original_from_substitution(self):
        runtime = target_runtime()
        obj = runtime["devices"][0]
        obj.name = "Reference oven"
        obj.type = "Oven"
        bindings = validate_reference_bindings(reference_binding())
        self.assertEqual(match_reference_roles(obj, "device", bindings, house_id="source")[0]["relation"], "reference")
        self.assertEqual(match_reference_roles(obj, "device", bindings, house_id="other")[0]["relation"], "substitution")

    def test_custom_sensor_roles_transfer_without_direct_activity_labels(self):
        runtime = target_runtime()
        binding = runtime["recognition_config"]["reference_bindings"]["known oven"]
        binding.update({"kind": "sensor", "reference": {"name": "reference detector", "type": "custom",
                       "properties": {"food_heat_detected": 1}}, "property_weights": {"food_heat_detected": 1},
                       "required_properties": ["food_heat_detected"]})
        runtime["devices"] = []
        runtime["sensors"] = [Sensor("target meter", 0, 0, "custom", 0, 1, 1, 0, properties={"food_heat_detected": 1})]
        self.assertIn("cooking", recognize_activities({"target meter": {"state": [1]}}, runtime, {}, 0))

    def test_sequence_preserves_both_reference_substitutions_after_cooking_stops(self):
        microwave = Device("heat", 0, 0, "Microwave", 1000, 0, 1000, 1000)
        ps = Device("console", 0, 0, "PlayStation", 180, 0, 180, 180)
        runtime = {"devices": [microwave, ps], "sensors": [], "avatar_position": (0, 0)}
        cook = {"m": {"type": "Smart Meter", "associated_device": "heat", "state": [1]}}
        relax = {"m": {"type": "Smart Meter", "associated_device": "heat", "state": [0]},
                 "p": {"type": "Smart Meter", "associated_device": "console", "state": [1]}}
        state = {}
        recognize_activities(cook, runtime, state, 0)
        self.assertIn("eating while relaxing", recognize_activities(relax, runtime, state, 5))
        references = {(item["target"], item["reference_type"]) for item in state["recognition_results"]["eating while relaxing"]["reference_matches"]}
        self.assertEqual(references, {("heat", "Oven"), ("console", "TV")})
        runtime["recognition_config"] = {"reference_bindings": {}}
        self.assertNotIn("eating while relaxing", recognize_activities(relax, runtime, state, 6))

    def test_invalid_reference_definitions_are_rejected(self):
        for field, value in (("property_weights", {"missing": 1}), ("property_weights", {"heats_food": 0}),
                             ("required_properties", ["missing"]), ("minimum_similarity", float("nan")),
                             ("kind", "unknown")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                bindings = reference_binding()
                bindings["known oven"][field] = value
                validate_reference_bindings(bindings)


class ReferenceTransferPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(library, "LIBRARY_DIR", self.root / "library"))

    def test_guided_sources_use_real_registered_objects_and_import_without_global_edits(self):
        from app.ui.reference_rules import guided_reference_sources

        shared = library.ensure_library()
        runtime = target_runtime()
        source_runtime = copy.deepcopy(runtime)
        source_runtime["devices"][0].name = "actual source oven"
        source_runtime["devices"][0].type = "Oven"
        snapshot = library.house_snapshot(source_runtime, "registeredsource", "Source house")
        library.atomic_json(library.LIBRARY_DIR / "houses" / "registeredsource.json", snapshot)
        ctx = AppContext(window=None, load_active=True, read_devices=runtime["devices"],
                         read_sensors=runtime["sensors"], recognition_config={"reference_bindings": {}})
        choices, errors = guided_reference_sources(ctx, ctx.recognition_config)
        self.assertEqual(errors, [])
        house_choices = {label: source for label, source in choices.items() if label.startswith("House ")}
        self.assertEqual(len(house_choices), 1)
        label, source = next(iter(house_choices.items()))
        self.assertIn("Source house", label)
        reference = source["reference_bindings"]["known oven"]["reference"]
        self.assertEqual((reference["name"], reference["house_id"]), ("actual source oven", "registeredsource"))
        ctx.recognition_config["reference_bindings"] = import_compatible_references(
            source, runtime["devices"][0], ctx.recognition_config, ["cooking"])
        self.assertEqual(library.load_library(), shared)
        self.assertIn("cooking", recognize_activities(OBSERVATION, library.context_runtime(ctx), {}, 0))
        self.assertNotIn("cooking", runtime["devices"][0].properties)

    def test_guided_sources_do_not_offer_absent_or_invalid_house_reference_objects(self):
        from app.ui.reference_rules import guided_reference_sources

        snapshot = library.house_snapshot(target_runtime(), "source", "Not an oven house")
        library.atomic_json(library.LIBRARY_DIR / "houses" / "source.json", snapshot)
        # Its frozen reference names an oven, but its actual device is a microwave.
        ctx = AppContext(window=None, load_active=True, read_devices=[], read_sensors=[],
                         recognition_config={"reference_bindings": {}})
        choices, errors = guided_reference_sources(ctx, ctx.recognition_config)
        self.assertEqual(errors, [])
        self.assertFalse(any(label.startswith("House ") for label in choices))
        self.assertTrue(any(label.startswith("Built-in catalogue:") for label in choices))

    def test_oven_only_house_can_browse_catalogue_and_add_tv_without_losing_oven(self):
        from app.ui.reference_rules import guided_reference_sources

        shared = library.ensure_library()
        runtime = target_runtime()
        ctx = AppContext(window=None, load_active=True, read_devices=runtime["devices"],
                         read_sensors=runtime["sensors"], recognition_config=runtime["recognition_config"])
        before = copy.deepcopy(ctx.recognition_config)
        choices, errors = guided_reference_sources(ctx, ctx.recognition_config)
        self.assertEqual(errors, [])
        tv_label = next(label for label in choices if label.startswith("Shared catalogue:") and "(TV)" in label)
        tv_source = choices[tv_label]
        self.assertEqual(ctx.recognition_config, before)
        self.assertEqual(compatible_reference_activities(tv_source, runtime["devices"][0], ctx.recognition_config), {})
        console = Device("console", 20, 20, "PlayStation", 180, 0, 180, 180)
        self.assertEqual(set(compatible_reference_activities(tv_source, console, ctx.recognition_config)), {"relaxing"})
        ctx.recognition_config["reference_bindings"] = import_compatible_references(
            tv_source, console, ctx.recognition_config, ["relaxing"])
        self.assertEqual(ctx.recognition_config["reference_bindings"]["known oven"],
                         before["reference_bindings"]["known oven"])
        choices, errors = guided_reference_sources(ctx, ctx.recognition_config)
        self.assertIn(tv_label, choices)
        self.assertTrue(any("(Oven)" in label for label in choices))
        self.assertEqual(library.load_library(), shared)

    def test_builtin_catalogue_is_available_without_creating_or_activating_a_library(self):
        from app.ui.reference_rules import guided_reference_sources

        runtime = target_runtime()
        ctx = AppContext(window=None, load_active=True, read_devices=runtime["devices"],
                         read_sensors=runtime["sensors"], recognition_config=runtime["recognition_config"])
        before = copy.deepcopy(ctx.recognition_config)
        choices, errors = guided_reference_sources(ctx, ctx.recognition_config)
        self.assertEqual(errors, [])
        self.assertTrue(any(label.startswith("Built-in catalogue:") and "(TV)" in label for label in choices))
        self.assertFalse(library.LIBRARY_DIR.exists())
        self.assertEqual(ctx.recognition_config, before)

    def test_shared_catalogue_oven_supports_physical_and_existing_microwaves(self):
        from app.ui.reference_rules import guided_reference_sources

        library.ensure_library()
        runtime = target_runtime()
        runtime["devices"][0].properties["food_preparation"] = 1
        ctx = AppContext(window=None, load_active=True, read_devices=runtime["devices"],
                         read_sensors=runtime["sensors"], recognition_config=runtime["recognition_config"])
        choices, errors = guided_reference_sources(ctx, ctx.recognition_config)
        self.assertEqual(errors, [])
        label = "Shared catalogue: Oven (Oven)"
        source = choices[label]
        self.assertNotIn(label + " [2]", choices)
        for target in (runtime["devices"][0], Device("old microwave", 0, 0, "Microwave", 1000, 0, 1000, 1000)):
            options = compatible_reference_activities(source, target, ctx.recognition_config)
            self.assertEqual(set(options), {"cooking"})
            self.assertEqual(options["cooking"]["similarity"], 1)
        runtime["devices"][0].properties = {"heating": 1}
        self.assertEqual(compatible_reference_activities(source, runtime["devices"][0], ctx.recognition_config), {})

    def test_old_library_migrates_explicit_associations_and_preserves_existing_settings(self):
        shared = library.default_library()
        del shared["recognition_config"]["reference_bindings"]
        shared["recognition_config"]["rules"]["cooking"]["threshold"] = .9
        library.atomic_json(library.LIBRARY_DIR / "library.json", shared)
        migrated = library.ensure_library()
        self.assertEqual(migrated["revision"], shared["revision"] + 1)
        self.assertEqual(migrated["recognition_config"]["rules"]["cooking"]["threshold"], .9)
        self.assertIn("device:Oven:cooking", migrated["recognition_config"]["reference_bindings"])
        self.assertEqual(library.ensure_library()["revision"], migrated["revision"])

    def test_reference_house_freezes_actual_source_objects_without_target_associations(self):
        runtime = target_runtime()
        runtime["devices"][0].type = "Oven"
        snapshot = library.house_snapshot(runtime, "baseline", "Baseline")
        bindings = anchor_reference_bindings(reference_binding(), snapshot)
        reference = bindings["known oven"]["reference"]
        self.assertEqual((reference["name"], reference["house_id"]), ("new appliance", "baseline"))
        self.assertNotIn("Microwave", [item["reference"]["type"] for item in bindings.values()])

    def test_reference_bindings_survive_csv_and_drive_the_reloaded_runtime(self):
        runtime = target_runtime()
        ctx = AppContext(window=None, load_active=True, read_devices=runtime["devices"], read_sensors=runtime["sensors"],
                         recognition_config=runtime["recognition_config"])
        for name in ("coordinates", "read_walls", "read_sensors", "read_devices", "read_doors"):
            self.stack.enter_context(patch.object(read, name, []))
        for name in ("read_room_overrides", "read_recognition_config", "read_recognition_link"):
            self.stack.enter_context(patch.object(read, name, {}))
        path = self.root / "scenario.csv"
        path.write_text(_serialize_scenario(ctx), encoding="utf-8")
        _, _, sensors, devices, _ = read.read_coordinates_from_file(path)
        reloaded = {"devices": devices, "sensors": sensors, "recognition_config": read.read_recognition_config}
        self.assertEqual(reloaded["recognition_config"], ctx.recognition_config)
        self.assertIn("cooking", recognize_activities(OBSERVATION, reloaded, {}, 0))

    def test_held_out_evaluation_and_coverage_use_the_same_reference_substitution(self):
        runtime = target_runtime()
        snapshot = library.house_snapshot(runtime, "target", "Target")
        frame = capture_frame(runtime, OBSERVATION, 0)
        frame["expected_activities"] = ["cooking"]
        data = {"house_id": "target", "house": snapshot, "partition": "test", "run_id": "test", "frames": [frame]}
        config = runtime["recognition_config"]
        result = evaluate_datasets([data], config)
        self.assertEqual(result["exact_match_accuracy"], 1)
        self.assertTrue(transfer_report(None, snapshot, config)["activities"]["cooking"]["transferable"])
        config["reference_bindings"] = {}
        self.assertEqual(evaluate_datasets([data], config)["exact_match_accuracy"], 0)
        self.assertFalse(transfer_report(None, snapshot, config)["activities"]["cooking"]["transferable"])

    def test_shared_rule_deletion_reaches_all_connected_houses(self):
        shared = library.ensure_library()
        ctx = AppContext(window=None, load_active=True,
                         read_devices=[Device("target", 0, 0, "Microwave", 1000, 0, 1000, 1000)])
        library.connect_house(ctx)
        runtime = library.context_runtime(ctx)
        observation = {"m": {"type": "Smart Meter", "associated_device": "target", "state": [1]}}
        state = {}
        self.assertIn("cooking", recognize_activities(observation, runtime, state, 0))
        del shared["recognition_config"]["reference_bindings"]["device:Oven:cooking"]
        library.save_library(shared, shared["revision"])
        self.assertNotIn("cooking", recognize_activities(observation, runtime, state, 1))

    def test_example_csv_files_share_only_an_oven_reference_and_transfer_at_runtime(self):
        for name in ("coordinates", "read_walls", "read_sensors", "read_devices", "read_doors"):
            self.stack.enter_context(patch.object(read, name, []))
        for name in ("read_room_overrides", "read_recognition_config", "read_recognition_link"):
            self.stack.enter_context(patch.object(read, name, {}))
        folder = Path(__file__).resolve().parents[1] / "examples" / "reference_transfer"
        for filename, expected_relation in (("oven_reference.csv", "reference"), ("microwave_target.csv", "substitution")):
            with self.subTest(filename=filename):
                _, _, sensors, devices, _ = read.read_coordinates_from_file(folder / filename)
                runtime = {"devices": devices, "sensors": sensors, "recognition_config": read.read_recognition_config}
                self.assertNotIn("cooking", devices[0].properties)
                state = {}
                recognized = recognize_activities({"power_meter": {"consumption": [1000]}}, runtime, state, 0)
                self.assertEqual(recognized, {"cooking"})
                match = state["recognition_results"]["cooking"]["reference_matches"][0]
                self.assertEqual(match["reference_type"], "Oven")
                self.assertEqual(match["relation"], expected_relation)
                self.assertEqual(match["similarity"], 1)


if __name__ == "__main__":
    unittest.main()
