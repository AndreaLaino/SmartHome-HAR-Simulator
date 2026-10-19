import csv
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.io.scenario import (
    _convert_timestamp,
    _parse_json_to_records,
    _save_records_to_csv,
    _write_scenario,
    merge_smartmeter_files,
    save_scenario_as,
)
from models import Point, Wall
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

    def test_raw_s3_csv_rows_are_preserved(self):
        content = (
            "timestamp_iso,device,device_id,ip,power_W,voltage_V,current_A\n"
            "2026-01-01 10:00:00,washer,WASHER,192.0.2.1,0,230,0\n"
            "2026-01-01 10:01:00,washer,WASHER,192.0.2.1,450,230,1.96\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "smartmeter.csv"
            _save_records_to_csv(
                [{"raw_csv": content}],
                str(path),
                "smartmeter",
            )
            with path.open("r", newline="", encoding="utf-8") as stream:
                rows = list(csv.reader(stream))

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][0], "2026-01-01 10:00:00")
        self.assertEqual(rows[1][4], "0")
        self.assertEqual(rows[2][4], "450")

    def test_raw_s3_gpio_csv_keeps_its_original_schema(self):
        content = (
            "timestamp_iso,label,kind,gpio,value\n"
            "2026-01-01 10:00:00,entrance,pir,17,1\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pir.csv"
            _save_records_to_csv([{"raw_csv": content}], str(path), "pir")
            with path.open("r", newline="", encoding="utf-8") as stream:
                rows = list(csv.reader(stream))

        self.assertEqual(rows[0], ["timestamp_iso", "label", "kind", "gpio", "value"])
        self.assertEqual(rows[1], ["2026-01-01 10:00:00", "entrance", "pir", "17", "1"])

    def test_merge_uses_first_non_empty_file_for_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "smartmeter_a_1.csv").write_text("", encoding="utf-8")
            (root / "smartmeter_a_2.csv").write_text(
                "timestamp,power\n2026-01-01 10:00:00,25\n",
                encoding="utf-8",
            )

            merged = merge_smartmeter_files(tmpdir)
            output = (root / "smartmeter_a.csv").read_text(encoding="utf-8")

        self.assertTrue(merged)
        self.assertEqual(
            output.replace("\r\n", "\n"),
            "timestamp,power\n2026-01-01 10:00:00,25\n",
        )


class ScenarioReaderTests(unittest.TestCase):
    def test_cancelled_save_as_does_not_change_current_file(self):
        ctx = SimpleNamespace(current_file="existing.csv")
        with tempfile.TemporaryDirectory() as tmpdir:
            destination = str(Path(tmpdir) / "cancelled.csv")
            with (
                patch("app.io.scenario.ensure_houses_dir", return_value=Path(tmpdir)),
                patch("app.io.scenario.ask_save_file", return_value=destination),
                patch("app.io.scenario.ask_confirmation", return_value=False),
            ):
                save_scenario_as(ctx)

            self.assertEqual(ctx.current_file, "existing.csv")
            self.assertFalse(Path(destination).exists())

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

    def test_edited_runtime_wall_geometry_is_saved(self):
        runtime_points = [Point("a", 25, 30), Point("b", 125, 30)]
        edited_wall = Wall(25, 30, 125, 30)
        ctx = SimpleNamespace(load_active=False, room_overrides={})

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "edited.csv"
            with (
                patch("app.io.scenario.ask_confirmation", return_value=True),
                patch("app.io.scenario.messagebox.showinfo"),
                patch("app.io.scenario.points", runtime_points),
                patch("app.io.scenario.sensors", []),
                patch("app.io.scenario.devices", []),
                patch("app.io.scenario.doors", []),
                patch("wall.walls_coordinates", [edited_wall]),
            ):
                _write_scenario(ctx, str(path))
            saved = path.read_text(encoding="utf-8")

        self.assertIn("Walls\n", saved.replace("\r\n", "\n"))
        self.assertIn("a,b", saved)


if __name__ == "__main__":
    unittest.main()
