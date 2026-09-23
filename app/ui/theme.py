from __future__ import annotations

import tkinter as tk
from collections.abc import Mapping
from tkinter import ttk


LIGHT_PALETTE = {
    "background": "#ffffff",
    "surface": "#f3f4f6",
    "surface_hover": "#e5e7eb",
    "panel": "#e5e7eb",
    "entry": "#ffffff",
    "border": "#cbd5e1",
    "text": "#111827",
    "muted": "#4b5563",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "disabled": "#94a3b8",
    "canvas": "#ffffff",
    "grid": "#d1d5db",
    "selection": "#dbeafe",
}


def get_theme_mode(source: str | Mapping[str, object] | None) -> str:
    """Compatibility helper: the application now has one light appearance."""
    return "light"


def get_theme_palette(source: str | Mapping[str, object] | None) -> dict[str, str]:
    return LIGHT_PALETTE.copy()


def set_theme_role(widget, role: str):
    """Mark a Tk widget that needs a specific colour role."""
    widget._theme_role = role
    return widget


def get_widget_theme_mode(widget) -> str:
    """Compatibility helper for windows that use the shared light styling."""
    return "light"


def _apply_ttk_widget_theme(widget, palette: Mapping[str, str]) -> bool:
    if not isinstance(widget, ttk.Widget):
        return False

    style = ttk.Style(widget)
    # Native Windows themes ignore several background/foreground options on
    # notebook tabs and readonly comboboxes. Clam honours the complete colour
    # map and keeps text contrast consistent.
    if "clam" in style.theme_names() and style.theme_use() != "clam":
        style.theme_use("clam")
    style.configure("App.TFrame", background=palette["background"])
    style.configure(
        "App.TLabel",
        background=palette["background"],
        foreground=palette["text"],
    )
    style.configure(
        "App.TButton",
        background=palette["surface_hover"],
        foreground=palette["text"],
        padding=(9, 5),
    )
    style.map(
        "App.TButton",
        background=[("active", palette["border"])],
        foreground=[("disabled", palette["disabled"])],
    )
    style.configure(
        "App.TCheckbutton",
        background=palette["background"],
        foreground=palette["text"],
    )
    style.configure(
        "App.TLabelframe",
        background=palette["surface"],
        foreground=palette["text"],
    )
    style.configure(
        "App.TLabelframe.Label",
        background=palette["surface"],
        foreground=palette["text"],
    )
    style.configure(
        "App.TNotebook",
        background=palette["background"],
        bordercolor=palette["border"],
    )
    style.configure(
        "App.TNotebook.Tab",
        background=palette["surface"],
        foreground=palette["text"],
        padding=(12, 6),
    )
    style.map(
        "App.TNotebook.Tab",
        background=[("selected", palette["accent"]), ("active", palette["surface_hover"])],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "App.TCombobox",
        fieldbackground=palette["entry"],
        background=palette["entry"],
        foreground=palette["text"],
        arrowcolor=palette["text"],
    )
    style.map(
        "App.TCombobox",
        fieldbackground=[
            ("readonly", palette["entry"]),
            ("disabled", palette["surface"]),
        ],
        foreground=[
            ("readonly", palette["text"]),
            ("disabled", palette["disabled"]),
        ],
        selectbackground=[("readonly", palette["entry"])],
        selectforeground=[("readonly", palette["text"])],
    )
    for orientation in ("Horizontal", "Vertical"):
        style.configure(
            f"App.{orientation}.TScrollbar",
            background=palette["surface_hover"],
            troughcolor=palette["entry"],
            bordercolor=palette["border"],
            arrowcolor=palette["text"],
        )
        style.configure(
            f"App.{orientation}.TProgressbar",
            background=palette["accent"],
            troughcolor=palette["entry"],
            bordercolor=palette["border"],
        )

    if isinstance(widget, ttk.Notebook):
        _configure(widget, style="App.TNotebook")
    elif isinstance(widget, ttk.LabelFrame):
        _configure(widget, style="App.TLabelframe")
    elif isinstance(widget, ttk.Frame):
        _configure(widget, style="App.TFrame")
    elif isinstance(widget, ttk.Label):
        _configure(widget, style="App.TLabel")
    elif isinstance(widget, ttk.Button):
        _configure(widget, style="App.TButton")
    elif isinstance(widget, ttk.Checkbutton):
        _configure(widget, style="App.TCheckbutton")
    elif isinstance(widget, ttk.Combobox):
        _configure(widget, style="App.TCombobox")
    elif isinstance(widget, ttk.Scrollbar):
        orientation = str(widget.cget("orient")).capitalize()
        _configure(widget, style=f"App.{orientation}.TScrollbar")
    elif isinstance(widget, ttk.Progressbar):
        orientation = str(widget.cget("orient")).capitalize()
        if not str(widget.cget("style")):
            _configure(widget, style=f"App.{orientation}.TProgressbar")
    return True


