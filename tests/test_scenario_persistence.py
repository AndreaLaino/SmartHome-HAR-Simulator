"""File-based regression tests without dialogs, network access, or a Tk root."""

import csv
import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app.io.scenario as scenario
import app.save_paths as save_paths
import read
from app.context import AppContext
from models import Device, Door, Point, Sensor, Wall


class ScenarioFilesTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        # The reader exposes these lists to the UI: isolate them without
        # changing the aliases owned by a running application or another test.
        for name in (
            "coordinates", "read_walls", "read_sensors", "read_devices",
            "read_doors", "read_walls_coordinates",
        ):
            self.stack.enter_context(patch.object(read, name, []))
        self.stack.enter_context(patch.object(read, "read_room_overrides", {}))
        self.stack.enter_context(patch.object(scenario, "ask_confirmation", return_value=True))
        self.info = self.stack.enter_context(patch.object(scenario.messagebox, "showinfo"))

    def write_input(self, content, name="input.csv", encoding="utf-8"):
        path = self.root / name
        path.write_text(content, encoding=encoding)
        return path

    def test_reader_resolves_walls_declared_before_points(self):
        path = self.write_input("Walls\na,b\na,missing\nPositions\na,5,10\nb,25,30\n")
        with redirect_stdout(io.StringIO()):
            points, walls, *_ = read.read_coordinates_from_file(path)
        self.assertEqual(points, [Point("a", 5, 10), Point("b", 25, 30)])
        self.assertEqual(walls, [Wall(5, 10, 25, 30)])

    def test_invalid_rows_do_not_prevent_valid_rows_in_each_section(self):
        path = self.write_input(
            "Positions\nbad,not-a-number,1\ntoo,short\na,0,0\nb,10,0\n"
            "Walls\na\na,b\nSensors\nbroken,1\n"
            "invalid,1,2,Temperature,bad,30,1,20,None,None,None\n"
            "temp,1,2,Temperature,0,30,1,20,None,None,None\n"
            "Devices\nshort,1\nbad,1,2,Oven,no,1,0,100,20,1\n"
            "oven,1,2,Oven,100,1,0,100,20,-1\n"
            "Doors\nbad,0,1,0,close\n0,0,10,0,close\n"
            "Rooms\n,Kitchen\nroom-1,\n room-2 , Kitchen \n"
        )
        with redirect_stdout(io.StringIO()):
            points, walls, sensors, devices, doors = read.read_coordinates_from_file(path)
        self.assertEqual([len(items) for items in (points, walls, sensors, devices, doors)], [2, 1, 1, 1, 1])
        self.assertEqual(sensors[0].state, 20)
        self.assertIsNone(sensors[0].direction)
        self.assertIsNone(sensors[0].associated_device)
        self.assertEqual(read.read_room_overrides, {"room-2": "Kitchen"})

    def test_consecutive_reads_replace_objects_and_room_labels_in_place(self):
        first = self.write_input("Positions\na,0,0\nRooms\nroom-1,Kitchen\n")
        second = self.write_input("Positions\nb,1,2\n", "second.csv")
        old_points, *_ = read.read_coordinates_from_file(first)
        new_points, *rest = read.read_coordinates_from_file(second)
        self.assertIs(old_points, new_points)
        self.assertEqual(new_points, [Point("b", 1, 2)])
        self.assertTrue(all(not items for items in rest))
        self.assertEqual(read.read_room_overrides, {})

    def test_section_names_can_also_be_object_names(self):
        path = self.write_input(
            "Positions\nWalls,0,0\nSensors,10,0\nWalls\nWalls,Sensors\n"
            "Sensors\nDevices,1,2,Temperature,0,30,1,20,None,None,None\n"
        )
        points, walls, sensors, *_ = read.read_coordinates_from_file(path)
        self.assertEqual([point.name for point in points], ["Walls", "Sensors"])
        self.assertEqual(walls, [Wall(0, 0, 10, 0)])
        self.assertEqual([sensor.name for sensor in sensors], ["Devices"])

    def test_spreadsheet_padded_section_headers_remain_compatible(self):
        path = self.write_input("Positions,,\na,0,0\nb,10,0\nWalls, ,\na,b\n")
        points, walls, *_ = read.read_coordinates_from_file(path)
        self.assertEqual(points, [Point("a", 0, 0), Point("b", 10, 0)])
        self.assertEqual(walls, [Wall(0, 0, 10, 0)])

    def test_utf8_bom_and_international_names_are_read(self):
        path = self.write_input("Positions\n入口,1,2\nRooms\n部屋,Cucina 🏠\n", encoding="utf-8-sig")
        points, *_ = read.read_coordinates_from_file(path)
        self.assertEqual(points[0].name, "入口")
        self.assertEqual(read.read_room_overrides, {"部屋": "Cucina 🏠"})

    def test_legacy_windows_encoded_scenario_remains_readable(self):
        path = self.write_input("Positions\nCafé,1,2\n", encoding="cp1252")
        points, *_ = read.read_coordinates_from_file(path)
        self.assertEqual(points, [Point("Café", 1, 2)])

    def test_device_configuration_keeps_fractional_watt_values(self):
        path = self.write_input("Devices\ncomputer,1,2,Computer,125.5,0,10.25,125.75,0,1\n")
        _, _, _, devices, _ = read.read_coordinates_from_file(path)
        self.assertEqual((devices[0].power, devices[0].min_consumption, devices[0].max_consumption), (125.5, 10.25, 125.75))

    def test_loaded_home_round_trip_preserves_configuration_and_resets_runtime(self):
        ctx = AppContext(window=Mock(), load_active=True)
        ctx.r_points = [Point("入口, A", 0, 0), Point("b", 100, 0)]
        ctx.read_walls = [Wall(0, 0, 100, 0)]
        ctx.read_sensors = [
            Sensor("pir", 1, 2, "PIR", 0, 1, 1, 1, 90),
            Sensor("chair", 3, 4, "Weight", 0, 1, 1, 1),
            Sensor("temp", 5, 6, "Temperature", -10, 40, 0.5, 22.5),
            Sensor("switch", 7, 8, "Switch", 0, 1, 1, 1),
            Sensor("meter", 9, 10, "Smart Meter", 0, 5000, 1, 1, None, 125, "oven"),
        ]
        ctx.read_devices = [Device("oven", 20, 25, "Oven", 2500, 1, 100, 2500, 125, -1)]
        ctx.read_doors = [Door(0, 0, 25, 0, "close")]
        ctx.room_overrides = {"部屋": "Kitchen, dining"}
        path = self.root / "round_trip.csv"

        self.assertTrue(scenario._write_scenario(ctx, path))
        points, walls, sensors, devices, doors = read.read_coordinates_from_file(path)

        self.assertEqual(points, ctx.r_points)
        self.assertEqual(walls, ctx.read_walls)
        self.assertEqual(doors, ctx.read_doors)
        self.assertEqual([sensor.state for sensor in sensors], [0, 0, 22.5, 1, 0])
        self.assertEqual(sensors[0].direction, 90)
        self.assertEqual(sensors[-1].associated_device, "oven")
        self.assertEqual(sensors[-1].consumption, 0)
        self.assertEqual((devices[0].state, devices[0].current_consumption, devices[0].consumption_direction), (0, 0, 1))
        self.assertEqual((devices[0].power, devices[0].min_consumption, devices[0].max_consumption), (2500, 100, 2500))
        self.assertEqual(read.read_room_overrides, ctx.room_overrides)
        # Saving must not stop devices or reset the current on-screen run.
        self.assertEqual(ctx.read_devices[0].state, 1)
        self.assertEqual(ctx.read_devices[0].current_consumption, 125)
        self.assertEqual(ctx.read_sensors[0].state, 1)
        self.assertEqual(ctx.read_sensors[-1].consumption, 125)
        self.assertIn("入口".encode("utf-8"), path.read_bytes())

    def test_declined_save_leaves_existing_file_and_context_untouched(self):
        path = self.write_input("original bytes")
        ctx = SimpleNamespace(current_file="original.csv")
        with patch.object(scenario, "ask_confirmation", return_value=False):
            self.assertFalse(scenario._write_scenario(ctx, path))
        self.assertEqual(path.read_text(encoding="utf-8"), "original bytes")
        self.assertEqual(ctx.current_file, "original.csv")
        self.info.assert_not_called()

    def test_save_as_updates_current_file_only_after_successful_write(self):
        ctx = SimpleNamespace(current_file="old.csv")
        destination = str(self.root / "new.csv")
        with (
            patch.object(scenario, "ensure_houses_dir", return_value=self.root),
            patch.object(scenario, "ask_save_file", return_value=destination),
            patch.object(scenario, "_write_scenario", return_value=True) as write,
        ):
            scenario.save_scenario_as(ctx)
        write.assert_called_once_with(ctx, destination)
        self.assertEqual(ctx.current_file, destination)

    def test_save_dispatches_to_existing_path_or_save_as(self):
        for current_file in (None, "existing.csv"):
            with (
                self.subTest(current_file=current_file),
                patch.object(scenario, "save_scenario_as") as save_as,
                patch.object(scenario, "_write_scenario") as write,
            ):
                ctx = SimpleNamespace(current_file=current_file)
                scenario.save_scenario(ctx)
                if current_file is None:
                    save_as.assert_called_once_with(ctx)
                    write.assert_not_called()
                else:
                    write.assert_called_once_with(ctx, current_file)
                    save_as.assert_not_called()

    def test_missing_load_preserves_existing_scenario(self):
        ctx = AppContext(window=Mock(), current_file="existing.csv")
        with (
            patch.object(scenario, "_clear_scenario") as clear,
            patch.object(scenario, "_load_scenario") as load,
            patch.object(scenario.messagebox, "showwarning") as warning,
        ):
            self.assertFalse(scenario.load_scenario_from_path(ctx, Mock(), self.root / "missing.csv"))
        clear.assert_not_called()
        load.assert_not_called()
        warning.assert_called_once()
        self.assertEqual(ctx.current_file, "existing.csv")

    def test_existing_load_clears_before_loading_absolute_path(self):
        path = self.write_input("Positions\na,0,0\n")
        calls = []
        ctx, canvas = Mock(), Mock()
        with (
            patch.object(scenario, "_clear_scenario", side_effect=lambda *_: calls.append("clear")),
            patch.object(scenario, "_load_scenario", side_effect=lambda *_: calls.append("load")) as load,
        ):
            self.assertTrue(scenario.load_scenario_from_path(ctx, canvas, path))
        self.assertEqual(calls, ["clear", "load"])
        load.assert_called_once()
        loaded_ctx, loaded_canvas, loaded_path = load.call_args.args
        self.assertIs(loaded_ctx, ctx)
        self.assertIs(loaded_canvas, canvas)
        self.assertTrue(os.path.isabs(loaded_path))
        # Windows runners may use an 8.3 alias (RUNNER~1) for their temp
        # directory. Absolute paths need not use the spelling from resolve().
        self.assertTrue(os.path.samefile(loaded_path, path))

    def test_cancelled_open_does_not_clear_current_home(self):
        with (
            patch.object(scenario, "ask_open_file", return_value=""),
            patch.object(scenario, "_clear_scenario") as clear,
            patch.object(scenario, "_load_scenario") as load,
        ):
            scenario.open_scenario(Mock(), Mock())
        clear.assert_not_called()
        load.assert_not_called()

    def test_load_publishes_objects_and_draws_before_autostarting_bound_loggers(self):
        path = self.write_input("Positions\na,0,0\nb,10,0\nWalls\na,b\nRooms\nroom-1,Kitchen\n")
        ctx, canvas = AppContext(window=Mock()), Mock()
        events = []
        with ExitStack() as local:
            for name in ("draw_points", "draw_walls", "draw_sensors", "draw_devices", "draw_doors"):
                local.enter_context(patch.object(scenario, name, side_effect=lambda *_, action=name: events.append(action)))
            fov = local.enter_context(patch("utils.set_pir_fov_sources"))
            local.enter_context(patch("utils.refresh_pir_fov"))
            local.enter_context(patch("app.ui.rooms.refresh_rooms", side_effect=lambda *_, **__: events.append("rooms")))
            ip = local.enter_context(patch("app.ui.bindings.autostart_bound_ip_loggers", side_effect=lambda *_: events.append("ip")))
            gpio = local.enter_context(patch("app.ui.bindings.autostart_bound_gpio_loggers", side_effect=lambda *_: events.append("gpio")))
            scenario._load_scenario(ctx, canvas, str(path))
        self.assertTrue(ctx.load_active)
        self.assertEqual(ctx.current_file, str(path))
        self.assertIs(ctx.r_points, read.coordinates)
        self.assertIs(ctx.read_walls, read.read_walls)
        self.assertEqual(ctx.room_overrides, {"room-1": "Kitchen"})
        self.assertEqual(events, ["draw_points", "draw_walls", "draw_sensors", "draw_devices", "draw_doors", "rooms", "ip", "gpio"])
        ip.assert_called_once_with(ctx.house_state.sensor_states())
        gpio.assert_called_once_with(ctx.house_state.sensor_states())
        fov.assert_called_once_with(canvas, ctx.read_sensors, read.read_walls_coordinates, ctx.read_doors)

    def test_clear_removes_wall_geometry_and_all_runtime_collections(self):
        ctx = AppContext(window=Mock(), load_active=True, current_file="old.csv")
        ctx.r_points = [Point("p", 0, 0)]
        ctx.read_walls = [Wall(0, 0, 10, 0)]
        ctx.read_sensors = [object()]
        ctx.read_devices = [object()]
        ctx.read_doors = [object()]
        ctx.rooms = [object()]
        ctx.room_overrides = {"room": "Kitchen"}
        ctx.house_state.sensor_states()["pir"] = {"state": [1]}
        ctx._activate_canvas_select = Mock()
        canvas = Mock()
        with ExitStack() as local:
            stores = [local.enter_context(patch.object(scenario, name, [object()])) for name in ("points", "walls", "sensors", "devices", "doors")]
            geometric_walls = local.enter_context(patch("wall.walls_coordinates", [Wall(0, 0, 5, 0)]))
            read.read_walls_coordinates.append(Wall(0, 0, 10, 0))
            local.enter_context(patch("label_layout.forget_canvas_labels"))
            local.enter_context(patch("sensor.reset_temperature_runtime_state"))
            scenario._clear_scenario(ctx, canvas)
            self.assertEqual(geometric_walls, [])
            self.assertEqual(read.read_walls_coordinates, [])
            self.assertTrue(all(not store for store in stores))
        self.assertTrue(all(not items for items in (ctx.r_points, ctx.read_walls, ctx.read_sensors, ctx.read_devices, ctx.read_doors, ctx.rooms, ctx.room_overrides)))
        self.assertEqual(ctx.house_state.sensor_states(), {})
        self.assertFalse(ctx.load_active)
        self.assertIsNone(ctx.current_file)
        self.assertEqual(ctx._movement_undo_stack, [])
        self.assertEqual(ctx._selected_objects, [])
        ctx._activate_canvas_select.assert_called_once_with()
        canvas.delete.assert_any_call("fov")


class ImportExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def read_rows(self, path):
        with path.open(newline="", encoding="utf-8") as stream:
            return list(csv.reader(stream))

    def test_json_ignores_invalid_and_non_object_lines(self):
        content = '\n'.join(["null", "[]", "42", '"text"', "not-json", json.dumps({"ts": "2026-01-01T12:00:00", "power": 0})])
        records = scenario._parse_json_to_records(content, "smartmeter")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["power"], 0)

    def test_dht_json_preserves_zero_readings_and_iso_timestamps(self):
        records = scenario._parse_json_to_records(json.dumps({"timestamp_iso": "2026-01-01T12:00:00Z", "ts": 1, "label": "cold", "gpio": 0, "temperature_c": 0, "humidity_rh": 0}), "dht")
        self.assertEqual(records, [{"timestamp": "2026-01-01 12:00:00.000", "label": "cold", "gpio": 0, "temperature": 0, "humidity": 0}])

    def test_timestamp_rejects_invalid_values_without_interrupting_import(self):
        for value in (None, "", "   ", "not-a-date", {}, [], float("inf"), float("nan"), 10**100):
            with self.subTest(value=value):
                self.assertEqual(scenario._convert_timestamp(value), "")

    def test_numeric_timestamp_strings_and_milliseconds_are_equivalent(self):
        seconds = 1700000000
        self.assertEqual(scenario._convert_timestamp(seconds), scenario._convert_timestamp(seconds * 1000))
        self.assertEqual(scenario._convert_timestamp(seconds), scenario._convert_timestamp(str(seconds)))

    def test_smartmeter_append_sorts_and_replaces_duplicates(self):
        path = self.root / "meter.csv"
        scenario._save_records_to_csv([
            {"timestamp": "2026-01-01 12:00:02", "power": 20},
            {"timestamp": "2026-01-01 12:00:01", "power": 10},
        ], path, "smartmeter", device_name="oven", ip="192.0.2.1")
        scenario._save_records_to_csv([
            {"timestamp": "2026-01-01 12:00:01", "power": 0},
            {"timestamp": "2026-01-01 12:00:03", "power": 30},
            {"timestamp": "  ", "power": 999},
        ], path, "smartmeter", device_name="oven", ip="192.0.2.1", append_mode=True)
        rows = self.read_rows(path)
        self.assertEqual(len(rows), 4)
        self.assertEqual([row[4] for row in rows[1:]], ["0", "20", "30"])
        self.assertEqual(rows[1][1:4], ["oven", "oven", "192.0.2.1"])
        self.assertEqual([row[0] for row in rows[1:]], sorted(row[0] for row in rows[1:]))

    def test_dht_export_has_compatible_schema_and_zero_values(self):
        path = self.root / "temperature.csv"
        scenario._save_records_to_csv([{"timestamp": "2026-01-01 12:00:00", "label": "inside", "gpio": 4, "temperature": 0, "humidity": 0}], path, "dht")
        self.assertEqual(self.read_rows(path), [
            ["timestamp_iso", "label", "gpio", "temp_C", "hum_%"],
            ["2026-01-01 12:00:00", "inside", "4", "0", "0"],
        ])

    def test_raw_csv_append_preserves_quoted_fields_and_latest_reading(self):
        path = self.root / "gpio.csv"
        scenario._save_records_to_csv([{"raw_csv": 'timestamp_iso,label,kind,gpio,value\n2026-01-01 12:00:00,"front, door",switch,17,0\n'}], path, "switch")
        scenario._save_records_to_csv([{"raw_csv": 'timestamp_iso,label,kind,gpio,value\n2026-01-01 12:00:00,"front, door",switch,17,1\n2026-01-01 12:00:01,back,switch,18,0\n'}], path, "switch", append_mode=True)
        self.assertEqual(self.read_rows(path)[1:], [
            ["2026-01-01 12:00:00", "front, door", "switch", "17", "1"],
            ["2026-01-01 12:00:01", "back", "switch", "18", "0"],
        ])

    def test_empty_import_does_not_truncate_an_existing_file(self):
        path = self.root / "existing.csv"
        path.write_bytes(b"keep these bytes")
        scenario._save_records_to_csv([], path, "smartmeter", append_mode=False)
        self.assertEqual(path.read_bytes(), b"keep these bytes")

    def test_export_chooses_most_recent_interactions_file_recursively(self):
        old = self.root / "old" / "interactions.csv"
        new = self.root / "new" / "interactions" / "interactions.csv"
        for path, content, modified in ((old, b"old", 1000), (new, b"timestamp,value\r\n2026,1\r\n", 2000)):
            path.parent.mkdir(parents=True)
            path.write_bytes(content)
            os.utime(path, (modified, modified))
        destination = self.root / "export.csv"
        with (
            patch.object(scenario, "ensure_saves_dir", return_value=self.root),
            patch.object(scenario, "ask_save_file", return_value=str(destination)),
            patch.object(scenario.messagebox, "showinfo") as info,
        ):
            scenario.export_simulation_csv()
        self.assertEqual(destination.read_bytes(), new.read_bytes())
        self.assertEqual(old.read_bytes(), b"old")
        info.assert_called_once()

    def test_export_without_logs_warns_without_asking_for_destination(self):
        with (
            patch.object(scenario, "ensure_saves_dir", return_value=self.root),
            patch.object(scenario, "ask_save_file") as dialog,
            patch.object(scenario.messagebox, "showwarning") as warning,
        ):
            scenario.export_simulation_csv()
        warning.assert_called_once()
        dialog.assert_not_called()

    def test_cancelled_export_does_not_copy_any_files(self):
        (self.root / "interactions.csv").write_text("source", encoding="utf-8")
        with (
            patch.object(scenario, "ensure_saves_dir", return_value=self.root),
            patch.object(scenario, "ask_save_file", return_value=""),
            patch.object(scenario.shutil, "copyfile") as copy,
        ):
            scenario.export_simulation_csv()
        copy.assert_not_called()

    def test_export_io_failure_reports_an_error(self):
        (self.root / "interactions.csv").write_text("source", encoding="utf-8")
        with (
            patch.object(scenario, "ensure_saves_dir", return_value=self.root),
            patch.object(scenario, "ask_save_file", return_value="chosen.csv"),
            patch.object(scenario.shutil, "copyfile", side_effect=PermissionError("denied")),
            patch.object(scenario.messagebox, "showerror") as error,
            patch.object(scenario.messagebox, "showinfo") as info,
        ):
            scenario.export_simulation_csv()
        error.assert_called_once()
        info.assert_not_called()


class SaveSessionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, child in (("SAVES_DIR", "saves"), ("HOUSES_DIR", "houses"), ("DEVICES_DIR", "devices")):
            self.stack.enter_context(patch.object(save_paths, name, self.root / child))
        self.stack.enter_context(patch.object(save_paths, "_current_session_dir", None))
        self.stack.enter_context(patch.object(save_paths, "_session_stamp_minute", return_value="20260101_1200"))

    def test_output_directories_are_created_idempotently(self):
        for ensure in (save_paths.ensure_saves_dir, save_paths.ensure_houses_dir, save_paths.ensure_devices_dir):
            with self.subTest(function=ensure.__name__):
                first = ensure()
                self.assertTrue(first.is_dir())
                self.assertEqual(ensure(), first)

    def test_new_sessions_are_unique_and_include_all_log_subdirectories(self):
        first = save_paths.create_new_save_session("living room / run")
        second = save_paths.create_new_save_session("living room / run")
        self.assertEqual(first.name, "20260101_1200_living_room_run")
        self.assertEqual(second.name, "20260101_1200_living_room_run_01")
        self.assertEqual(save_paths.get_current_save_session(), second)
        self.assertEqual({item.name for item in first.iterdir()}, set(save_paths.SESSION_SUBDIRS.values()))

    def test_get_or_create_reuses_the_active_session(self):
        first = save_paths.get_or_create_current_save_session("first")
        self.assertEqual(save_paths.get_or_create_current_save_session("ignored"), first)
        self.assertEqual(len(list((self.root / "saves").iterdir())), 1)

    def test_deleted_active_session_is_replaced(self):
        first = save_paths.create_new_save_session()
        first.rename(first.with_name("archived"))
        self.assertIsNone(save_paths.get_current_save_session())
        replacement = save_paths.get_or_create_current_save_session()
        self.assertTrue(replacement.is_dir())
        self.assertEqual(save_paths.get_current_save_session(), replacement)

    def test_explicit_session_subdirectory_does_not_create_an_active_session(self):
        explicit = self.root / "external-session"
        self.assertEqual(save_paths.get_session_subdir("sensors", explicit), explicit / "sensors")
        self.assertIsNone(save_paths.get_current_save_session())
        self.assertTrue((explicit / "sensors").is_dir())

    def test_unknown_session_kind_is_rejected_without_creating_files(self):
        with self.assertRaisesRegex(ValueError, "Unknown session subdir"):
            save_paths.get_session_subdir("../../escape")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_concurrent_session_creation_does_not_overwrite_runs(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            sessions = list(executor.map(lambda _: save_paths.create_new_save_session("test"), range(8)))
        self.assertEqual(len(set(sessions)), 8)
        self.assertTrue(all((session / "interactions").is_dir() for session in sessions))

    def test_concurrent_first_loggers_share_one_session(self):
        ready = threading.Barrier(8)

        def get_session(_index):
            ready.wait(timeout=10)
            return save_paths.get_or_create_current_save_session()

        with ThreadPoolExecutor(max_workers=8) as executor:
            sessions = list(executor.map(get_session, range(8)))
        self.assertEqual(len(set(sessions)), 1)
        self.assertEqual(len(list((self.root / "saves").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
