from __future__ import annotations

import tkinter as tk
from pathlib import Path

from PIL import Image, ImageColor, ImageOps, ImageTk


TOOL_PALETTE = {
    "palette": "#f3f4f6",
    "button": "#ffffff",
    "hover": "#e5e7eb",
    "active": "#dbeafe",
    "icon": "#273142",
    "accent": "#2563eb",
    "separator": "#cbd5e1",
}


class HoverDescription:
    def __init__(self, widget, text: str, delay_ms: int = 450):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        self._after_id = None
        if self._window is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + 2
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            window,
            text=self.text,
            justify=tk.LEFT,
            bg="#17191c",
            fg="#f3f4f6",
            relief=tk.SOLID,
            bd=1,
            padx=9,
            pady=6,
            font=("Segoe UI", 9),
        )
        label.pack()
        self._window = window

    def _hide(self, _event=None):
        self._cancel()
        if self._window is not None:
            self._window.destroy()
            self._window = None


class ToolPalette:
    """Compact, icon-only canvas tool palette with hover descriptions."""

    def __init__(self, parent):
        self._colors = TOOL_PALETTE
        self.frame = tk.Frame(
            parent,
            bg=self._colors["palette"],
            bd=0,
            padx=4,
            pady=5,
        )
        self._buttons = {}
        self._button_icons = {}
        self._separators = []
        self._images = []
        self._active_key = None

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)

    def add_separator(self):
        separator = tk.Frame(
            self.frame,
            bg=self._colors["separator"],
            height=1,
            width=30,
        )
        separator.pack(
            pady=5,
            padx=3,
        )
        self._separators.append(separator)

    def add_tool(self, key: str, icon: str, description: str, command):
        canvas = tk.Canvas(
            self.frame,
            width=34,
            height=34,
            bg=self._colors["button"],
            highlightthickness=1,
            highlightbackground=self._colors["palette"],
            bd=0,
            cursor="hand2",
        )
        canvas.pack(pady=2)
        self._draw_icon(canvas, icon)
        canvas.bind("<Button-1>", lambda _event: command())
        canvas.bind("<Enter>", lambda _event, k=key: self._hover(k, True))
        canvas.bind("<Leave>", lambda _event, k=key: self._hover(k, False))
        HoverDescription(canvas, description)
        self._buttons[key] = canvas
        self._button_icons[key] = icon
        return canvas

    def set_active(self, key: str | None):
        self._active_key = key
        for button_key, button in self._buttons.items():
            button.configure(
                bg=(
                    self._colors["active"]
                    if button_key == key
                    else self._colors["button"]
                ),
                highlightbackground=(
                    self._colors["accent"]
                    if button_key == key
                    else self._colors["palette"]
                ),
            )

    def _hover(self, key: str, entered: bool):
        button = self._buttons[key]
        if key == self._active_key:
            return
        button.configure(
            bg=self._colors["hover"] if entered else self._colors["button"]
        )

    @staticmethod
    def _prepare_icon(
        path: Path,
        size: int = 26,
        icon_color: str = "#e8edf2",
    ):
        """Normalize arbitrary PNG dimensions/backgrounds for the toolbar."""
        with Image.open(path) as source:
            rgba = source.convert("RGBA")

        alpha = rgba.getchannel("A")
        if alpha.getextrema()[0] >= 250:
            # Opaque black-on-white icons use darkness as their transparency.
            alpha = ImageOps.invert(ImageOps.grayscale(rgba))

        bbox = alpha.getbbox()
        if bbox is None:
            return None
        alpha = alpha.crop(bbox)
        alpha.thumbnail((size, size), Image.Resampling.LANCZOS)

        red, green, blue = ImageColor.getrgb(icon_color)
        normalized = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        glyph = Image.new("RGBA", alpha.size, (red, green, blue, 255))
        glyph.putalpha(alpha)
        offset = ((size - alpha.width) // 2, (size - alpha.height) // 2)
        normalized.alpha_composite(glyph, offset)
        return normalized

    def _load_icon(self, icon: str):
        project_root = Path(__file__).resolve().parents[2]
        path = project_root / "images" / f"{icon}.png"
        if not path.is_file():
            return None
        try:
            normalized = self._prepare_icon(
                path,
                icon_color=self._colors["icon"],
            )
            if normalized is None:
                return None
            photo = ImageTk.PhotoImage(normalized)
            self._images.append(photo)
            return photo
        except (OSError, ValueError):
            return None

    def _draw_icon(self, canvas, icon: str):
        photo = self._load_icon(icon)
        if photo is not None:
            canvas.create_image(17, 17, image=photo)
            canvas._tool_icon_image = photo
            return

        if icon == "select":
            canvas.create_polygon(
                9, 7, 24, 19, 17, 20, 21, 28, 17, 30, 13, 22, 8, 27,
                fill=self._colors["icon"],
                outline=self._colors["icon"],
            )
        elif icon == "manual":
            canvas.create_oval(9, 5, 25, 29, outline=self._colors["icon"], width=2)
            canvas.create_line(17, 6, 17, 28, fill=self._colors["icon"], width=1)
            canvas.create_arc(
                10, 6, 24, 18, start=0, extent=90,
                outline=self._colors["accent"], width=3,
            )
        elif icon == "point":
            canvas.create_line(17, 6, 17, 28, fill=self._colors["icon"], width=1)
            canvas.create_line(6, 17, 28, 17, fill=self._colors["icon"], width=1)
            canvas.create_oval(13, 13, 21, 21, fill=self._colors["accent"], outline="")
        elif icon == "device":
            canvas.create_rectangle(8, 9, 26, 25, outline=self._colors["icon"], width=2)
            canvas.create_line(12, 7, 12, 11, fill=self._colors["icon"], width=2)
            canvas.create_line(22, 7, 22, 11, fill=self._colors["icon"], width=2)
            canvas.create_oval(14, 15, 20, 21, outline=self._colors["accent"], width=2)
        elif icon == "sensor":
            canvas.create_oval(14, 14, 20, 20, fill=self._colors["accent"], outline="")
            for inset in (7, 3):
                canvas.create_arc(
                    inset, inset, 34 - inset, 34 - inset,
                    start=315, extent=90, outline=self._colors["icon"], width=2,
                )
        elif icon == "wall":
            canvas.create_line(7, 25, 27, 9, fill=self._colors["icon"], width=5)
            canvas.create_line(7, 28, 29, 10, fill=self._colors["accent"], width=1)
        elif icon == "door":
            canvas.create_line(8, 27, 8, 8, 27, 8, fill=self._colors["icon"], width=2)
            canvas.create_line(8, 27, 24, 14, fill=self._colors["accent"], width=3)
            canvas.create_arc(
                8, 8, 40, 40, start=90, extent=45,
                outline=self._colors["icon"], dash=(2, 2),
            )
