import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.io import safe_dialog
from app.io.save_dialog import SavePathDialog


class SaveDialogTests(unittest.TestCase):
    def test_macos_save_never_opens_native_panel_or_helper(self):
        with patch.object(safe_dialog.sys, "platform", "darwin"), \
                patch("app.io.save_dialog.ask_save_path", return_value={"path": "/tmp/home.csv"}) as chooser, \
                patch.object(safe_dialog, "_run_direct") as native, patch.object(safe_dialog, "_run_helper") as helper:
            self.assertEqual(safe_dialog.ask_save_file(title="Save scenario as", defaultextension=".csv"), "/tmp/home.csv")
            chooser.assert_called_once()
            native.assert_not_called()
            helper.assert_not_called()

    def test_cancel_on_macos_does_not_fall_back_to_native_panel(self):
        with patch.object(safe_dialog.sys, "platform", "darwin"), \
                patch("app.io.save_dialog.ask_save_path", return_value={"cancelled": True}), \
                patch.object(safe_dialog, "_run_direct") as native, patch.object(safe_dialog, "_run_helper") as helper:
            self.assertIsNone(safe_dialog.ask_save_file())
            native.assert_not_called()
            helper.assert_not_called()

    def test_other_platforms_keep_the_existing_dialog_path(self):
        with patch.object(safe_dialog.sys, "platform", "linux"), \
                patch.object(safe_dialog, "_run_direct", return_value={"path": "/tmp/home.csv"}) as native:
            self.assertEqual(safe_dialog.ask_save_file(), "/tmp/home.csv")
            self.assertEqual(native.call_args.args, ("save",))

    def test_tk_save_adds_extension_and_rejects_invalid_folder_without_closing(self):
        with tempfile.TemporaryDirectory() as folder:
            dialog = SavePathDialog.__new__(SavePathDialog)
            dialog.default_extension = ".csv"
            dialog.status = Mock()
            dialog.quit = Mock()
            dialog.get_selection = Mock(return_value=str(Path(folder) / "my home"))
            dialog.ok_command()
            dialog.quit.assert_called_once_with(str(Path(folder) / "my home.csv"))
            dialog.quit.reset_mock()
            dialog.get_selection.return_value = os.fspath(Path(folder) / "missing folder" / "home.csv")
            dialog.ok_command()
            dialog.quit.assert_not_called()
            dialog.status.configure.assert_called()


if __name__ == "__main__":
    unittest.main()
