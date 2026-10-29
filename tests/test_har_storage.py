"""Persistence and category contracts for learned, cross-house HAR."""

from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.context import AppContext
from app.io import scenario
from har import catalogue, storage
from models import Device, LightSwitch, Sensor
import read


class CategoryStorageTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(storage, "DATA_DIR", self.root / "har_data"))

    def session(self, identifier="session-a"):
        house = storage.new_house_config()
        return {
            "schema_version": 1, "session_id": identifier,
            "house_id": house["house_id"], "house_label": "Kitchen A",
            "partition": "source", "catalogue": catalogue.default_catalogue(),
            "frames": [{
                "time_seconds": 0, "devices": [], "sensors": [],
                "sensor_states": {}, "rooms": [], "labels": None,
            }, {
                "time_seconds": 10, "devices": [], "sensors": [],
                "sensor_states": {}, "rooms": [], "labels": [],
            }, {
                "time_seconds": 20, "devices": [], "sensors": [],
                "sensor_states": {}, "rooms": [], "labels": ["cooking", "talking"],
            }],
        }

    def legacy_dictionary(self):
        return {
            "schema_version": 1,
            "device_categories": {"DeviceCooking": "Custom food preparation description."},
            "sensor_categories": {
                "SensorPower": {"description": "House meter.", "measurement": "power", "unit": "W"},
            },
            "device_types": {"Oven": ["DeviceCooking"], "Unclassified appliance": []},
            "sensor_types": {"Smart Meter": ["SensorPower"], "Unclassified sensor": []},
        }

    def test_shared_activity_names_are_optional_and_preserve_exact_label_spelling(self):
        self.assertEqual(storage.load_activity_names(), [])
        self.assertFalse(storage.DATA_DIR.exists())
        path = storage.save_activity_names([" cooking ", "relaxing", "cooking", "Cooking", "attività personale"])
        self.assertEqual(path, storage.DATA_DIR / "activities.json")
        self.assertEqual(storage.load_activity_names(), ["cooking", "relaxing", "Cooking", "attività personale"])
        self.assertEqual(storage.read_json(path), {
            "schema_version": 1, "activities": ["cooking", "relaxing", "Cooking", "attività personale"],
        })
        self.assertEqual(list(storage.DATA_DIR.iterdir()), [path])
        storage.save_activity_names([])
        self.assertEqual(storage.load_activity_names(), [])

    def test_invalid_activity_names_do_not_replace_the_saved_registry(self):
        path = storage.save_activity_names(["cooking"])
        before = path.read_bytes()
        for names in (None, "cooking", [None], [""], ["  "], ["cooking, relaxing"], ["cooking\n"], ["a\rb"]):
            with self.subTest(names=names), self.assertRaises(ValueError):
                storage.save_activity_names(names)
            self.assertEqual(path.read_bytes(), before)

    def test_corrupt_activity_registry_is_reported_without_rewriting_it(self):
        path = storage.save_activity_names(["cooking"])
        for data in ({"schema_version": 2, "activities": ["cooking"]},
                     {"schema_version": 1},
                     {"schema_version": 1, "activities": "cooking"},
                     {"schema_version": 1, "activities": ["cooking, relaxing"]}):
            storage.atomic_json(path, data)
            before = path.read_bytes()
            with self.subTest(data=data), self.assertRaises(ValueError):
                storage.load_activity_names()
            self.assertEqual(path.read_bytes(), before)
        path.write_text("{unfinished", encoding="utf-8")
        with self.assertRaises(ValueError):
            storage.load_activity_names()
        self.assertEqual(path.read_text(encoding="utf-8"), "{unfinished")

    def test_categories_are_reused_across_types_without_assigning_activities(self):
        dictionary = catalogue.default_catalogue()
        oven = Device("different name", 0, 0, "Oven", 100, 0, 0, 100)
        microwave = Device("appliance b", 0, 0, "Microwave", 100, 0, 0, 100)
        self.assertEqual(catalogue.categories_for(oven, "device", dictionary), ["Device_Cooking"])
        self.assertEqual(catalogue.categories_for(microwave, "device", dictionary), ["Device_Cooking"])
        self.assertNotIn("activities", dictionary)
        self.assertEqual(catalogue.categories_for({"type": "computer"}, "device", dictionary), [
            "Device_Computer", "Device_Entertainment",
        ])

    def test_explicit_empty_and_multiple_categories_override_type_defaults(self):
        dictionary = catalogue.default_catalogue()
        device = Device("oven", 0, 0, "Oven", 100, 0, 0, 100, categories=[])
        self.assertEqual(catalogue.categories_for(device, "device", dictionary), [])
        device.categories = ["Device_Cooking", "Device_Computer", "Device_Cooking"]
        self.assertEqual(catalogue.categories_for(device, "device", dictionary), ["Device_Cooking", "Device_Computer"])
        self.assertEqual(catalogue.categories_for({"type": "Unknown"}, "device", dictionary), [])
        self.assertEqual(catalogue.categories_for({"type": "PIR"}, "sensor", dictionary), ["Sensor_Presence"])

    def test_old_and_new_explicit_categories_resolve_against_the_requested_dictionary(self):
        current = catalogue.default_catalogue()
        frozen = self.legacy_dictionary()
        before = json.loads(json.dumps(frozen))
        for kind, old_name, new_name in (
            ("device", "DeviceCooking", "Device_Cooking"),
            ("sensor", "SensorPower", "Sensor_Power"),
        ):
            with self.subTest(kind=kind):
                self.assertEqual(catalogue.categories_for(
                    {"categories": [old_name, new_name]}, kind, current,
                ), [new_name])
                self.assertEqual(catalogue.categories_for(
                    {"categories": [new_name, old_name]}, kind, frozen,
                ), [old_name])
        self.assertEqual(catalogue.validate_catalogue(frozen), before)
        self.assertEqual(frozen, before)

    def test_loading_legacy_shared_dictionary_upgrades_names_without_losing_customization(self):
        dictionary = self.legacy_dictionary()
        dictionary["device_categories"]["DeviceInstrument"] = "A custom category, not a built-in alias."
        dictionary["device_types"]["Piano"] = ["DeviceInstrument"]
        dictionary["sensor_categories"]["SensorPower"]["unit"] = "kW"
        dictionary["sensor_categories"]["SensorHumidity"] = {
            "description": "Relative humidity.", "measurement": "generic", "unit": "%",
        }
        dictionary["sensor_types"]["Humidity"] = ["SensorHumidity"]
        path = storage.ensure_data() / "catalogue.json"
        storage.atomic_json(path, dictionary)

        upgraded = catalogue.load_catalogue()

        self.assertEqual(upgraded["device_categories"], {
            "Device_Cooking": "Custom food preparation description.",
            "DeviceInstrument": "A custom category, not a built-in alias.",
        })
        self.assertEqual(upgraded["sensor_categories"], {
            "Sensor_Power": {"description": "House meter.", "measurement": "power", "unit": "kW"},
            "SensorHumidity": {"description": "Relative humidity.", "measurement": "generic", "unit": "%"},
        })
        self.assertEqual(upgraded["device_types"], {
            "Oven": ["Device_Cooking"], "Unclassified appliance": [], "Piano": ["DeviceInstrument"],
        })
        self.assertEqual(upgraded["sensor_types"], {
            "Smart Meter": ["Sensor_Power"], "Unclassified sensor": [], "Humidity": ["SensorHumidity"],
        })
        self.assertEqual(storage.read_json(path), upgraded)
        self.assertEqual(catalogue.load_catalogue(), upgraded)

    def test_conflicting_legacy_and_current_shared_definitions_do_not_replace_the_file(self):
        path = storage.ensure_data() / "catalogue.json"
        for kind in ("device", "sensor"):
            with self.subTest(kind=kind):
                dictionary = self.legacy_dictionary()
                if kind == "device":
                    dictionary["device_categories"]["Device_Cooking"] = "A different definition."
                else:
                    dictionary["sensor_categories"]["Sensor_Power"] = {
                        "description": "House meter.", "measurement": "power", "unit": "kW",
                    }
                storage.atomic_json(path, dictionary)
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    catalogue.load_catalogue()
                self.assertEqual(path.read_bytes(), before)
                with self.assertRaises(ValueError):
                    catalogue.save_catalogue(dictionary)
                self.assertEqual(path.read_bytes(), before)

    def test_unknown_categories_and_wrong_kind_are_rejected(self):
        dictionary = catalogue.default_catalogue()
        for values, kind in ((["cooking"], "device"), (["Device_Cooking"], "sensor"), ("Device_Cooking", "device")):
            with self.subTest(values=values, kind=kind), self.assertRaises(ValueError):
                catalogue.validate_categories(values, kind, dictionary)

    def test_shared_dictionary_customization_round_trips(self):
        dictionary = catalogue.load_catalogue()
        dictionary["device_categories"]["Device_Instrument"] = "Musical instrument."
        dictionary["device_types"]["Piano"] = ["Device_Instrument"]
        catalogue.save_catalogue(dictionary)
        reloaded = catalogue.load_catalogue()
        self.assertEqual(catalogue.categories_for({"type": "Piano"}, "device", reloaded), ["Device_Instrument"])
        self.assertEqual(set(path.name for path in storage.DATA_DIR.iterdir()), {"catalogue.json", "models", "sessions", "reports"})
        self.assertEqual(reloaded["sensor_categories"]["Sensor_Power"]["unit"], "W")
        self.assertEqual(reloaded["sensor_categories"]["Sensor_Weight"]["unit"], "configured")

    def test_invalid_measurements_and_ambiguous_type_names_are_rejected(self):
        dictionary = catalogue.default_catalogue()
        dictionary["sensor_categories"]["Sensor_Power"]["measurement"] = "activity"
        with self.assertRaises(ValueError):
            catalogue.save_catalogue(dictionary)
        dictionary = catalogue.default_catalogue()
        dictionary["device_types"]["oven"] = ["Device_Cleaning"]
        with self.assertRaises(ValueError):
            catalogue.save_catalogue(dictionary)

    def test_category_compatibility_rejects_different_physical_meanings(self):
        source = catalogue.default_catalogue()
        target = catalogue.default_catalogue()
        target["sensor_categories"]["Sensor_Power"]["description"] = "Another meter."
        target["sensor_categories"]["Sensor_Extra"] = {
            "description": "Target-only reading.", "measurement": "generic", "unit": "configured",
        }
        catalogue.validate_catalogue_compatibility(source, target)
        target["sensor_categories"]["Sensor_Power"]["unit"] = "kW"
        with self.assertRaisesRegex(ValueError, "Sensor_Power.*unit"):
            catalogue.validate_catalogue_compatibility(source, target)
        target = catalogue.default_catalogue()
        target["sensor_categories"]["Sensor_Power"]["measurement"] = "temperature"
        with self.assertRaisesRegex(ValueError, "Sensor_Power.*measurement"):
            catalogue.validate_catalogue_compatibility(source, target)

    def test_sessions_preserve_frozen_dictionary_and_label_semantics(self):
        session = self.session()
        storage.save_session(session)
        dictionary = catalogue.load_catalogue()
        dictionary["device_types"]["Oven"] = ["Device_Cleaning"]
        catalogue.save_catalogue(dictionary)
        self.assertEqual(storage.list_sessions(), [session])
        self.assertEqual(storage.list_sessions()[0]["catalogue"]["device_types"]["Oven"], ["Device_Cooking"])

    def test_non_finite_data_does_not_replace_existing_json(self):
        path = self.root / "existing.json"
        storage.atomic_json(path, {"reading": 1})
        for value in (float("inf"), float("nan"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage.atomic_json(path, {"reading": value})
            self.assertEqual(json.loads(path.read_text()), {"reading": 1})
        session = self.session()
        session["frames"][0]["sensor_states"] = {"meter": {"state": [float("nan")]}}
        with self.assertRaises(ValueError):
            storage.save_session(session)

    def test_json_numeric_overflow_is_rejected_on_read(self):
        path = self.root / "bad.json"
        path.write_text('{"reading":1e9999}', encoding="utf-8")
        with self.assertRaises(ValueError):
            storage.read_json(path)

    def test_failed_atomic_replace_preserves_previous_data_and_cleans_temporary_file(self):
        path = self.root / "model.json"
        storage.atomic_json(path, {"version": 1})
        with patch.object(storage.os, "replace", side_effect=PermissionError("read only")):
            with self.assertRaises(PermissionError):
                storage.atomic_json(path, {"version": 2})
        self.assertEqual(json.loads(path.read_text()), {"version": 1})
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_sessions_reject_invalid_times_and_labels(self):
        for replacement in ({"time_seconds": -1}, {"time_seconds": "0"}, {"labels": "cooking"}):
            session = self.session()
            session["frames"][0].update(replacement)
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                storage.save_session(session)
        session = self.session()
        session["frames"][1]["time_seconds"] = 30
        with self.assertRaises(ValueError):
            storage.save_session(session)

    def test_session_categories_are_validated_against_its_own_dictionary(self):
        session = self.session()
        session["frames"][0]["devices"] = [{
            "name": "appliance", "type": "Custom", "categories": ["UnknownCategory"],
        }]
        with self.assertRaisesRegex(ValueError, "Unknown device category"):
            storage.validate_session(session)
        session["catalogue"]["device_categories"]["UnknownCategory"] = "A new target capability."
        self.assertIs(storage.validate_session(session), session)

    def test_safe_model_round_trip_and_invalid_identifiers(self):
        model = {"model_id": "learned-model-1", "schema_version": 1, "estimator": {"trees": []}}
        storage.save_model(model)
        self.assertEqual(storage.load_model(model["model_id"]), model)
        self.assertEqual(storage.list_models(), [model])
        for identifier in ("../escape", "..", "a/b", "a\\b", "", None):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                storage.load_model(identifier)
        session = self.session("../escape")
        with self.assertRaises(ValueError):
            storage.save_session(session)
        self.assertFalse((self.root / "escape.json").exists())


class HARScenarioTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for key in ("coordinates", "read_walls", "read_sensors", "read_devices", "read_doors", "read_walls_coordinates"):
            self.stack.enter_context(patch.object(read, key, []))
        for key in ("read_room_overrides", "read_har_config"):
            self.stack.enter_context(patch.object(read, key, {}))

    def test_each_house_and_new_scenario_gets_distinct_identity(self):
        first = AppContext(window=None)
        second = AppContext(window=None)
        self.assertNotEqual(first.har_config["house_id"], second.har_config["house_id"])
        before = first.har_config["house_id"]
        with patch("label_layout.forget_canvas_labels"), patch("sensor.reset_temperature_runtime_state"):
            scenario._clear_scenario(first, Mock())
        self.assertNotEqual(first.har_config["house_id"], before)

    def test_failed_recording_save_preserves_scene_before_new_or_load(self):
        ctx = AppContext(window=None, load_active=True, current_file="original.csv")
        original_config = dict(ctx.har_config)
        original_device = Device("oven", 0, 0, "Oven", 1, 0, 0, 1)
        ctx.read_devices = [original_device]
        ctx._learned_har_window = Mock()
        canvas = Mock()
        for operation in (
            lambda: scenario._clear_scenario(ctx, canvas),
            lambda: scenario._load_scenario(ctx, canvas, "replacement.csv"),
        ):
            with patch("har.runtime.finish_recording", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    operation()
            self.assertEqual(ctx.har_config, original_config)
            self.assertEqual(ctx.read_devices, [original_device])
            self.assertEqual(ctx.current_file, "original.csv")
            self.assertTrue(ctx.load_active)
            canvas.delete.assert_not_called()
            ctx._learned_har_window.close.assert_not_called()

    def test_house_editor_closes_only_after_recording_is_saved(self):
        ctx = AppContext(window=None)
        ctx._learned_har_window = Mock()
        events = []
        ctx._learned_har_window.close.side_effect = lambda: events.append("close")
        with patch("har.runtime.finish_recording", side_effect=lambda _: events.append("save")):
            scenario._prepare_har_scene_change(ctx)
        self.assertEqual(events, ["save", "close"])

    def test_csv_round_trip_preserves_identity_categories_and_light_switches(self):
        ctx = AppContext(window=None, load_active=True)
        ctx.har_config.update(house_label="House A", model_id="model-1")
        ctx.read_devices = [
            Device("oven", 0, 0, "Oven", 2000, 0, 0, 2000, categories=["Device_Cooking", "Device_Computer"]),
            Device("lamp", 5, 5, "Smart Light", 10, 0, 0, 10, light_control="automatic",
                   light_switches=[LightSwitch("wall switch", 100, 50, "lamp")], categories=[]),
            Device("other", 10, 10, "Microwave", 1000, 0, 0, 1000),
        ]
        ctx.read_sensors = [Sensor("pir", 0, 0, "PIR", 0, 1, 1, 0, categories=["Sensor_Presence"])]
        path = self.root / "house.csv"
        path.write_text(scenario._serialize_scenario(ctx), encoding="utf-8")
        _, _, sensors, devices, _ = read.read_coordinates_from_file(path)
        self.assertEqual(read.read_har_config, ctx.har_config)
        self.assertEqual(devices[0].categories, ["Device_Cooking", "Device_Computer"])
        self.assertEqual(devices[1].categories, [])
        self.assertIsNone(devices[2].categories)
        self.assertEqual(sensors[0].categories, ["Sensor_Presence"])
        self.assertEqual(devices[1].light_control, "automatic")
        self.assertEqual(devices[1].light_switches, ctx.read_devices[1].light_switches)

    def test_legacy_csv_explicit_categories_remain_usable_with_current_dictionary(self):
        ctx = AppContext(window=None, load_active=True)
        ctx.read_devices = [Device(
            "old oven", 0, 0, "Oven", 2000, 0, 0, 2000, categories=["DeviceCooking"],
        )]
        ctx.read_sensors = [Sensor(
            "old meter", 0, 0, "Smart Meter", 0, 2000, 1, 0,
            associated_device="old oven", categories=["SensorPower"],
        )]
        path = self.root / "legacy-categories.csv"
        path.write_text(scenario._serialize_scenario(ctx), encoding="utf-8")

        _, _, sensors, devices, _ = read.read_coordinates_from_file(path)

        dictionary = catalogue.default_catalogue()
        self.assertEqual(catalogue.categories_for(devices[0], "device", dictionary), ["Device_Cooking"])
        self.assertEqual(catalogue.categories_for(sensors[0], "sensor", dictionary), ["Sensor_Power"])

    def test_load_restores_saved_identity_and_legacy_load_creates_a_new_one(self):
        config = storage.new_house_config()
        path = self.root / "house.csv"
        import csv
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["HAR metadata"])
            writer.writerow(["config", json.dumps(config)])
        ctx = AppContext(window=None)
        with ExitStack() as local:
            for name in ("draw_points", "draw_walls", "draw_sensors", "draw_devices", "draw_doors"):
                local.enter_context(patch.object(scenario, name))
            local.enter_context(patch("utils.refresh_pir_fov"))
            local.enter_context(patch("utils.set_pir_fov_sources"))
            local.enter_context(patch("app.ui.rooms.refresh_rooms"))
            local.enter_context(patch("app.ui.bindings.autostart_bound_ip_loggers"))
            local.enter_context(patch("app.ui.bindings.autostart_bound_gpio_loggers"))
            scenario._load_scenario(ctx, Mock(), str(path))
            self.assertEqual(ctx.har_config, config)
            path.write_text("Devices\nold,0,0,Oven,100,0,0,100,0,1\n", encoding="utf-8")
            scenario._load_scenario(ctx, Mock(), str(path))
        self.assertNotEqual(ctx.har_config["house_id"], config["house_id"])
        self.assertIsNone(ctx.read_devices[0].categories)

    def test_custom_categories_and_object_names_are_safely_quoted(self):
        ctx = AppContext(window=None, load_active=True)
        ctx.read_devices = [Device('device,"a"\nrow', 0, 0, "Custom", 1, 0, 0, 1, categories=['Custom,"category"'])]
        path = self.root / "quoted.csv"
        path.write_text(scenario._serialize_scenario(ctx), encoding="utf-8")
        *_, devices, doors = read.read_coordinates_from_file(path)
        self.assertEqual(devices[0].name, ctx.read_devices[0].name)
        self.assertEqual(devices[0].categories, ctx.read_devices[0].categories)


if __name__ == "__main__":
    unittest.main()
