"""Connected door geometry and endpoint cleanup through edit history."""

from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.context import AppContext
from app.io.scenario import _serialize_scenario
from app.ui import device_inspector as editor
from models import Door, Point, Wall
import read


class ConnectedSegmentTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("_redraw_editable_objects", "_draw_selections", "_draw_drag_preview"):
            self.stack.enter_context(patch.object(editor, name))
        self.geometry = self.stack.enter_context(patch.object(read, "read_walls_coordinates", []))
        self.ctx = AppContext(window=None, load_active=True)
        self.ctx.canvas = SimpleNamespace(
            _zoom_factor=1.0, _snap_to_grid=False,
            canvasx=lambda value: value, canvasy=lambda value: value,
            focus_set=lambda: None, delete=lambda _tag: None,
        )
        self.a, self.b = Point("a", 0, 0), Point("b", 100, 0)
        self.c, self.d = Point("c", 100, 100), Point("d", 0, 100)
        self.unrelated = Point("standalone", 300, 300)
        self.ctx.r_points[:] = [self.a, self.b, self.c, self.d, self.unrelated]
        self.door = Door(0, 0, 100, 0, "open")
        self.left, self.right = Wall(0, 0, 0, 100), Wall(100, 0, 100, 100)
        self.ctx.read_doors.append(self.door)
        self.ctx.read_walls[:] = [self.left, self.right]
        self.geometry[:] = self.ctx.read_walls

    def drag(self, selection, start, end):
        with patch.object(editor, "_selection_at_event", return_value=selection):
            editor.begin_selection_drag(self.ctx, SimpleNamespace(x=start[0], y=start[1]))
            editor.drag_selected_object(self.ctx, SimpleNamespace(x=end[0], y=end[1]))
            editor.finish_selection_drag(self.ctx)

    def test_door_translation_moves_points_and_connected_walls_with_undo_redo(self):
        self.drag(editor.MapSelection("door", self.door), (50, 0), (70, 30))
        self.assertEqual((self.a.x, self.a.y, self.b.x, self.b.y), (20, 30, 120, 30))
        self.assertEqual((self.left.x1, self.left.y1, self.right.x1, self.right.y1), (20, 30, 120, 30))
        self.assertEqual((self.c.x, self.c.y, self.d.x, self.d.y), (100, 100, 0, 100))
        self.assertEqual((self.door.x1, self.door.y1, self.door.x2, self.door.y2), (20, 30, 120, 30))
        self.assertEqual(self.door.state, "open")
        editor.undo_last_movement(self.ctx)
        self.assertEqual((self.a.x, self.a.y, self.b.x, self.b.y), (0, 0, 100, 0))
        self.assertEqual((self.left.x1, self.left.y1, self.right.x1, self.right.y1), (0, 0, 100, 0))
        editor.redo_last_movement(self.ctx)
        self.assertEqual((self.a.x, self.a.y, self.b.x, self.b.y), (20, 30, 120, 30))

    def test_resizing_either_door_endpoint_keeps_its_point_and_wall_attached(self):
        for endpoint in (1, 2):
            with self.subTest(endpoint=endpoint):
                start = (0, 0) if endpoint == 1 else (100, 0)
                self.drag(editor.MapSelection("door", self.door), start, (start[0] + 20, 30))
                moved_point, moved_wall = (self.a, self.left) if endpoint == 1 else (self.b, self.right)
                self.assertEqual((moved_point.x, moved_point.y), (start[0] + 20, 30))
                self.assertEqual((moved_wall.x1, moved_wall.y1), (moved_point.x, moved_point.y))
                untouched = self.b if endpoint == 1 else self.a
                self.assertEqual((untouched.x, untouched.y), (100, 0) if endpoint == 1 else (0, 0))
                editor.undo_last_movement(self.ctx)

    def test_group_drag_translates_shared_vertices_only_once_even_onto_an_old_vertex(self):
        selections = [editor.MapSelection("door", self.door), editor.MapSelection("point", self.b),
                      editor.MapSelection("wall", self.right)]
        editor._set_selected_objects(self.ctx, selections)
        self.drag(selections[0], (50, 0), (150, 0))
        self.assertEqual((self.a.x, self.b.x, self.c.x), (100, 200, 200))
        self.assertEqual((self.door.x1, self.door.x2), (100, 200))
        self.assertEqual((self.left.x1, self.right.x1, self.right.x2), (100, 200, 200))
        self.assertEqual((self.unrelated.x, self.unrelated.y), (300, 300))

    def test_door_resize_uses_logical_coordinates_and_grid_snapping(self):
        self.ctx.canvas._zoom_factor = 2.0
        self.ctx.canvas._snap_to_grid = True
        self.ctx.canvas._grid_spacing = 25
        self.drag(editor.MapSelection("door", self.door), (0, 0), (60, 60))
        self.assertEqual((self.a.x, self.a.y), (25, 25))
        self.assertEqual((self.door.x1, self.door.y1, self.left.x1, self.left.y1), (25, 25, 25, 25))

    def test_deletion_removes_only_endpoints_with_no_remaining_wall_or_door(self):
        self.ctx.read_walls.remove(self.left)
        original_points = self.ctx.r_points[:]
        editor.delete_map_selection(self.ctx, editor.MapSelection("door", self.door), confirm=False)
        self.assertNotIn(self.a, self.ctx.r_points)
        self.assertIn(self.b, self.ctx.r_points)  # Still attached to the right wall.
        self.assertIn(self.d, self.ctx.r_points)  # Already standalone before this deletion.
        self.assertIn(self.unrelated, self.ctx.r_points)
        editor.undo_last_movement(self.ctx)
        self.assertEqual(self.ctx.r_points, original_points)
        self.assertIs(self.ctx.r_points[0], self.a)
        self.assertIs(self.ctx.read_doors[0], self.door)
        editor.redo_last_movement(self.ctx)
        self.assertNotIn(self.a, self.ctx.r_points)
        self.assertIn(self.b, self.ctx.r_points)

    def test_wall_cleanup_preserves_endpoint_shared_with_a_door_and_restores_original_names(self):
        self.ctx.read_doors[:] = [Door(100, 0, 100, 100)]
        wall = Wall(0, 0, 100, 0)
        self.ctx.read_walls[:] = [wall]
        self.geometry[:] = [wall]
        original_points = self.ctx.r_points[:]
        editor.delete_map_selection(self.ctx, editor.MapSelection("wall", wall), confirm=False)
        self.assertNotIn(self.a, self.ctx.r_points)
        self.assertIn(self.b, self.ctx.r_points)
        self.assertEqual(self.geometry, [])
        for _ in range(2):
            editor.undo_last_movement(self.ctx)
            self.assertEqual(self.ctx.r_points, original_points)
            self.assertEqual(len(self.ctx.r_points), 5)
            self.assertIs(self.ctx.r_points[0], self.a)
            self.assertEqual(self.geometry, [wall])
            editor.redo_last_movement(self.ctx)
            self.assertNotIn(self.a, self.ctx.r_points)

    def test_removing_a_point_cleans_endpoints_of_its_removed_segments_and_restores_everything(self):
        original_points = self.ctx.r_points[:]
        editor.delete_map_selection(self.ctx, editor.MapSelection("point", self.a), confirm=False)
        self.assertEqual(self.ctx.read_doors, [])
        self.assertEqual(self.ctx.read_walls, [self.right])
        self.assertNotIn(self.a, self.ctx.r_points)
        self.assertNotIn(self.d, self.ctx.r_points)
        self.assertIn(self.b, self.ctx.r_points)
        editor.undo_last_movement(self.ctx)
        self.assertEqual(self.ctx.r_points, original_points)
        self.assertEqual(self.ctx.read_walls, [self.left, self.right])
        self.assertEqual(self.ctx.read_doors, [self.door])

    def test_duplicate_door_has_reference_points_and_undo_does_not_leave_orphans(self):
        editor._set_selected_objects(self.ctx, [editor.MapSelection("door", self.door)])
        original_points = self.ctx.r_points[:]
        editor.duplicate_selected_object(self.ctx)
        duplicate = self.ctx.read_doors[-1]
        endpoints = {(duplicate.x1, duplicate.y1), (duplicate.x2, duplicate.y2)}
        self.assertTrue(endpoints.issubset({(point.x, point.y) for point in self.ctx.r_points}))
        editor.undo_last_movement(self.ctx)
        self.assertEqual(self.ctx.r_points, original_points)
        editor.redo_last_movement(self.ctx)
        self.assertEqual(len(self.ctx.r_points), len(original_points) + 2)

    def test_cut_segment_cleanup_can_be_undone_without_renaming_points(self):
        self.ctx.read_walls.clear()
        editor._set_selected_objects(self.ctx, [editor.MapSelection("door", self.door)])
        original_points = self.ctx.r_points[:]
        editor.cut_selected_object(self.ctx)
        self.assertNotIn(self.a, self.ctx.r_points)
        self.assertNotIn(self.b, self.ctx.r_points)
        editor.undo_last_movement(self.ctx)
        self.assertEqual(self.ctx.r_points, original_points)
        self.assertIs(self.ctx.r_points[0], self.a)

    def test_new_house_wall_cleanup_keeps_legacy_point_name_pairs_valid_after_undo(self):
        self.ctx.load_active = False
        with patch("point.points", [self.a, self.b, self.unrelated]) as points, \
                patch("wall.walls_coordinates", [self.left]) as walls, \
                patch("wall.walls", ["a", "b"]) as names, patch("door.doors", []):
            self.left.x2, self.left.y2 = self.b.x, self.b.y
            editor.delete_map_selection(self.ctx, editor.MapSelection("wall", self.left), confirm=False)
            self.assertEqual(points, [self.unrelated])
            self.assertEqual((walls, names), ([], []))
            editor.undo_last_movement(self.ctx)
            self.assertEqual(points, [self.a, self.b, self.unrelated])
            self.assertIs(points[0], self.a)
            self.assertEqual((walls, names), ([self.left], ["a", "b"]))

    def test_saved_geometry_uses_moved_point_names_and_survives_reload(self):
        self.drag(editor.MapSelection("door", self.door), (50, 0), (70, 30))
        content = _serialize_scenario(self.ctx)
        with TemporaryDirectory() as temp:
            path = Path(temp) / "house.csv"
            path.write_text(content, encoding="utf-8")
            for name in ("coordinates", "read_walls", "read_sensors", "read_devices", "read_doors"):
                self.stack.enter_context(patch.object(read, name, []))
            self.stack.enter_context(patch.object(read, "read_room_overrides", {}))
            self.stack.enter_context(patch.object(read, "read_har_config", {}))
            points, walls, _, _, doors = read.read_coordinates_from_file(path)
        self.assertEqual([(p.name, p.x, p.y) for p in points], [(p.name, p.x, p.y) for p in self.ctx.r_points])
        self.assertEqual(walls, self.ctx.read_walls)
        self.assertEqual(doors, self.ctx.read_doors)


if __name__ == "__main__":
    unittest.main()
