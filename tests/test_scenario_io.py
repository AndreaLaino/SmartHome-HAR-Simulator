import json
import tempfile
import unittest
from pathlib import Path

from app.io.scenario import _convert_timestamp, _parse_json_to_records
from read import read_coordinates_from_file, read_room_overrides


class JsonImportTests(unittest.TestCase):
    def test_smartmeter_zero_values_are_preserved(self):
        content = json.dumps(
            {
                "ts": 1700000000000,
                "apower": 0,
                "voltage": 0,
                "current": 0,
                "energy_total": 0,
            }
        )
        record = _parse_json_to_records(content, "smartmeter")[0]

        self.assertNotEqual(record["timestamp"], "")
        self.assertEqual(record["power"], 0)
        self.assertEqual(record["voltage"], 0)
        self.assertEqual(record["current"], 0)
        self.assertEqual(record["energy"], 0)

    def test_iso_timestamp_is_accepted(self):
        self.assertEqual(
            _convert_timestamp("2026-06-12T10:30:45.123"),
            "2026-06-12 10:30:45.123",
        )

    def test_invalid_json_lines_are_skipped(self):
        content = '{"ts": 1700000000, "apower": 10}\nnot-json'
        self.assertEqual(len(_parse_json_to_records(content, "smartmeter")), 1)


class ScenarioReaderTests(unittest.TestCase):
    def test_scenario_sections_are_loaded_and_runtime_state_is_reset(self):
        scenario = """Positions
p1,0,0
p2,10,0

Walls
p1,p2

Sensors
pir,1,1,PIR,0,1,1,1,0,None,None
meter,2,2,Smart Meter,0,5000,10,1,None,50,washer

Devices
washer,3,3,Washing_Machine,500,1,300,500,400,-1

Doors
0,0,10,0,open

Rooms
room-abc123,Kitchen
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scenario.csv"
            path.write_text(scenario, encoding="utf-8")
            points, walls, sensors, devices, doors = read_coordinates_from_file(path)

        self.assertEqual([(point.name, point.x, point.y) for point in points], [("p1", 0, 0), ("p2", 10, 0)])
        self.assertEqual((walls[0].x1, walls[0].y1, walls[0].x2, walls[0].y2), (0, 0, 10, 0))
        self.assertEqual(sensors[0].state, 0.0)
        self.assertEqual(sensors[1].consumption, 0.0)
        self.assertEqual(sensors[1].associated_device, "washer")
        self.assertEqual(devices[0].state, 0)
        self.assertEqual(devices[0].current_consumption, 0)
        self.assertEqual(devices[0].consumption_direction, 1)
        self.assertEqual(doors[0].state, "open")
        self.assertEqual(read_room_overrides, {"room-abc123": "Kitchen"})


if __name__ == "__main__":
    unittest.main()
