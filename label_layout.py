from __future__ import annotations

import tkinter as tk
import weakref

from canvas_zoom import to_canvas


_LABELS = weakref.WeakKeyDictionary()


def _scale_label_anchors(canvas, ratio: float) -> None:
    for metadata in _LABELS.get(canvas, {}).values():
        anchor_x, anchor_y = metadata["anchor"]
        metadata["anchor"] = (anchor_x * ratio, anchor_y * ratio)


def create_map_label(
    canvas,
    x: float,
    y: float,
    *,
    text: str,
    fill: str,
    tags=(),
    kind: str = "object",
    font=None,
    hover_target=None,
):
    """Create a readable label near an object or at a room centroid."""
    normalized_tags = (tags,) if isinstance(tags, str) else tuple(tags)
    is_room = kind == "room"
    anchor_x, anchor_y = to_canvas(canvas, x, y)
    label_x = anchor_x if is_room else anchor_x + 8
    label_y = anchor_y if is_room else anchor_y - 8
    item_id = canvas.create_text(
        label_x,
        label_y,
        text=text,
        fill=fill,
        anchor=tk.CENTER if is_room else tk.SW,
        font=font,
        tags=normalized_tags + ("map_label", f"map_label_{kind}"),
        state=tk.NORMAL,
    )
    labels = _LABELS.setdefault(canvas, {})
    labels[item_id] = {
        "anchor": (anchor_x, anchor_y),
        "kind": kind,
    }
    canvas._scale_map_label_anchors = lambda ratio: _scale_label_anchors(canvas, ratio)
    return item_id


def forget_canvas_labels(canvas) -> None:
    _LABELS.pop(canvas, None)


def _overlaps(first, second, padding=3) -> bool:
    return not (
        first[2] + padding < second[0]
        or first[0] - padding > second[2]
        or first[3] + padding < second[1]
        or first[1] - padding > second[3]
    )


def layout_map_labels(canvas) -> None:
    """Move only room labels when their centered position is occupied."""
    if canvas is None:
        return
    labels = _LABELS.get(canvas, {})
    if not labels:
        return

    canvas.update_idletasks()
    occupied = [
        bbox
        for item_id in canvas.find_all()
        if canvas.type(item_id) in ("oval", "rectangle")
        for bbox in [canvas.bbox(item_id)]
        if bbox is not None
    ]
    live_items = [
        (item_id, metadata)
        for item_id, metadata in labels.items()
        if canvas.type(item_id) and metadata["kind"] == "room"
    ]
    live_items.sort(key=lambda item: item[0])

    for item_id, metadata in live_items:
        anchor_x, anchor_y = metadata["anchor"]
        selected = None
        for offset_x, offset_y in (
            (0, 0),
            (0, -24),
            (0, 24),
            (-45, 0),
            (45, 0),
            (0, -48),
            (0, 48),
        ):
            canvas.coords(item_id, anchor_x + offset_x, anchor_y + offset_y)
            bbox = canvas.bbox(item_id)
            if bbox is not None and not any(_overlaps(bbox, other) for other in occupied):
                selected = (offset_x, offset_y, bbox)
                break

        if selected is None:
            canvas.coords(item_id, anchor_x, anchor_y)
            bbox = canvas.bbox(item_id)
            selected = (0, 0, bbox)

        _offset_x, _offset_y, bbox = selected
        if bbox is not None:
            occupied.append(bbox)

    canvas.tag_raise("map_label")
