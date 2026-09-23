from __future__ import annotations


MIN_ZOOM = 0.5
MAX_ZOOM = 2.5
ZOOM_STEP = 1.2
TRACKPAD_ZOOM_STEP = 1.025
TRACKPAD_INTERVAL_MS = 16

GRID_WIDTH = 1038
GRID_HEIGHT = 811
GRID_SPACING = 25
GRID_BACKGROUND = "white"
GRID_LINE_COLOR = "#c7c7c7"
GRID_TAG = "background_grid"


def draw_grid_background(
    canvas,
    *,
    width: int = GRID_WIDTH,
    height: int = GRID_HEIGHT,
    spacing: int = GRID_SPACING,
) -> None:
    """Draw a native grid that always covers the canvas viewport."""
    if width <= 0 or height <= 0 or spacing <= 0:
        raise ValueError("Grid dimensions and spacing must be positive")

    def redraw(zoom=None) -> None:
        current_zoom = get_zoom(canvas) if zoom is None else float(zoom)
        background = str(getattr(canvas, "_grid_background", GRID_BACKGROUND))
        line_color = str(getattr(canvas, "_grid_line_color", GRID_LINE_COLOR))

        # Exclude the previous grid before measuring actual scenario content.
        canvas.delete(GRID_TAG)
        content_bbox = canvas.bbox("all")
        content_width = max(0, content_bbox[2]) if content_bbox else 0
        content_height = max(0, content_bbox[3]) if content_bbox else 0
        render_width = max(
            float(width) * current_zoom,
            float(canvas.winfo_width()),
            float(content_width),
        )
        render_height = max(
            float(height) * current_zoom,
            float(canvas.winfo_height()),
            float(content_height),
        )
        rendered_spacing = float(spacing) * current_zoom

        canvas.create_rectangle(
            0,
            0,
            render_width,
            render_height,
            fill=background,
            outline="",
            width=0,
            tags=(GRID_TAG,),
        )
        if bool(getattr(canvas, "_show_grid", True)):
            line_number = 1
            while line_number * rendered_spacing < render_width:
                x = line_number * rendered_spacing
                canvas.create_line(
                    x,
                    0,
                    x,
                    render_height,
                    fill=line_color,
                    width=1,
                    tags=(GRID_TAG,),
                )
                line_number += 1
            line_number = 1
            while line_number * rendered_spacing < render_height:
                y = line_number * rendered_spacing
                canvas.create_line(
                    0,
                    y,
                    render_width,
                    y,
                    fill=line_color,
                    width=1,
                    tags=(GRID_TAG,),
                )
                line_number += 1

        canvas.tag_lower(GRID_TAG)
        canvas.configure(scrollregion=(0, 0, render_width, render_height))

    canvas._redraw_zoom_background = redraw
    if not getattr(canvas, "_grid_resize_bound", False):
        canvas.bind(
            "<Configure>",
            lambda _event: canvas._redraw_zoom_background(),
            add="+",
        )
        canvas._grid_resize_bound = True

    redraw()


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


def snap_logical_position(
    canvas,
    x: float,
    y: float,
    *,
    spacing: int = GRID_SPACING,
) -> tuple[int, int]:
    """Snap a logical position when the canvas grid-snap preference is active."""
    if not bool(getattr(canvas, "_snap_to_grid", False)):
        return int(round(x)), int(round(y))
    return (
        int(round(float(x) / spacing) * spacing),
        int(round(float(y) / spacing) * spacing),
    )


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


def set_canvas_cursor(canvas, cursor: str) -> None:
    """Set the persistent tool cursor that temporary gestures should restore."""
    canvas._tool_cursor = cursor
    canvas.configure(cursor=cursor)


def bind_canvas_pan(canvas) -> None:
    """Pan with the middle mouse button without using left or right click."""
    def start_pan(event):
        canvas.scan_mark(event.x, event.y)
        canvas._pan_previous_cursor = getattr(canvas, "_tool_cursor", "")
        canvas.configure(cursor="fleur")
        return "break"

    def drag_pan(event):
        canvas.scan_dragto(event.x, event.y, gain=1)
        return "break"

    def stop_pan(_event):
        canvas.configure(
            cursor=getattr(canvas, "_pan_previous_cursor", "")
        )
        return "break"

    canvas.bind("<ButtonPress-2>", start_pan)
    canvas.bind("<B2-Motion>", drag_pan)
    canvas.bind("<ButtonRelease-2>", stop_pan)
