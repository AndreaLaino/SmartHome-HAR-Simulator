"""A Tk-only save chooser that avoids the native macOS save panel."""

import os
import tkinter as tk
from tkinter.filedialog import FileDialog


class SavePathDialog(FileDialog):
    def __init__(self, parent, title, default_extension=""):
        super().__init__(parent, title)
        self.default_extension = default_extension
        self.result = None
        self.ok_button.configure(text="Save")
        self.top.geometry("640x420")
        self.top.minsize(480, 320)
        tk.Label(self.top, text="Folder / filter", anchor="w").pack(side=tk.TOP, fill=tk.X, before=self.filter)
        tk.Label(self.top, text="File name", anchor="w").pack(side=tk.BOTTOM, fill=tk.X, after=self.selection)
        self.status = tk.Label(self.botframe, text="", anchor="w", fg="#b42318")
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.top.bind("<Escape>", self.cancel_command)
        if parent.winfo_viewable():
            self.top.transient(parent)

    def choose(self, initialdir=None, initialfile=None, pattern="*"):
        directory = os.path.abspath(os.path.expanduser(initialdir or os.curdir))
        if not os.path.isdir(directory):
            directory = os.getcwd()
        self.directory = directory
        self.set_filter(directory, pattern)
        self.set_selection(initialfile or "")
        self.filter_command()
        self.selection.focus_set()
        self.selection.selection_range(0, tk.END)
        self.top.wait_visibility()
        self.top.grab_set()
        # FileDialog.go() starts/quits the application's mainloop. A modal wait
        # confines closure to this dialog, leaving the simulator running.
        self.top.wait_window()
        return self.result

    def ok_command(self):
        filename = os.path.abspath(self.get_selection())
        if os.path.isdir(filename):
            self.status.configure(text="Choose a file name, not a folder.")
            return
        if self.default_extension and not os.path.splitext(filename)[1]:
            filename += self.default_extension
        if not os.path.isdir(os.path.dirname(filename)):
            self.status.configure(text="The selected folder does not exist.")
            return
        # The application asks for confirmation before replacing an existing file.
        self.quit(filename)

    def quit(self, filename=None):
        self.result = filename
        self.top.destroy()


def ask_save_path(title="Save file", initialdir=None, initialfile=None, filetypes=None, defaultextension=None):
    parent = tk._default_root
    own_root = None
    if parent is None:
        own_root = parent = tk.Tk()
        parent.withdraw()
    try:
        pattern = "*"
        if filetypes:
            candidate = filetypes[0][1]
            pattern = candidate if isinstance(candidate, str) else candidate[0]
        dialog = SavePathDialog(parent, title, defaultextension or "")
        filename = dialog.choose(initialdir, initialfile, pattern)
        return {"path": filename} if filename else {"cancelled": True}
    finally:
        if own_root is not None:
            own_root.destroy()
