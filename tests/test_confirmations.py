import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.confirmations import ask_confirmation
from app.ui.device_inspector import MapSelection, delete_map_selection
from app.controllers.simulation import exit_app
from models import Wall


class ConfirmationPreferenceTests(unittest.TestCase):
    def test_closing_application_uses_exit_confirmation_preference(self):
        window = SimpleNamespace(quit=lambda: self.fail("window should stay open"))
        ctx = SimpleNamespace(window=window, preferences={}, smart_logger=None)

        with patch(
            "app.confirmations.ask_confirmation",
            return_value=False,
        ) as confirm:
            exit_app(ctx)

        self.assertEqual(confirm.call_args.args[1], "confirm_exit_app")

    def test_disabled_confirmation_allows_action_without_dialog(self):
        ctx = SimpleNamespace(preferences={"confirm_exit_app": False})

        with patch("app.confirmations._show_confirmation_dialog") as dialog:
            confirmed = ask_confirmation(
                ctx,
                "confirm_exit_app",
                "Exit",
                "Close?",
            )

        self.assertTrue(confirmed)
        dialog.assert_not_called()

    def test_dont_ask_again_is_saved_only_after_yes(self):
        preference_var = SimpleNamespace(values=[], set=lambda value: None)
        ctx = SimpleNamespace(
            preferences={"confirm_delete_wall": True},
            _confirmation_vars={"confirm_delete_wall": preference_var},
        )

        with (
            patch(
                "app.confirmations._show_confirmation_dialog",
                return_value=(True, True),
            ),
            patch("app.confirmations.save_preferences") as save,
        ):
            confirmed = ask_confirmation(
                ctx,
                "confirm_delete_wall",
                "Delete",
                "Delete wall?",
            )

        self.assertTrue(confirmed)
        self.assertFalse(ctx.preferences["confirm_delete_wall"])
        save.assert_called_once_with(ctx.preferences)

    def test_dont_ask_again_is_not_saved_after_no(self):
        ctx = SimpleNamespace(preferences={"confirm_delete_wall": True})

        with (
            patch(
                "app.confirmations._show_confirmation_dialog",
                return_value=(False, True),
            ),
            patch("app.confirmations.save_preferences") as save,
        ):
            confirmed = ask_confirmation(
                ctx,
                "confirm_delete_wall",
                "Delete",
                "Delete wall?",
            )

        self.assertFalse(confirmed)
        self.assertTrue(ctx.preferences["confirm_delete_wall"])
        save.assert_not_called()

    def test_wall_deletion_uses_wall_confirmation_preference(self):
        wall = Wall(0, 0, 100, 0)
        ctx = SimpleNamespace(
            load_active=True,
            read_walls=[wall],
            preferences={"confirm_delete_wall": True},
        )

        with patch(
            "app.ui.device_inspector.ask_confirmation",
            return_value=False,
        ) as confirm:
            deleted = delete_map_selection(ctx, MapSelection("wall", wall))

        self.assertFalse(deleted)
        self.assertEqual(ctx.read_walls, [wall])
        self.assertEqual(confirm.call_args.args[1], "confirm_delete_wall")


if __name__ == "__main__":
    unittest.main()
