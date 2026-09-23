from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from app.preferences import save_preferences


def _dialog_parent(ctx):
    parent = getattr(ctx, "window", None)
    if parent is not None:
        return parent
    canvas = getattr(ctx, "canvas", None)
    if canvas is not None:
        try:
            return canvas.winfo_toplevel()
        except Exception:
            pass
    return None


def _show_confirmation_dialog(parent, title: str, message: str) -> tuple[bool, bool]:
    """Show a yes/no dialog and return (confirmed, suppress_future)."""
    dialog = tk.Toplevel(parent) if parent is not None else tk.Toplevel()
    dialog.title(title)
    dialog.resizable(False, False)
    if parent is not None:
        try:
            dialog.transient(parent)
        except tk.TclError:
            pass

    result = {"confirmed": False, "suppress": False}
    suppress = tk.BooleanVar(dialog, value=False)
    body = tk.Frame(dialog, padx=20, pady=16)
    body.pack(fill=tk.BOTH, expand=True)
    tk.Label(
        body,
        text=message,
        justify=tk.LEFT,
        anchor="w",
        wraplength=440,
    ).pack(fill=tk.X)
    tk.Checkbutton(
        body,
        text="Don't ask again (always allow this action)",
        variable=suppress,
        anchor="w",
    ).pack(fill=tk.X, pady=(14, 4))

    buttons = tk.Frame(body)
    buttons.pack(anchor="e", pady=(12, 0))

    def close(confirmed: bool) -> None:
        result["confirmed"] = confirmed
        result["suppress"] = bool(suppress.get())
        dialog.destroy()

    tk.Button(buttons, text="No", width=10, command=lambda: close(False)).pack(
        side=tk.RIGHT,
        padx=(8, 0),
    )
    yes_button = tk.Button(buttons, text="Yes", width=10, command=lambda: close(True))
    yes_button.pack(side=tk.RIGHT)

    dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))
    dialog.bind("<Escape>", lambda _event: close(False))
    dialog.bind("<Return>", lambda _event: close(True))
    dialog.grab_set()
    yes_button.focus_set()
    dialog.wait_window()
    return bool(result["confirmed"]), bool(result["suppress"])


def ask_confirmation(ctx, preference_key: str, title: str, message: str) -> bool:
    """Ask unless disabled, persisting a checked suppression preference."""
    preferences = getattr(ctx, "preferences", {})
    if not bool(preferences.get(preference_key, True)):
        return True

    confirmed, suppress_future = _show_confirmation_dialog(
        _dialog_parent(ctx),
        title,
        message,
    )
    # Suppression means "always allow", so never persist it after No/close.
    if confirmed and suppress_future:
        preferences[preference_key] = False
        confirmation_vars = getattr(ctx, "_confirmation_vars", {})
        preference_var = confirmation_vars.get(preference_key)
        if preference_var is not None:
            preference_var.set(False)
        try:
            save_preferences(preferences)
        except OSError as error:
            messagebox.showwarning(
                "Settings",
                f"The confirmation was hidden for this session but the setting "
                f"could not be saved:\n{error}",
                parent=_dialog_parent(ctx),
            )
    return confirmed


__all__ = ["ask_confirmation"]
