"""Opt-in Tk checks for live, non-destructive activity-log previews."""

import gc
import os
import time
import tkinter as tk
import unittest

import log


@unittest.skipUnless(os.environ.get("SIMULATOR_GUI_TESTS") == "1", "Set SIMULATOR_GUI_TESTS=1 for real Tk checks")
class ActivityLogUITests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda _type, error, _traceback: self.errors.append(error)
        self.addCleanup(self.close)
        self.root.update()

    def close(self):
        root = self.root
        for job in root.tk.splitlist(root.tk.call("after", "info")):
            root.tk.call("after", "cancel", job)
        root.destroy()
        root.report_callback_exception = None
        self.root = None
        del root
        gc.collect()

    def descendants(self, widget):
        for child in widget.winfo_children():
            yield child
            yield from self.descendants(child)

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while not condition():
            self.root.update()
            self.assertFalse(self.errors)
            if time.monotonic() >= deadline:
                self.fail("The activity-log window did not refresh")
            time.sleep(.01)

    def test_standalone_log_shows_open_intervals_refreshes_and_cancels_polling(self):
        state = {}
        log.log_activity_start("relaxing", "18:39", state)
        initial_jobs = set(self.root.tk.splitlist(self.root.tk.call("after", "info")))
        window = log.show_activity_log(state)
        self.root.update()
        text = window._activity_log_text
        self.assertIn("relaxing", text.get("1.0", tk.END))
        self.assertIn("In progress", text.get("1.0", tk.END))
        self.assertEqual(str(text["state"]), tk.DISABLED)
        log.log_activity_start("eating", "18:40", state)
        self.wait_for(lambda: "eating" in text.get("1.0", tk.END))
        log.log_activity_end("eating", "18:50", state)
        self.wait_for(lambda: "18:50" in text.get("1.0", tk.END))
        self.assertEqual(state["active_activities"], {"relaxing": "18:39"})
        log.log_activity_end("relaxing", "21:28", state)
        self.wait_for(lambda: "21:28" in text.get("1.0", tk.END))
        self.assertNotIn("In progress", text.get("1.0", tk.END))
        scheduled = set(self.root.tk.splitlist(self.root.tk.call("after", "info"))) - initial_jobs
        self.assertTrue(scheduled)
        window.destroy()
        self.root.update()
        remaining = set(self.root.tk.splitlist(self.root.tk.call("after", "info")))
        self.assertFalse(scheduled & remaining)
        self.assertFalse(self.errors)

    def test_activity_tab_also_refreshes_an_activity_that_is_still_running(self):
        state = {}
        log.log_activity_start("relaxing", "18:39", state)
        canvas = tk.Canvas(self.root)
        log.show_log(canvas, {}, False, state)
        chooser = next(widget for widget in self.root.winfo_children()
                       if isinstance(widget, tk.Toplevel) and widget.title() == "generate log")
        widgets = list(self.descendants(chooser))
        next(widget for widget in widgets if isinstance(widget, tk.Checkbutton)
             and widget["text"] == "Include Activity Log").invoke()
        next(widget for widget in widgets if isinstance(widget, tk.Button)
             and widget["text"] == "Open Preview").invoke()
        self.root.update()
        preview = next(widget for widget in self.root.winfo_children()
                       if isinstance(widget, tk.Toplevel) and widget.title() == "Sensor Log (multi-tab)")
        text = next(widget for widget in self.descendants(preview) if isinstance(widget, tk.Text))
        self.assertIn("relaxing", text.get("1.0", tk.END))
        self.assertIn("In progress", text.get("1.0", tk.END))
        log.log_activity_end("relaxing", "21:28", state)
        self.wait_for(lambda: "21:28" in text.get("1.0", tk.END))
        self.assertNotIn("In progress", text.get("1.0", tk.END))
        preview.destroy()
        chooser.destroy()
        self.root.update()
        self.assertFalse(self.errors)


if __name__ == "__main__":
    unittest.main()
