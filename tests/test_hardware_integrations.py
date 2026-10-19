try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from app.hardware import smartmeter
from app.ui import bindings


class SmartMeterHardwareTests(unittest.TestCase):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    def test_gen2_preserves_zero_power(self):
        response = self.Response(
            {"voltage": 230.0, "apower": 0.0, "power": 99.0, "current": 0.0}
        )
        with patch.object(smartmeter.requests, "get", return_value=response):
            reading = smartmeter._get_voltage_gen2("192.0.2.1")

        self.assertEqual(reading, (230.0, 0.0, 0.0))

    def test_gen1_preserves_zero_meter_values(self):
        response = self.Response(
            {
                "meters": [{"voltage": 0.0, "power": 0.0, "current": 0.0}],
                "voltage": 230.0,
                "power": 99.0,
                "current": 1.0,
            }
        )
        with patch.object(smartmeter.requests, "get", return_value=response):
            reading = smartmeter._get_voltage_gen1("192.0.2.1")

        self.assertEqual(reading, (0.0, 0.0, 0.0))


class HardwareAutostartTests(unittest.TestCase):
    def test_stale_ip_mapping_is_not_started_without_matching_sensor(self):
        mapping = {
            "old_meter": {
                "by": "ip",
                "value": "192.0.2.1",
                "interval": 10,
            }
        }
        with (
            patch.object(bindings, "_load_sensor_map_json", return_value=mapping),
            patch.object(bindings, "_all_sensor_names", return_value=[]),
            patch("app.hardware.smartmeter.start_logger") as start_logger,
        ):
            bindings.autostart_bound_ip_loggers({})

        start_logger.assert_not_called()

    def test_stale_gpio_mapping_is_not_started_without_matching_sensor(self):
        mapping = {
            "old_pir": {
                "by": "gpio",
                "kind": "pir",
                "gpio": 17,
                "interval": 10,
            }
        }
        with (
            patch.object(bindings, "_load_sensor_map_json", return_value=mapping),
            patch.object(bindings, "_all_sensor_names", return_value=[]),
            patch("app.hardware.real_sensors.start_bound_logger") as start_logger,
        ):
            bindings.autostart_bound_gpio_loggers({})

        start_logger.assert_not_called()


class PackagingMetadataTests(unittest.TestCase):
    def test_runtime_dependency_and_root_modules_are_packaged(self):
        project_root = Path(__file__).resolve().parents[1]
        with (project_root / "pyproject.toml").open("rb") as stream:
            config = tomllib.load(stream)

        dependencies = config["project"]["dependencies"]
        modules = set(config["tool"]["setuptools"]["py-modules"])

        self.assertTrue(any(item.startswith("requests") for item in dependencies))
        self.assertTrue(
            {"canvas_zoom", "label_layout", "room_recognition"}.issubset(modules)
        )


if __name__ == "__main__":
    unittest.main()
