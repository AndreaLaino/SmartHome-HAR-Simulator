"""A new doorway cuts actual wall geometry and is one reversible map edit."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.context import AppContext
from app.io.scenario import _serialize_scenario
from app.ui import device_inspector as editor
from app.ui.main_ui import _menu_add_door
from models import Point, Wall
from room_recognition import recognize_rooms
from utils import is_path_blocked_by_walls
import read


class DoorInsertionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.ctx = AppContext(window=None, load_active=True)
        self.ctx.canvas = SimpleNamespace(_zoom_factor=1, create_line=Mock(), delete=Mock())
        self.stack.enter_context(patch.object(editor, "_redraw_editable_objects"))
        self.stack.enter_context(patch.object(editor, "_draw_selections"))
        self.geometry = self.stack.enter_context(patch.object(read, "read_walls_coordinates", []))
        self.stack.enter_context(patch("door.read_doors", self.ctx.read_doors))
        self.original = Wall(0, 100, 200, 100)
        self.ctx.read_walls.append(self.original)
        self.geometry.append(self.original)
        self.ctx.r_points[:] = [Point("left", 0, 100), Point("right", 200, 100)]

    def insert(self, start=(75, 100), end=(125, 100)):
        points = editor.current_points(self.ctx)
        endpoints = []
        for x, y in (start, end):
            point = next((p for p in points if (p.x, p.y) == (x, y)), None)
            if point is None:
                point = Point(f"cut_{len(points)}", x, y)
                points.append(point)
            endpoints.append(point)
        with patch("app.ui.main_ui.begin_point_segment_mode") as mode:
            _menu_add_door(self.ctx)
        return mode.call_args.args[2](*endpoints)

    def test_open_door_replaces_only_its_wall_span_and_one_undo_restores_the_wall(self):
        door = self.insert()
        self.assertEqual(self.ctx.read_walls, [Wall(0, 100, 75, 100), Wall(125, 100, 200, 100)])
        self.assertEqual(self.geometry, self.ctx.read_walls)
        self.assertTrue(is_path_blocked_by_walls(100, 50, 100, 150, self.ctx.read_walls, [door]))
        door.state = "open"
        self.assertFalse(is_path_blocked_by_walls(100, 50, 100, 150, self.ctx.read_walls, [door]))
        self.assertTrue(is_path_blocked_by_walls(25, 50, 25, 150, self.ctx.read_walls, [door]))
        for _ in range(3):
            editor.undo_last_movement(self.ctx)
            self.assertEqual(self.ctx.read_walls, [self.original])
            self.assertEqual(self.geometry, [self.original])
            self.assertEqual(self.ctx.read_doors, [])
            self.assertEqual([p.name for p in self.ctx.r_points], ["left", "right"])
            editor.redo_last_movement(self.ctx)
            self.assertEqual(self.ctx.read_doors, [door])
            self.assertEqual(len(self.ctx.read_walls), 2)
            self.assertEqual(self.geometry, self.ctx.read_walls)
            self.assertEqual(len(self.ctx.r_points), 4)
            self.assertEqual(door.state, "open")

    def test_full_wall_replacement_and_door_without_a_wall_are_undoable(self):
        door = self.insert((0, 100), (200, 100))
        self.assertEqual(self.ctx.read_walls, [])
        editor.undo_last_movement(self.ctx)
        self.assertEqual(self.ctx.read_walls, [self.original])
        editor.redo_last_movement(self.ctx)
        self.assertEqual(self.ctx.read_doors, [door])
        self.assertEqual(len(self.ctx.r_points), 2)
        separate = self.insert((0, 200), (200, 200))
        self.assertEqual(len(self.ctx.read_doors), 2)
        editor.undo_last_movement(self.ctx)
        self.assertEqual(self.ctx.read_doors, [door])
        editor.redo_last_movement(self.ctx)
        self.assertIn(separate, self.ctx.read_doors)

    def test_crossing_parallel_and_touching_walls_are_not_removed(self):
        vertical, parallel = Wall(100, 0, 100, 200), Wall(0, 110, 200, 110)
        touching = Wall(125, 100, 180, 150)
        self.ctx.read_walls.extend([vertical, parallel, touching])
        self.geometry[:] = self.ctx.read_walls
        self.insert()
        for wall in (vertical, parallel, touching):
            self.assertTrue(any(wall is candidate for candidate in self.ctx.read_walls))

    def test_diagonal_reverse_endpoints_and_partial_overlap(self):
        self.ctx.read_walls[:] = [Wall(0, 0, 200, 200)]
        self.geometry[:] = self.ctx.read_walls
        self.ctx.r_points[:] = [Point("a", 0, 0), Point("b", 200, 200)]
        self.insert((125, 125), (75, 75))
        self.assertEqual(self.ctx.read_walls, [Wall(0, 0, 75, 75), Wall(125, 125, 200, 200)])
        editor.undo_last_movement(self.ctx)
        self.insert((-25, -25), (50, 50))
        self.assertEqual(self.ctx.read_walls, [Wall(50, 50, 200, 200)])
        self.assertNotIn((0, 0), {(p.x, p.y) for p in self.ctx.r_points})
        editor.undo_last_movement(self.ctx)
        self.assertEqual(self.ctx.read_walls, [Wall(0, 0, 200, 200)])
        self.assertIn((0, 0), {(p.x, p.y) for p in self.ctx.r_points})

    def test_door_can_cut_multiple_adjoining_wall_segments(self):
        self.ctx.read_walls[:] = [Wall(0, 100, 100, 100), Wall(100, 100, 200, 100)]
        self.geometry[:] = self.ctx.read_walls
        self.ctx.r_points.insert(1, Point("middle", 100, 100))
        self.insert()
        self.assertEqual(self.ctx.read_walls, [Wall(0, 100, 75, 100), Wall(125, 100, 200, 100)])
        self.assertNotIn("middle", [p.name for p in self.ctx.r_points])
        editor.undo_last_movement(self.ctx)
        self.assertEqual(self.ctx.read_walls, [Wall(0, 100, 100, 100), Wall(100, 100, 200, 100)])
        self.assertEqual([p.name for p in self.ctx.r_points], ["left", "middle", "right"])

    def test_unsaved_house_keeps_legacy_wall_names_consistent(self):
        self.ctx.load_active = False
        with patch("wall.walls_coordinates", [self.original]) as walls, \
                patch("wall.walls", ["left", "right"]) as names, \
                patch("point.points", self.ctx.r_points) as points, patch("door.doors", []):
            self.insert()
            for _ in range(2):
                self.assertEqual(len(walls), 2)
                by_name = {p.name: (p.x, p.y) for p in points}
                for wall, start, end in zip(walls, names[::2], names[1::2]):
                    self.assertEqual((by_name[start], by_name[end]), ((wall.x1, wall.y1), (wall.x2, wall.y2)))
                editor.undo_last_movement(self.ctx)
                self.assertEqual((walls, names), ([self.original], ["left", "right"]))
                editor.redo_last_movement(self.ctx)

    def test_cut_wall_geometry_survives_save_reload_and_still_bounds_the_room(self):
        self.ctx.read_walls.extend([Wall(200, 100, 200, 300), Wall(200, 300, 0, 300), Wall(0, 300, 0, 100)])
        self.geometry[:] = self.ctx.read_walls
        self.ctx.r_points.extend([Point("bottom_right", 200, 300), Point("bottom_left", 0, 300)])
        door = self.insert()
        self.assertEqual(len(recognize_rooms(self.ctx.read_walls, [door], [], [], [])), 1)
        content = _serialize_scenario(self.ctx)
        with TemporaryDirectory() as temp:
            path = Path(temp) / "door.csv"
            path.write_text(content, encoding="utf-8")
            for name in ("coordinates", "read_walls", "read_sensors", "read_devices", "read_doors"):
                self.stack.enter_context(patch.object(read, name, []))
            self.stack.enter_context(patch.object(read, "read_room_overrides", {}))
            self.stack.enter_context(patch.object(read, "read_har_config", {}))
            _, walls, _, _, doors = read.read_coordinates_from_file(path)
        self.assertEqual(walls, self.ctx.read_walls)
        doors[0].state = "open"
        self.assertFalse(is_path_blocked_by_walls(100, 50, 100, 150, walls, doors))


if __name__ == "__main__":
    unittest.main()
