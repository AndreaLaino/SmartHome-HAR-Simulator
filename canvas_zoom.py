from __future__ import annotations


MIN_ZOOM = 0.5
MAX_ZOOM = 2.5
ZOOM_STEP = 1.2
TRACKPAD_ZOOM_STEP = 1.025
TRACKPAD_INTERVAL_MS = 16


def get_zoom(canvas) -> float:
    return float(getattr(canvas, "_zoom_factor", 1.0) or 1.0)


def to_canvas(canvas, x: float, y: float) -> tuple[float, float]:
    zoom = get_zoom(canvas)
    return float(x) * zoom, float(y) * zoom


def to_canvas_length(canvas, value: float) -> float:
    return float(value) * get_zoom(canvas)


def event_to_logical(canvas, event) -> tuple[float, float]:
    zoom = get_zoom(canvas)
    return canvas.canvasx(event.x) / zoom, canvas.canvasy(event.y) / zoom


def canvas_to_logical(canvas, x: float, y: float) -> tuple[float, float]:
    zoom = get_zoom(canvas)
    return float(x) / zoom, float(y) / zoom


def update_scrollregion(canvas) -> None:
    bbox = canvas.bbox("all")
    if bbox is not None:
        canvas.configure(scrollregion=bbox)


def set_zoom(
    canvas,
    target_zoom: float,
    *,
    screen_x=None,
    screen_y=None,
    redraw_background: bool = True,
) -> float:
    current = get_zoom(canvas)
    target = max(MIN_ZOOM, min(MAX_ZOOM, float(target_zoom)))
    if abs(target - current) < 1e-9:
        return current

    if screen_x is None:
        screen_x = canvas.winfo_width() / 2
    if screen_y is None:
        screen_y = canvas.winfo_height() / 2

    logical_focus_x = canvas.canvasx(screen_x) / current
    logical_focus_y = canvas.canvasy(screen_y) / current
    ratio = target / current
    canvas.scale("all", 0, 0, ratio, ratio)
    scale_label_anchors = getattr(canvas, "_scale_map_label_anchors", None)
    if callable(scale_label_anchors):
        scale_label_anchors(ratio)
    canvas._zoom_factor = target
    background_callback = getattr(canvas, "_redraw_zoom_background", None)
    if redraw_background and callable(background_callback):
        background_callback(target)
    update_scrollregion(canvas)
    _keep_focus_under_pointer(
        canvas,
        logical_focus_x * target,
        logical_focus_y * target,
        float(screen_x),
        float(screen_y),
    )

    return target


def _keep_focus_under_pointer(
    canvas,
    focus_x: float,
    focus_y: float,
    screen_x: float,
    screen_y: float,
) -> None:
    bbox = canvas.bbox("all")
    if bbox is None:
        return

    region_width = max(1.0, float(bbox[2] - bbox[0]))
    region_height = max(1.0, float(bbox[3] - bbox[1]))
    viewport_width = max(1.0, float(canvas.winfo_width()))
    viewport_height = max(1.0, float(canvas.winfo_height()))

    left = focus_x - screen_x
    top = focus_y - screen_y
    max_left = max(float(bbox[0]), float(bbox[2]) - viewport_width)
    max_top = max(float(bbox[1]), float(bbox[3]) - viewport_height)
    left = min(max(left, float(bbox[0])), max_left)
    top = min(max(top, float(bbox[1])), max_top)

    canvas.xview_moveto((left - bbox[0]) / region_width)
    canvas.yview_moveto((top - bbox[1]) / region_height)


def zoom_in(canvas, *, screen_x=None, screen_y=None) -> float:
    return set_zoom(canvas, get_zoom(canvas) * ZOOM_STEP, screen_x=screen_x, screen_y=screen_y)


def zoom_out(canvas, *, screen_x=None, screen_y=None) -> float:
    return set_zoom(canvas, get_zoom(canvas) / ZOOM_STEP, screen_x=screen_x, screen_y=screen_y)


def zoom_trackpad(canvas, direction: int, *, screen_x: float, screen_y: float) -> float:
    factor = TRACKPAD_ZOOM_STEP if direction > 0 else 1.0 / TRACKPAD_ZOOM_STEP
    return set_zoom(
        canvas,
        get_zoom(canvas) * factor,
        screen_x=screen_x,
        screen_y=screen_y,
    )


def queue_trackpad_zoom(
    canvas,
    direction: int,
    *,
    screen_x: float,
    screen_y: float,
    on_update=None,
) -> None:
    """Coalesce trackpad events into one synchronized update per UI frame."""
    pending = int(getattr(canvas, "_zoom_pending_steps", 0) or 0)
    canvas._zoom_pending_steps = pending + (1 if direction > 0 else -1)
    canvas._zoom_pointer = (float(screen_x), float(screen_y))
    canvas._zoom_on_update = on_update

    if getattr(canvas, "_zoom_trackpad_job", None) is None:
        canvas._zoom_trackpad_job = canvas.after(
            TRACKPAD_INTERVAL_MS,
            lambda: _apply_queued_trackpad_zoom(canvas),
        )


def _apply_queued_trackpad_zoom(canvas) -> None:
    canvas._zoom_trackpad_job = None
    pending = int(getattr(canvas, "_zoom_pending_steps", 0) or 0)
    canvas._zoom_pending_steps = 0
    if pending == 0:
        return

    # Limit one frame's jump while retaining the direction and responsiveness.
    steps = max(-3, min(3, pending))
    pointer_x, pointer_y = getattr(
        canvas,
        "_zoom_pointer",
        (canvas.winfo_width() / 2, canvas.winfo_height() / 2),
    )
    target = get_zoom(canvas) * (TRACKPAD_ZOOM_STEP ** steps)
    set_zoom(
        canvas,
        target,
        screen_x=pointer_x,
        screen_y=pointer_y,
    )

    on_update = getattr(canvas, "_zoom_on_update", None)
    if callable(on_update):
        on_update(get_zoom(canvas))


def reset_zoom(canvas) -> float:
    zoom = set_zoom(canvas, 1.0)
    canvas.xview_moveto(0)
    canvas.yview_moveto(0)
    return zoom


def bind_canvas_pan(canvas) -> None:
    """Pan with a two-finger/secondary click drag without using Button-1."""
    def start_pan(event):
        canvas.scan_mark(event.x, event.y)
        canvas.configure(cursor="fleur")
        return "break"

    def drag_pan(event):
        canvas.scan_dragto(event.x, event.y, gain=1)
        return "break"

    def stop_pan(_event):
        canvas.configure(cursor="")
        return "break"

    # Depending on macOS/Tk settings, a two-finger click is Button-2 or Button-3.
    for button in (2, 3):
        canvas.bind(f"<ButtonPress-{button}>", start_pan)
        canvas.bind(f"<B{button}-Motion>", drag_pan)
        canvas.bind(f"<ButtonRelease-{button}>", stop_pan)
