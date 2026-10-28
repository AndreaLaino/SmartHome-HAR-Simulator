"""Observable category channels transfer independently of device identity."""

from copy import deepcopy
from datetime import datetime
import unittest
from unittest.mock import MagicMock, Mock, patch

import device
import sensor
from har.catalogue import categories_for, default_catalogue
from har.features import FeatureEncoder, primitive_names, primitives
from models import Device, Sensor


class CategoryFeatureTests(unittest.TestCase):
    def setUp(self):
        self.catalogue = default_catalogue()

    def frame(self):
        return {
            "time_seconds": 0, "rooms": [],
            "devices": [{"name": "oven-a", "type": "Oven", "state": 1,
                         "power": 1000, "max_consumption": 1000, "current_consumption": 500,
                         "categories": ["Device_Cooking"]}],
            "sensors": [{"name": "meter-a", "type": "Smart Meter", "min_val": 0,
                         "max_val": 1000, "state": 1, "consumption": 500,
                         "associated_device": "oven-a", "categories": ["Sensor_Power"]}],
            "sensor_states": {"meter-a": {"state": [1], "consumption": [500]}},
            "labels": ["cooking"],
        }

    def channels(self, frame):
        return dict(zip(primitive_names(self.catalogue), primitives(frame, self.catalogue)))

    def test_changed_device_names_types_and_labels_preserve_equivalent_observations(self):
        source = self.frame()
        target = deepcopy(source)
        target["devices"][0].update(name="microwave-z", type="Microwave")
        target["sensors"][0].update(name="other-meter", type="Custom Meter", associated_device="microwave-z")
        target["sensor_states"]["other-meter"] = target["sensor_states"].pop("meter-a")
        target["labels"] = ["different human annotation"]
        self.assertEqual(primitives(source, self.catalogue), primitives(target, self.catalogue))
        self.assertFalse(any("oven-a" in name or "cooking" == name for name in primitive_names(self.catalogue)))

    def test_explicit_empty_multiple_and_extra_categories_have_distinct_meanings(self):
        frame = self.frame()
        frame["devices"][0]["categories"] = None
        self.assertEqual(self.channels(frame)["device/Device_Cooking/available"], 1)
        frame["devices"][0]["categories"] = []
        self.assertEqual(self.channels(frame)["device/Device_Cooking/available"], 0)
        frame["devices"][0]["categories"] = ["Device_Cooking", "Device_Entertainment"]
        channels = self.channels(frame)
        self.assertEqual(channels["device/Device_Cooking/on_fraction"], 1)
        self.assertEqual(channels["device/Device_Entertainment/on_fraction"], 1)
        baseline = primitives(frame, self.catalogue)
        frame["devices"][0]["categories"].append("TargetOnlyCategory")
        self.assertEqual(primitives(frame, self.catalogue), baseline)

    def test_explicit_zero_meter_reading_overrides_on_state_and_stale_power(self):
        frame = self.frame()
        frame["sensor_states"]["meter-a"]["consumption"] = [500, 0]
        channels = self.channels(frame)
        self.assertEqual(channels["device/Device_Cooking/on_fraction"], 1)
        self.assertEqual(channels["device/Device_Cooking/power_w"], 0)
        self.assertEqual(channels["device/Device_Cooking/power_observed"], 1)
        self.assertEqual(channels["sensor/Sensor_Power/value"], 0)
        self.assertEqual(channels["sensor/Sensor_Power/observed"], 1)

    def test_duplicate_whole_device_meters_do_not_double_power(self):
        frame = self.frame()
        baseline = primitives(frame, self.catalogue)
        extra = deepcopy(frame["sensors"][0])
        extra["name"] = "meter-b"
        frame["sensors"].append(extra)
        frame["sensor_states"]["meter-b"] = {"consumption": [500]}
        self.assertEqual(self.channels(frame)["device/Device_Cooking/power_w"], 500)
        self.assertEqual(self.channels(frame)["device/Device_Cooking/power_fraction"], .5)
        self.assertEqual(primitives(frame, self.catalogue), baseline)

    def test_weight_scales_are_normalized_without_assuming_kilograms(self):
        frame = self.frame()
        frame["devices"] = []
        frame["sensors"] = [{"name": "load", "type": "Weight", "min_val": 0,
                             "max_val": 1, "categories": ["Sensor_Weight"]}]
        frame["sensor_states"] = {"load": {"state": [.5]}}
        self.assertEqual(self.channels(frame)["sensor/Sensor_Weight/normalized"], .5)
        frame["sensors"][0]["max_val"] = 100
        frame["sensor_states"]["load"]["state"] = [50]
        self.assertEqual(self.channels(frame)["sensor/Sensor_Weight/normalized"], .5)
        self.assertEqual(self.channels(frame)["sensor/Sensor_Weight/value"], 50)
        self.assertEqual(self.catalogue["sensor_categories"]["Sensor_Weight"]["unit"], "configured")

    def test_temporal_features_use_simulated_duration_and_history_is_per_encoder(self):
        encoder = FeatureEncoder(self.catalogue, 120)
        frame = self.frame()
        frame["sensors"] = []
        frame["sensor_states"] = {}
        frame["devices"][0]["state"] = 0
        encoder.update(frame)
        frame["time_seconds"] = 30
        frame["devices"][0]["state"] = 1
        encoder.update(frame)
        frame["time_seconds"] = 90
        frame["devices"][0]["state"] = 0
        values = dict(zip(encoder.feature_names, encoder.update(frame)))
        self.assertAlmostEqual(values["device/Device_Cooking/on_fraction/mean"], 2 / 3)
        self.assertEqual(values["device/Device_Cooking/on_fraction/changes"], 2)
        self.assertEqual(values["observed_window_seconds"], 90)
        other = FeatureEncoder(self.catalogue, 120)
        fresh = dict(zip(other.feature_names, other.update(frame)))
        self.assertEqual(fresh["observed_window_seconds"], 0)
        self.assertEqual(fresh["device/Device_Cooking/on_fraction/mean"], 0)


