import unittest
from unittest.mock import patch

from models import Point
from door import add_door_between_points
from wall import add_wall_between_points


class Canvas:
    _zoom_factor = 1.0

    def __init__(self):
        self.lines = []

    def create_line(self, *coordinates, **options):
        self.lines.append((coordinates, options))
        return len(self.lines)


class SegmentCreationTests(unittest.TestCase):
    def setUp(self):
        self.first = Point("a", 0, 0)
        self.second = Point("b", 100, 0)
        self.canvas = Canvas()

    def test_wall_is_created_from_existing_point_coordinates(self):
        changed = []
        with (
            patch("wall.read_walls", []) as created_walls,
            patch("wall.read_walls_coordinates", []) as pir_walls,
            patch("wall.raise_overlay_labels"),
        ):
            wall = add_wall_between_points(
                self.canvas,
                self.first,
                self.second,
                True,
                on_changed=lambda: changed.append(True),
            )

        self.assertEqual((wall.x1, wall.y1), (self.first.x, self.first.y))
        self.assertEqual((wall.x2, wall.y2), (self.second.x, self.second.y))
        self.assertEqual(created_walls, [wall])
        self.assertEqual(pir_walls, [wall])
        self.assertEqual(changed, [True])

    def test_duplicate_wall_between_same_points_is_rejected(self):
        with (
            patch("wall.read_walls", []) as created_walls,
            patch("wall.read_walls_coordinates", []),
            patch("wall.raise_overlay_labels"),
        ):
            first = add_wall_between_points(
                self.canvas, self.first, self.second, True
            )
            duplicate = add_wall_between_points(
                self.canvas, self.second, self.first, True
            )

        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)
        self.assertEqual(len(created_walls), 1)

    def test_door_is_created_from_existing_point_coordinates(self):
        changed = []
        with patch("door.read_doors", []) as created_doors:
            door = add_door_between_points(
                self.canvas,
                self.first,
                self.second,
                True,
                on_changed=lambda: changed.append(True),
            )

        self.assertEqual((door.x1, door.y1), (self.first.x, self.first.y))
        self.assertEqual((door.x2, door.y2), (self.second.x, self.second.y))
        self.assertEqual(created_doors, [door])
        self.assertEqual(changed, [True])


if __name__ == "__main__":
    unittest.main()
