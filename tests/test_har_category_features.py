"""Observable category channels transfer independently of device identity."""

from copy import deepcopy
from datetime import datetime
import unittest
from unittest.mock import Mock, patch

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
                         "categories": ["DeviceCooking"]}],
            "sensors": [{"name": "meter-a", "type": "Smart Meter", "min_val": 0,
                         "max_val": 1000, "state": 1, "consumption": 500,
                         "associated_device": "oven-a", "categories": ["SensorPower"]}],
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
        self.assertEqual(self.channels(frame)["device/DeviceCooking/available"], 1)
        frame["devices"][0]["categories"] = []
        self.assertEqual(self.channels(frame)["device/DeviceCooking/available"], 0)
        frame["devices"][0]["categories"] = ["DeviceCooking", "DeviceEntertainment"]
        channels = self.channels(frame)
        self.assertEqual(channels["device/DeviceCooking/on_fraction"], 1)
        self.assertEqual(channels["device/DeviceEntertainment/on_fraction"], 1)
        baseline = primitives(frame, self.catalogue)
        frame["devices"][0]["categories"].append("TargetOnlyCategory")
        self.assertEqual(primitives(frame, self.catalogue), baseline)

    def test_explicit_zero_meter_reading_overrides_on_state_and_stale_power(self):
        frame = self.frame()
        frame["sensor_states"]["meter-a"]["consumption"] = [500, 0]
        channels = self.channels(frame)
        self.assertEqual(channels["device/DeviceCooking/on_fraction"], 1)
        self.assertEqual(channels["device/DeviceCooking/power_w"], 0)
        self.assertEqual(channels["device/DeviceCooking/power_observed"], 1)
        self.assertEqual(channels["sensor/SensorPower/value"], 0)
        self.assertEqual(channels["sensor/SensorPower/observed"], 1)

    def test_duplicate_whole_device_meters_do_not_double_power(self):
        frame = self.frame()
        baseline = primitives(frame, self.catalogue)
        extra = deepcopy(frame["sensors"][0])
        extra["name"] = "meter-b"
        frame["sensors"].append(extra)
        frame["sensor_states"]["meter-b"] = {"consumption": [500]}
        self.assertEqual(self.channels(frame)["device/DeviceCooking/power_w"], 500)
        self.assertEqual(self.channels(frame)["device/DeviceCooking/power_fraction"], .5)
        self.assertEqual(primitives(frame, self.catalogue), baseline)

    def test_weight_scales_are_normalized_without_assuming_kilograms(self):
        frame = self.frame()
        frame["devices"] = []
        frame["sensors"] = [{"name": "load", "type": "Weight", "min_val": 0,
                             "max_val": 1, "categories": ["SensorWeight"]}]
        frame["sensor_states"] = {"load": {"state": [.5]}}
        self.assertEqual(self.channels(frame)["sensor/SensorWeight/normalized"], .5)
        frame["sensors"][0]["max_val"] = 100
        frame["sensor_states"]["load"]["state"] = [50]
        self.assertEqual(self.channels(frame)["sensor/SensorWeight/normalized"], .5)
        self.assertEqual(self.channels(frame)["sensor/SensorWeight/value"], 50)
        self.assertEqual(self.catalogue["sensor_categories"]["SensorWeight"]["unit"], "configured")

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
        self.assertAlmostEqual(values["device/DeviceCooking/on_fraction/mean"], 2 / 3)
        self.assertEqual(values["device/DeviceCooking/on_fraction/changes"], 2)
        self.assertEqual(values["observed_window_seconds"], 90)
        other = FeatureEncoder(self.catalogue, 120)
        fresh = dict(zip(other.feature_names, other.update(frame)))
        self.assertEqual(fresh["observed_window_seconds"], 0)
        self.assertEqual(fresh["device/DeviceCooking/on_fraction/mean"], 0)


class TransferDeviceTypeTests(unittest.TestCase):
    def test_basic_target_types_are_available_and_receive_shared_categories(self):
        for kind, category in (("Microwave", "DeviceCooking"), ("TV", "DeviceEntertainment"),
                               ("PlayStation", "DeviceEntertainment")):
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
        with patch.object(device.tk, "Label"), patch.object(device.tk, "Entry"), patch.object(device.ttk, "Combobox") as combo:
            dialog.body(Mock())
        choices = combo.call_args_list[0].kwargs["values"]
        self.assertTrue({"Microwave", "TV", "PlayStation", "Normal Light", "Smart Light"}.issubset(choices))

    def test_generic_target_meters_use_simulated_power_when_on_and_zero_when_off(self):
        for kind in ("Microwave", "TV", "PlayStation"):
            params = device.get_device_params(kind)
            appliance = Device("appliance", 0, 0, kind, params["power"], 1,
                               params["min_consumption"], params["max_consumption"])
            meter = Sensor("meter", 0, 0, "Smart Meter", 0, 5000, 1, 0, associated_device=appliance.name)
            with self.subTest(kind=kind), patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=None):
                reading = sensor.compute_smartmeter_consumption(meter, [appliance], 1, datetime(2026, 1, 1))
                self.assertEqual(reading, params["power"])
                appliance.current_consumption = params["power"] / 2
                self.assertEqual(sensor.compute_smartmeter_consumption(meter, [appliance], 1, datetime(2026, 1, 1)), params["power"] / 2)
                appliance.state = 0
                self.assertEqual(sensor.compute_smartmeter_consumption(meter, [appliance], 1, datetime(2026, 1, 1)), 0)

    def test_authoritative_llm_zero_is_not_replaced_by_generic_fallback(self):
        appliance = Device("appliance", 0, 0, "Microwave", 1000, 1, 1000, 1000)
        meter = Sensor("meter", 0, 0, "Smart Meter", 0, 5000, 1, 0, associated_device=appliance.name)
        with patch.object(sensor, "_get_llm_smartmeter_consumption", return_value=0):
            self.assertEqual(sensor.compute_smartmeter_consumption(meter, [appliance], 1, datetime(2026, 1, 1)), 0)


if __name__ == "__main__":
    unittest.main()