class TransferDeviceTypeTests(unittest.TestCase):
    @staticmethod
    def combo(values, selected):
        options = {"values": tuple(values)}
        selection = {"value": selected}
        combo = MagicMock()
        combo.get.side_effect = lambda: selection["value"]
        combo.set.side_effect = lambda value: selection.update(value=value)
        combo.current.side_effect = lambda index: selection.update(value=options["values"][index])
        combo.configure.side_effect = lambda **kwargs: options.update(kwargs)
        combo.config.side_effect = combo.configure.side_effect
        combo.__getitem__.side_effect = options.__getitem__
        combo.__setitem__.side_effect = options.__setitem__
        return combo

    def test_basic_target_types_are_available_and_receive_shared_categories(self):
        for kind, category in (("Microwave", "Device_Cooking"), ("TV", "Device_Entertainment"),
                               ("PlayStation", "Device_Entertainment")):
            with self.subTest(kind=kind):
                self.assertIn(kind, device.DEVICE_TYPES)
                params = device.get_device_params(kind)
                self.assertGreater(params["power"], 0)
                self.assertEqual(params["min_consumption"], params["max_consumption"])
                self.assertEqual(categories_for({"type": kind}, "device", default_catalogue()), [category])
        for kind in ("Bed", "Sofa", "Chair", "Shower", "Sink", "Toilet"):
            self.assertEqual(device.get_device_params(kind), {"power": 0, "min_consumption": 0, "max_consumption": 0})

    def test_device_creation_dropdown_contains_the_target_types_and_existing_lights(self):
        dialog = device.DeviceDialog.__new__(device.DeviceDialog)
        with patch.object(device.tk, "Label"), patch.object(device.tk, "Entry"), \
                patch.object(device.ttk, "Combobox") as combo, \
                patch.object(device, "load_catalogue", return_value=default_catalogue()):
            dialog.body(Mock())
        choices = next(call.kwargs["values"] for call in combo.call_args_list
                       if tuple(call.kwargs["values"]) == device.DEVICE_TYPES)
        self.assertTrue({"Microwave", "TV", "PlayStation", "Normal Light", "Smart Light"}.issubset(choices))
        category_choices = next(call.kwargs["values"] for call in combo.call_args_list
                                if "Device_Cooking" in call.kwargs["values"])
        self.assertEqual(category_choices[0], "All devices")

    def test_unavailable_catalogue_keeps_device_creation_available_without_modal_dialogs(self):
        for error in (OSError("catalogue is unreadable"), ValueError("invalid catalogue")):
            with self.subTest(error=type(error).__name__):
                dialog = device.DeviceDialog.__new__(device.DeviceDialog)
                with patch.object(device.tk, "Label") as label, patch.object(device.tk, "Entry"), \
                        patch.object(device.ttk, "Combobox") as combo, \
                        patch.object(device, "load_catalogue", side_effect=error), \
                        patch.object(device, "messagebox") as messagebox:
                    dialog.body(Mock())
                choices = [tuple(call.kwargs["values"]) for call in combo.call_args_list]
                self.assertIn(("All devices",), choices)
                self.assertIn(device.DEVICE_TYPES, choices)
                self.assertEqual(dialog.device_groups, {})
                self.assertTrue(any("Categories unavailable" in call.kwargs.get("text", "")
                                    for call in label.call_args_list))
                self.assertEqual(messagebox.mock_calls, [])

    def test_category_groups_use_shared_roles_and_only_offer_creatable_types(self):
        dictionary = default_catalogue()
        before = deepcopy(dictionary)

        groups = device.device_types_by_category(dictionary)

        self.assertEqual({kind for types in groups.values() for kind in types}, set(device.DEVICE_TYPES))
        self.assertEqual(groups["Device_Cooking"], ("Oven", "Microwave", "Toaster"))
        self.assertEqual(groups["Device_Computer"], ("Computer",))
        self.assertIn("Computer", groups["Device_Entertainment"])
        self.assertIn("TV", groups["Device_Entertainment"])
        self.assertIn("PlayStation", groups["Device_Entertainment"])
        self.assertNotIn("Stove", {kind for types in groups.values() for kind in types})
        self.assertEqual(dictionary, before)

    def test_category_groups_follow_custom_legacy_and_unclassified_mappings(self):
        dictionary = default_catalogue()
        dictionary["device_categories"]["CustomRole"] = "A locally configured role."
        dictionary["device_types"]["Microwave"] = ["CustomRole"]
        dictionary["device_types"]["Oven"] = ["DeviceCooking"]
        dictionary["device_types"]["TV"] = []
        del dictionary["device_types"]["Toaster"]
        dictionary["device_types"]["Unavailable custom appliance"] = ["CustomRole"]

        groups = device.device_types_by_category(dictionary)

        self.assertEqual(groups["CustomRole"], ("Microwave",))
        self.assertEqual(groups["Device_Cooking"], ("Oven",))
        self.assertIn("TV", groups["Unclassified"])
        self.assertIn("Toaster", groups["Unclassified"])
        self.assertEqual({kind for types in groups.values() for kind in types}, set(device.DEVICE_TYPES))
        self.assertNotIn("Unavailable custom appliance", {kind for types in groups.values() for kind in types})

    def test_custom_category_names_remain_distinct_from_all_and_unclassified_options(self):
        dictionary = default_catalogue()
        custom_groups = {
            "All devices": "Oven",
            "All devices (all types)": "Microwave",
            "Unclassified": "TV",
            "Unclassified (no categories)": "PlayStation",
        }
        for category, kind in custom_groups.items():
            dictionary["device_categories"][category] = "A real custom role."
            dictionary["device_types"][kind] = [category]
        dictionary["device_types"]["Toaster"] = []
        before = deepcopy(dictionary)
        dialog = device.DeviceDialog.__new__(device.DeviceDialog)
        with patch.object(device.tk, "Label"), patch.object(device.tk, "Entry"), \
                patch.object(device.ttk, "Combobox", side_effect=lambda _master, **kwargs: self.combo(kwargs["values"], "")), \
                patch.object(device, "load_catalogue", return_value=dictionary):
            dialog.body(Mock())

        for category, kind in custom_groups.items():
            self.assertEqual(dialog.device_groups[category], (kind,))
        no_categories = "Unclassified (no categories) (no categories)"
        self.assertEqual(dialog.device_groups[no_categories], ("Toaster",))
        choices = tuple(dialog.device_category["values"])
        self.assertEqual(len(choices), len(set(choices)))
        self.assertEqual(choices[0], "All devices (all types) (all types)")
        self.assertEqual(dialog.device_category.get(), choices[0])
        self.assertEqual(tuple(dialog.device_type["values"]), device.DEVICE_TYPES)

        for category, kind in (*custom_groups.items(), (no_categories, "Toaster")):
            dialog.device_category.set(category)
            dialog.on_category_selected()
            self.assertEqual(tuple(dialog.device_type["values"]), (kind,))
            self.assertEqual(dialog.device_type.get(), kind)
        dialog.device_category.set(choices[0])
        dialog.on_category_selected()
        self.assertEqual(tuple(dialog.device_type["values"]), device.DEVICE_TYPES)
        self.assertEqual(dictionary, before)

    def test_category_switch_keeps_valid_type_replaces_other_type_and_restores_all(self):
        dictionary = default_catalogue()
        before = deepcopy(dictionary)
        dialog = device.DeviceDialog.__new__(device.DeviceDialog)
        dialog.device_groups = device.device_types_by_category(dictionary)
        dialog.device_category = self.combo(dialog.device_groups, "Device_Entertainment")
        dialog.device_type = self.combo(device.DEVICE_TYPES, "Computer")
        dialog.on_device_type_selected = Mock()

        dialog.on_category_selected()
        self.assertEqual(dialog.device_type.get(), "Computer")
        self.assertEqual(tuple(dialog.device_type["values"]), dialog.device_groups["Device_Entertainment"])
        dialog.device_category.set("Device_Computer")
        dialog.on_category_selected()
        self.assertEqual(dialog.device_type.get(), "Computer")
        dialog.device_category.set("Device_Cooking")
        dialog.on_category_selected()
        self.assertEqual(dialog.device_type.get(), "Oven")
        self.assertEqual(tuple(dialog.device_type["values"]), ("Oven", "Microwave", "Toaster"))
        dialog.device_category.set("All devices")
        dialog.on_category_selected()
        self.assertEqual(dialog.device_type.get(), "Oven")
        self.assertEqual(tuple(dialog.device_type["values"]), device.DEVICE_TYPES)
        self.assertEqual(dialog.on_device_type_selected.call_count, 4)
        self.assertEqual(dictionary, before)
        self.assertEqual(categories_for({"type": "Computer"}, "device", dictionary), [
            "Device_Computer", "Device_Entertainment",
        ])

    def test_target_meters_use_their_profiles_when_on_and_zero_when_off(self):
        for kind in ("Microwave", "TV", "PlayStation"):
            params = device.get_device_params(kind)
            appliance = Device("appliance", 0, 0, kind, params["power"], 1,
                               params["min_consumption"], params["max_consumption"])
            meter = Sensor("meter", 0, 0, "Smart Meter", 0, 5000, 1, 0, associated_device=appliance.name)
            now = datetime(2026, 1, 1)
            cycles = {appliance.name: {"start_time": now, "cycle_type": kind}}
            with self.subTest(kind=kind), patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=None), \
                    patch("consumption_profiles.add_noise", side_effect=float):
                reading = sensor.compute_smartmeter_consumption(meter, [appliance], 1, now, cycles)
                self.assertEqual(reading, params["power"])
                appliance.current_consumption = params["power"] / 2
                self.assertEqual(sensor.compute_smartmeter_consumption(meter, [appliance], 1, now, cycles), params["power"])
                appliance.state = 0
                self.assertEqual(sensor.compute_smartmeter_consumption(meter, [appliance], 1, now, cycles), 0)

    def test_unprofiled_meter_fallback_preserves_configured_and_current_power(self):
        appliance = Device("custom", 0, 0, "Custom appliance", 300, 1, 100, 300)
        meter = Sensor("meter", 0, 0, "Smart Meter", 0, 5000, 1, 0, associated_device=appliance.name)
        with patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=None):
            self.assertEqual(sensor.compute_smartmeter_consumption(meter, [appliance], 1, datetime(2026, 1, 1)), 300)
            appliance.current_consumption = 150
            self.assertEqual(sensor.compute_smartmeter_consumption(meter, [appliance], 1, datetime(2026, 1, 1)), 150)
            appliance.state = 0
            self.assertEqual(sensor.compute_smartmeter_consumption(meter, [appliance], 1, datetime(2026, 1, 1)), 0)

    def test_authoritative_llm_zero_is_not_replaced_by_generic_fallback(self):
        appliance = Device("appliance", 0, 0, "Microwave", 1000, 1, 1000, 1000)
        meter = Sensor("meter", 0, 0, "Smart Meter", 0, 5000, 1, 0, associated_device=appliance.name)
        with patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=0):
            self.assertEqual(sensor.compute_smartmeter_consumption(meter, [appliance], 1, datetime(2026, 1, 1)), 0)


if __name__ == "__main__":
    unittest.main()
