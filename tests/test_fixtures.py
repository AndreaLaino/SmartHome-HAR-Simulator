"""Passive objects, ordinary linked sensors, and the power/clock regression."""

import csv
import io
import math
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import device
import read
import sensor
import sim
from app.context import AppContext
from app.io.scenario import _serialize_scenario
from app.ui import device_inspector as inspector
from har import runtime as har_runtime, storage
from har.catalogue import default_catalogue
from har.features import primitive_names, primitives
from house_state import HouseState
from models import Device, Fixture, FIXTURE_TYPES, Room, Sensor
from room_recognition import update_room_power, point_in_polygon
from tests.test_simulation_runtime import make_timer, sources
from utils import update_devices_consumption


class FixtureTests(unittest.TestCase):
    def test_passive_objects_have_no_electrical_fields_and_separate_categories(self):
        for kind in FIXTURE_TYPES:
            with self.subTest(kind=kind):
                obj = Fixture("object", 50, 50, kind)
                self.assertNotIsInstance(obj, Device)
                for field in ("power", "min_consumption", "max_consumption", "current_consumption"):
                    self.assertFalse(hasattr(obj, field))
                self.assertNotIn(kind, device.DEVICE_TYPES)
        groups = device.device_types_by_category(default_catalogue(), FIXTURE_TYPES)
        self.assertEqual(groups["Device_Toilet"], ("Toilet",))
        self.assertEqual(groups["Device_Seating"], ("Sofa", "Chair"))

    def test_external_sensor_avoids_other_sensors_and_stays_in_the_objects_room(self):
        obj = Fixture("wc", 50, 50, "Toilet")
        room = Room("bathroom", [(0, 0), (100, 0), (100, 100), (0, 100)])
        occupied = [Sensor("weight_wc", 75, 50, "Weight", 0, 1, 1, 0),
                    Sensor("weight_wc_2", 50, 75, "Weight", 0, 1, 1, 0)]
        result = sensor.create_associated_sensor(obj, "Weight", occupied, [room], object_names=["weight_wc_3"])
        self.assertEqual(result.name, "weight_wc_4")
        self.assertEqual(result.associated_device, "wc")
        self.assertFalse(result.integrated)
        self.assertIsNone(result.consumption)
        self.assertTrue(point_in_polygon(result.x, result.y, room.polygon))
        self.assertTrue(all(math.hypot(result.x - s.x, result.y - s.y) >= 20 for s in occupied))
        self.assertNotEqual((result.x, result.y), (obj.x, obj.y))

    def test_tiny_room_fails_before_creating_any_partial_objects(self):
        obj = Fixture("wc", 50, 50, "Toilet")
        room = Room("tiny", [(49, 49), (51, 49), (51, 51), (49, 51)])
        canvas = SimpleNamespace(master=Mock())
        dialog = SimpleNamespace(result=(obj.name, obj.type, None, None, None, "manual", "Weight"))
        with patch.object(device, "devices", []), patch.object(sensor, "sensors", []), \
                patch.object(device, "DeviceDialog", return_value=dialog), \
                patch.object(device, "event_to_logical", return_value=(50, 50)), \
                patch.object(device, "snap_logical_position", return_value=(50, 50)), \
                patch.object(device.messagebox, "showwarning"), patch.object(device, "draw_device"):
            self.assertFalse(device.add_device(canvas, object(), False, rooms=[room]))
            self.assertEqual(device.devices, [])
            self.assertEqual(sensor.sensors, [])

    def test_electrical_sensor_is_external_and_invalid_meter_links_are_rejected(self):
        oven = Device("oven", 50, 50, "Oven", 2000, 0, 1500, 2000)
        meter = sensor.create_associated_sensor(oven, "Smart Meter", [])
        self.assertEqual((meter.type, meter.associated_device, meter.integrated), ("Smart Meter", "oven", False))
        with self.assertRaises(ValueError):
            sensor.create_associated_sensor(Fixture("wc", 50, 50, "Toilet"), "Smart Meter", [])
        with self.assertRaises(ValueError):
            sensor.create_associated_sensor(Device("light", 50, 50, "Smart Light", 10, 0, 10, 10), "Smart Meter", [])

    def test_creation_writes_both_objects_to_the_active_house_with_one_history_action(self):
        for loaded in (False, True):
            with self.subTest(loaded=loaded), patch.object(device, "devices", []), \
                    patch.object(device, "device_file", []), patch.object(sensor, "sensors", []), \
                    patch.object(sensor, "sensors_file", []), patch.object(device, "draw_device"), \
                    patch("utils.draw_sensor"), patch.object(device, "event_to_logical", return_value=(50, 50)), \
                    patch.object(device, "snap_logical_position", return_value=(50, 50)), \
                    patch.object(device, "DeviceDialog", return_value=SimpleNamespace(
                        result=("wc", "Toilet", None, None, None, "manual", "Weight"))):
                objects = device.device_file if loaded else device.devices
                sensors = sensor.sensors_file if loaded else sensor.sensors
                ctx = SimpleNamespace(load_active=True, read_devices=objects, read_sensors=sensors,
                                      house_state=HouseState(), canvas=Mock())
                self.assertTrue(device.add_device(SimpleNamespace(master=Mock()), object(), loaded,
                    on_created=lambda obj, linked: inspector.record_device_addition(ctx, obj, linked)))
                self.assertIsInstance(objects[0], Fixture)
                self.assertEqual(sensors[0].associated_device, objects[0].name)
                action = ctx._movement_undo_stack[0]
                self.assertEqual(len(action["objects"]), 2)
                inverse = inspector._apply_history_action(ctx, action)
                self.assertEqual((objects, sensors), ([], []))
                inspector._apply_history_action(ctx, inverse)
                self.assertEqual(sensors[0].associated_device, objects[0].name)
                old_position = (sensors[0].x, sensors[0].y)
                objects[0].x += 25
                self.assertEqual((sensors[0].x, sensors[0].y), old_position)

    def test_weight_observes_linked_object_after_the_sensor_is_moved(self):
        obj = Fixture("wc", 50, 50, "Toilet")
        load = sensor.create_associated_sensor(obj, "Weight", [])
        load.x, load.y = 1000, 1000
        state = HouseState()
        sim._handle_weight_interaction(state, [load], state.sensor_states(), "2026-11-02 00:00", (50, 50), [obj])
        self.assertEqual((load.state, obj.state), (1, 1))
        sim._handle_weight_interaction(state, [load], state.sensor_states(), "2026-11-02 00:01", (1000, 1000), [obj])
        self.assertEqual((load.state, obj.state), (0, 0))
        self.assertEqual(state.sensor_states()[load.name]["state"], [1, 0])
        self.assertEqual(state.active_cycles(), {})

    def test_all_passive_types_ignore_power_cycles_including_legacy_device_instances(self):
        timer = make_timer()
        for kind in FIXTURE_TYPES:
            for obj in (Fixture("f", 0, 0, kind, 1), Device("f", 0, 0, kind, 0, 1, 0, 0)):
                with self.subTest(kind=kind, model=type(obj).__name__):
                    state = HouseState()
                    state.active_cycles()[obj.name] = {"cycle_type": kind, "start_time": timer.simulated_start_time}
                    update_devices_consumption(None, [obj], 1, timer, state.active_cycles())
                    sim.toggle_device_state(None, None, state, timer, sources(devices=[obj]), x=0, y=0, render=False)
                    self.assertEqual(state.active_cycles(), {})

    def test_clock_completes_fifteen_minutes_and_har_records_occupancy_without_watts(self):
        timer = make_timer(start=datetime(2026, 2, 28))
        obj = Fixture("wc", 50, 50, "Toilet")
        load = sensor.create_associated_sensor(obj, "Weight", [])
        state = HouseState()
        config = storage.new_house_config()
        runtime = sources(devices=[obj], sensors=[load])
        runtime["har_config"] = config
        ctx = SimpleNamespace(house_state=state, har_config=config)
        with TemporaryDirectory() as temp, patch.object(storage, "DATA_DIR", Path(temp)):
            session = har_runtime.start_recording(ctx)
            har_runtime.set_recording_labels(ctx, ["using the bathroom"])
            sim._handle_weight_interaction(state, [load], state.sensor_states(), "2026-02-28 00:00", (50, 50), [obj])

            def step(delta):
                sim.update_sensors(None, timer, None, state, runtime, schedule_next=False, force=True,
                                   delta_override=delta, fast=True)
                har_runtime.process_frame(state.sensor_states(), runtime, state.activity_state(),
                                          timer.elapsed_time.total_seconds() * 60, house_state=state)

            timer.on_advance_step = step
            timer.advance_time()
            self.assertEqual(timer.get_simulated_time(), "00:15")
            self.assertEqual(len(session["frames"]), 15)
            self.assertEqual(session["frames"][-1]["time_seconds"], 900)
            self.assertTrue(all("power" not in frame["devices"][0] for frame in session["frames"]))
            self.assertTrue(all(frame["sensor_states"][load.name]["state"] == [1] for frame in session["frames"]))
            saved = storage.read_json(har_runtime.finish_recording(state))
            self.assertEqual(saved["frames"], session["frames"])

    def test_new_csv_has_a_watt_free_fixtures_section_and_loads_legacy_objects(self):
        ctx = AppContext(window=None, load_active=True)
        fixture = Fixture("wc, A", 50, 50, "Toilet", 1, ["Device_Toilet"])
        ctx.read_devices = [Device("oven", 10, 10, "Oven", 2000, 0, 1500, 2000), fixture]
        ctx.read_sensors = [sensor.create_associated_sensor(fixture, "Weight", [])]
        text = _serialize_scenario(ctx)
        rows = list(csv.reader(io.StringIO(text)))
        fixture_row = rows[rows.index(["Fixtures"]) + 1]
        self.assertEqual(fixture_row, ["wc, A", "50", "50", "Toilet"])
        self.assertEqual(fixture.state, 1)
        legacy = "Devices\nold_wc,10,20,Toilet,0,1,0,0,0,1\n"
        list_names = ("coordinates", "read_walls", "read_sensors", "read_devices", "read_doors")
        with TemporaryDirectory() as temp:
            path = Path(temp) / "house.csv"
            with patch.multiple(read, **{name: [] for name in list_names}), \
                    patch.object(read, "read_room_overrides", {}), patch.object(read, "read_har_config", {}):
                path.write_text(text, encoding="utf-8")
                _, _, sensors, objects, _ = read.read_coordinates_from_file(path)
                restored = next(item for item in objects if isinstance(item, Fixture))
                self.assertEqual((restored.name, restored.state, restored.categories), (fixture.name, 0, fixture.categories))
                self.assertEqual(sensors[0].associated_device, restored.name)
                self.assertEqual(sensors[0].state, 0)
                path.write_text(legacy, encoding="utf-8")
                _, _, _, objects, _ = read.read_coordinates_from_file(path)
                self.assertIsInstance(objects[0], Fixture)
                self.assertFalse(hasattr(objects[0], "power"))

    def test_har_and_room_power_do_not_treat_an_old_meter_link_as_fixture_watts(self):
        obj = Fixture("wc", 50, 50, "Toilet")
        old_meter = Sensor("old", 25, 25, "Smart Meter", 0, 5000, 1, 1, consumption=1200, associated_device="wc")
        room = Room("bathroom", [(0, 0), (100, 0), (100, 100), (0, 100)])
        self.assertEqual(sensor.compute_smartmeter_consumption(old_meter, [obj], 1, None), 0)
        update_room_power([room], [old_meter], [obj])
        self.assertEqual(room.total_power_w, 0)
        self.assertEqual(room.smartmeter_names, [])
        catalogue = default_catalogue()
        frame = har_runtime.capture_frame({}, {"devices": [obj], "sensors": [old_meter]}, 0, catalogue)
        channels = dict(zip(primitive_names(catalogue), primitives(frame, catalogue)))
        self.assertEqual(channels["device/Device_Toilet/available"], 1)
        self.assertEqual(channels["device/Device_Toilet/power_observed"], 0)
        self.assertEqual(channels["device/Device_Toilet/power_w"], 0)

    def test_fixture_details_have_no_watts_or_on_off_switch(self):
        obj = Fixture("wc", 50, 50, "Toilet")
        ctx = SimpleNamespace(load_active=True, read_devices=[obj], read_sensors=[])
        with patch.object(inspector.messagebox, "showinfo") as show:
            inspector.show_device_details(ctx, obj)
        text = show.call_args.args[1]
        self.assertNotIn(" W", text)
        self.assertNotIn("OFF", text)
        self.assertIn("Not monitored", text)


if __name__ == "__main__":
    unittest.main()