def _configure(widget, **options) -> None:
    try:
        widget.configure(**options)
    except (tk.TclError, AttributeError):
        pass


def apply_theme_tree(
    widget,
    source: str | Mapping[str, object],
    inherited_role: str = "background",
) -> dict[str, str]:
    """Apply a palette to ordinary Tk widgets, respecting optional role tags."""
    palette = get_theme_palette(source)
    widget._theme_mode = "light"
    if bool(getattr(widget, "_theme_exempt", False)):
        return palette

    role = str(getattr(widget, "_theme_role", inherited_role))
    container_role = role if role in {"background", "surface", "panel"} else inherited_role
    parent_bg = palette.get(container_role, palette["background"])

    if isinstance(widget, tk.Menu):
        _configure(
            widget,
            bg=palette["surface"],
            fg=palette["text"],
            activebackground=palette["accent"],
            activeforeground="#ffffff",
            disabledforeground=palette["disabled"],
            selectcolor=palette["accent"],
        )
        try:
            last = widget.index("end")
            for index in range((last if last is not None else -1) + 1):
                try:
                    widget.entryconfigure(
                        index,
                        background=palette["surface"],
                        foreground=palette["text"],
                        activebackground=palette["accent"],
                        activeforeground="#ffffff",
                        selectcolor=palette["accent"],
                    )
                except tk.TclError:
                    pass
        except tk.TclError:
            pass
        return palette

    if _apply_ttk_widget_theme(widget, palette):
        pass
    elif isinstance(widget, (tk.Tk, tk.Toplevel, tk.Frame)):
        _configure(widget, bg=parent_bg)
    elif isinstance(widget, tk.LabelFrame):
        _configure(widget, bg=parent_bg, fg=palette["text"])
    elif isinstance(widget, tk.Label):
        foreground = palette["muted"] if role == "muted" else palette["text"]
        _configure(widget, bg=parent_bg, fg=foreground)
    elif isinstance(widget, (tk.Entry, tk.Text)):
        _configure(
            widget,
            bg=palette["entry"],
            fg=palette["text"],
            insertbackground=palette["text"],
            selectbackground=palette["accent"],
            selectforeground="#ffffff",
            highlightbackground=palette["border"],
            highlightcolor=palette["accent"],
        )
        if isinstance(widget, tk.Entry):
            _configure(
                widget,
                readonlybackground=palette["entry"],
                disabledbackground=palette["surface"],
                disabledforeground=palette["disabled"],
            )
    elif isinstance(widget, tk.Listbox):
        _configure(
            widget,
            bg=palette["entry"],
            fg=palette["text"],
            selectbackground=palette["accent"],
            selectforeground="#ffffff",
            highlightbackground=palette["border"],
            highlightcolor=palette["accent"],
        )
    elif isinstance(widget, (tk.Radiobutton, tk.Checkbutton)):
        _configure(
            widget,
            bg=parent_bg,
            fg=palette["text"],
            activebackground=parent_bg,
            activeforeground=palette["text"],
            disabledforeground=palette["disabled"],
            selectcolor=palette["entry"],
        )
    elif isinstance(widget, tk.Menubutton):
        _configure(
            widget,
            bg=palette["entry"],
            fg=palette["text"],
            activebackground=palette["selection"],
            activeforeground=palette["text"],
            disabledforeground=palette["disabled"],
            highlightbackground=palette["border"],
            highlightcolor=palette["accent"],
        )
    elif isinstance(widget, tk.Button):
        primary = role == "primary"
        danger = role == "danger"
        normal = "#dc2626" if danger else (
            palette["accent"] if primary else palette["surface_hover"]
        )
        hover = "#b91c1c" if danger else (
            palette["accent_hover"] if primary else palette["border"]
        )
        widget._theme_normal = normal
        widget._theme_hover = hover
        _configure(
            widget,
            bg=normal,
            fg="#ffffff" if primary or danger else palette["text"],
            activebackground=hover,
            activeforeground="#ffffff" if primary or danger else palette["text"],
            disabledforeground=palette["disabled"],
        )
    elif isinstance(widget, tk.Scale):
        _configure(
            widget,
            bg=parent_bg,
            fg=palette["text"],
            activebackground=palette["accent"],
            troughcolor=palette["entry"],
            highlightbackground=parent_bg,
        )
    elif isinstance(widget, tk.Canvas):
        if role == "plot":
            _configure(widget, bg="#ffffff")
        elif role == "canvas":
            _configure(widget, bg=palette["canvas"])
        else:
            _configure(widget, bg=parent_bg, highlightbackground=palette["border"])

    child_role = container_role
    try:
        children = widget.winfo_children()
    except (tk.TclError, AttributeError):
        children = ()
    for child in children:
        apply_theme_tree(child, "light", child_role)
    return palette
