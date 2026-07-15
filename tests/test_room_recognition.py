import unittest

from models import Device, Door, Point, Sensor, Wall
from room_recognition import find_room_polygons, point_in_polygon, recognize_rooms


class RoomGeometryTests(unittest.TestCase):
    def test_two_adjacent_rooms_are_detected(self):
        walls = [
            Wall(0, 0, 20, 0),
            Wall(20, 0, 20, 10),
            Wall(20, 10, 0, 10),
            Wall(0, 10, 0, 0),
            Wall(10, 0, 10, 10),
        ]

        polygons = find_room_polygons(walls, [])

        self.assertEqual(len(polygons), 2)
        self.assertTrue(any(point_in_polygon(5, 5, polygon) for polygon in polygons))
        self.assertTrue(any(point_in_polygon(15, 5, polygon) for polygon in polygons))

    def test_door_closes_boundary_with_one_pixel_tolerance(self):
        walls = [
            Wall(0, 0, 10, 0),
            Wall(10, 0, 10, 4),
            Wall(10, 6, 10, 10),
            Wall(10, 10, 0, 10),
            Wall(0, 10, 0, 0),
        ]
        doors = [Door(11, 4, 11, 6)]

        self.assertEqual(len(find_room_polygons(walls, doors)), 1)


class RoomClassificationTests(unittest.TestCase):
    def setUp(self):
        self.walls = [
            Wall(0, 0, 20, 0),
            Wall(20, 0, 20, 10),
            Wall(20, 10, 0, 10),
            Wall(0, 10, 0, 0),
            Wall(10, 0, 10, 10),
        ]

    def test_devices_and_context_classify_rooms(self):
        sensors = [
            Sensor("pir_office", 5, 5, "PIR", 0, 1, 1, 0),
            Sensor("pir_kitchen", 15, 5, "PIR", 0, 1, 1, 0),
        ]
        devices = [
            Device("pc", 5, 5, "Computer", 200, 0, 100, 200),
            Device("oven", 15, 5, "Oven", 1000, 0, 500, 1000),
        ]
        rooms = recognize_rooms(self.walls, [], sensors, devices, [])

        self.assertEqual({room.room_type for room in rooms}, {"Office", "Kitchen"})

    def test_manual_override_replaces_automatic_label(self):
        devices = [Device("pc", 5, 5, "Computer", 200, 0, 100, 200)]
        rooms = recognize_rooms(self.walls, [], [], devices, [])
        office = next(room for room in rooms if room.inferred_type == "Office")

        overridden = recognize_rooms(
            self.walls,
            [],
            [],
            devices,
            [],
            overrides={office.room_id: "Bedroom"},
        )
        changed = next(room for room in overridden if room.room_id == office.room_id)

        self.assertEqual(changed.inferred_type, "Office")
        self.assertEqual(changed.room_type, "Bedroom")
        self.assertEqual(changed.manual_type, "Bedroom")

    def test_multiple_workstations_outweigh_single_bedroom_clue(self):
        devices = [
            Device("pc1", 2, 2, "Computer", 200, 0, 100, 200),
            Device("pc2", 4, 2, "Computer", 200, 0, 100, 200),
            Device("pc3", 6, 2, "Computer", 200, 0, 100, 200),
        ]
        points = [
            Point("bed1", 2, 5),
            Point("desk", 4, 5),
            Point("chair", 6, 5),
            Point("wardrobe", 8, 5),
        ]

        rooms = recognize_rooms(self.walls, [], [], devices, points)
        mixed_room = next(room for room in rooms if room.device_names)

        self.assertEqual(mixed_room.inferred_type, "Office")
        self.assertGreater(mixed_room.confidence, 0.7)

    def test_two_computers_with_desk_outweigh_single_bed(self):
        devices = [
            Device("pc2", 2, 2, "Computer", 200, 0, 100, 200),
            Device("pc3", 4, 2, "Computer", 200, 0, 100, 200),
        ]
        points = [
            Point("bed1", 2, 5),
            Point("desk", 4, 5),
            Point("chair", 6, 5),
            Point("wardrobe", 8, 5),
        ]

        rooms = recognize_rooms(self.walls, [], [], devices, points)
        mixed_room = next(room for room in rooms if room.device_names)

        self.assertEqual(mixed_room.inferred_type, "Office")

    def test_device_type_normalization_is_not_name_format_dependent(self):
        devices = [Device("workstation", 5, 5, "computer", 200, 0, 100, 200)]

        rooms = recognize_rooms(self.walls, [], [], devices, [])

        self.assertIn("Office", {room.inferred_type for room in rooms})


if __name__ == "__main__":
    unittest.main()
