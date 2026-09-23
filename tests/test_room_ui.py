import unittest
from types import SimpleNamespace

from app.ui.rooms import draw_rooms


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


if __name__ == "__main__":
    unittest.main()
