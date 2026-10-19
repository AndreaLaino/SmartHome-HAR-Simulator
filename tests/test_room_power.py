import unittest

from models import Device, Room, Sensor
from room_recognition import update_room_power


class RoomPowerTests(unittest.TestCase):
    def setUp(self):
        self.left = Room("a", [(0, 0), (100, 0), (100, 100), (0, 100)])
        self.right = Room("b", [(100, 0), (200, 0), (200, 100), (100, 100)])
        self.rooms = [self.left, self.right]

    def meter(self, name, watts, *, x=50, device=None):
        return Sensor(name, x, 50, "Smart Meter", 0, 5000, 1, 0,
                      consumption=watts, associated_device=device)

    def test_totals_use_current_meter_watts_and_associated_device_room(self):
        devices = [
            Device("lamp", 50, 50, "Lighting", 10, 1, 10, 10),
            Device("pc", 150, 50, "Computer", 250, 1, 100, 250),
            Device("unmetered", 50, 50, "Oven", 2000, 1, 500, 2000, 900),
        ]
        meters = [
            self.meter("light meter", 10, x=150, device="lamp"),
            self.meter("pc meter", 83.25, x=50, device="pc"),
            self.meter("extra meter", 4.75),
        ]
        update_room_power(self.rooms, meters, devices)
        self.assertEqual(self.left.total_power_w, 14.75)
        self.assertEqual(self.right.total_power_w, 83.25)
        self.assertEqual(self.left.smartmeter_names, ["light meter", "extra meter"])

    def test_shared_boundary_and_repeated_meter_count_only_once(self):
        meter = self.meter("boundary", 12.5, x=100)
        update_room_power(list(reversed(self.rooms)), [meter, meter], [])
        self.assertEqual(self.left.total_power_w, 12.5)
        self.assertEqual(self.right.total_power_w, 0)
        self.assertEqual(self.left.smartmeter_names, ["boundary"])

    def test_moving_device_reassigns_power_and_removing_meter_clears_total(self):
        light = Device("lamp", 50, 50, "Lighting", 10, 1, 10, 10)
        meter = self.meter("meter", 10, device="lamp")
        update_room_power(self.rooms, [meter], [light])
        light.x = 150
        meter.consumption = 7.5
        update_room_power(self.rooms, [meter], [light])
        self.assertEqual(self.left.total_power_w, 0)
        self.assertEqual(self.left.smartmeter_names, [])
        self.assertEqual(self.right.total_power_w, 7.5)
        update_room_power(self.rooms, [], [light])
        self.assertEqual(self.right.total_power_w, 0)
        self.assertEqual(self.right.smartmeter_names, [])

    def test_invalid_readings_other_sensors_and_outside_loads_are_excluded(self):
        devices = [Device("outside", 300, 50, "Lighting", 10, 1, 10, 10)]
        meters = [self.meter(str(value), value) for value in (None, "bad", -10, float("nan"), float("inf"))]
        meters += [self.meter("outside meter", 20, x=300),
                   self.meter("outside device meter", 10, device="outside"),
                   Sensor("temperature", 50, 50, "Temperature", 0, 40, 1, 22, consumption=22)]
        update_room_power(self.rooms, meters, devices)
        self.assertEqual(self.left.total_power_w, 0)
        self.assertEqual(self.right.total_power_w, 0)

    def test_missing_device_uses_meter_position_and_zero_reading_is_metered(self):
        update_room_power(self.rooms, [self.meter("orphan", 0, device="missing")], [])
        self.assertEqual(self.left.total_power_w, 0)
        self.assertEqual(self.left.smartmeter_names, ["orphan"])


if __name__ == "__main__":
    unittest.main()
