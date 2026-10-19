import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.ui.rooms import draw_rooms, refresh_rooms, render_room_power
from models import Device, Room, Sensor, Wall


class RoomUiTests(unittest.TestCase):
    def test_room_overlay_is_removed_and_not_redrawn_when_hidden(self):
        class Canvas:
            _show_room_types = False

            def __init__(self):
                self.deleted = []

            def delete(self, tag):
                self.deleted.append(tag)

            def create_polygon(self, *_args, **_kwargs):
                raise AssertionError("hidden room overlays must not be drawn")

        canvas = Canvas()
        draw_rooms(SimpleNamespace(canvas=canvas, rooms=[object()]))

        self.assertEqual(canvas.deleted, ["room_overlay"])

    def test_power_labels_update_without_rebuilding_overlays(self):
        room = Room("room-1", [], smartmeter_names=["meter"], total_power_w=12.5)
        canvas = Mock(_show_room_types=True)
        with patch("app.ui.rooms.layout_map_labels"):
            render_room_power(canvas, [room])
        canvas.itemconfig.assert_called_once_with("room_label:room-1", text="Room 1: Unknown\nPower: 12.5 W")
        canvas.delete.assert_not_called()

    def test_refresh_keeps_manual_runtime_connected_to_new_rooms(self):
        ctx = SimpleNamespace(
            load_active=True, r_points=[], read_doors=[], room_overrides={},
            read_walls=[Wall(0, 0, 100, 0), Wall(100, 0, 100, 100),
                        Wall(100, 100, 0, 100), Wall(0, 100, 0, 0)],
            read_devices=[Device("lamp", 50, 50, "Lighting", 10, 1, 10, 10)],
            read_sensors=[Sensor("meter", 50, 50, "Smart Meter", 0, 5000, 1, 0,
                                 consumption=10, associated_device="lamp")],
            _manual_runtime_sources={"rooms": []},
        )
        refresh_rooms(ctx, draw=False)
        self.assertIs(ctx._manual_runtime_sources["rooms"], ctx.rooms)
        self.assertEqual(ctx.rooms[0].total_power_w, 10)


if __name__ == "__main__":
    unittest.main()
