"""Frozen, scene-derived PIR coverage; no activity labels or occupant position."""

import math

from har.catalogue import category_name, validate_categories


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def capture_context(sensors, objects, runtime):
    """Record categories of object centres visible inside each PIR's actual FOV.

    None means unknown geometry; [] means a known field covering no classified
    objects. Persist the relation so later scene changes cannot rewrite training.
    """
    from utils import PIR_MAX_DISTANCE, PIR_FOV_ANGLE, is_within_fov, is_path_blocked_by_walls

    coverage = {}
    objects_known = all(not obj["categories"] or all(_finite(obj.get(field)) for field in ("x", "y"))
                        for obj in objects)
    for sensor in sensors:
        if str(sensor.type).upper() != "PIR":
            continue
        coverage[sensor.name] = None
        if (not objects_known or runtime.get("walls") is None or runtime.get("doors") is None
                or not all(_finite(getattr(sensor, field, None)) for field in ("x", "y", "direction"))):
            continue
        categories = set()
        for obj in objects:
            if (not obj["categories"]
                    or not is_within_fov(sensor, obj["x"], obj["y"], PIR_MAX_DISTANCE, PIR_FOV_ANGLE)):
                continue
            if not is_path_blocked_by_walls(sensor.x, sensor.y, obj["x"], obj["y"],
                                            runtime["walls"], runtime["doors"]):
                categories.update(obj["categories"])
        coverage[sensor.name] = sorted(categories)
    return {"schema_version": 1, "max_distance": PIR_MAX_DISTANCE, "fov_angle": PIR_FOV_ANGLE,
            "coverage": coverage}


def validate_context(frame, catalogue, *, allow_extra_categories=False):
    """Validate optional new snapshots without inventing context for old data."""
    if "pir_context" not in frame:
        return None
    context = frame["pir_context"]
    if (not isinstance(context, dict) or type(context.get("schema_version")) is not int
            or context["schema_version"] != 1):
        raise ValueError("Invalid PIR context snapshot version.")
    if (not _finite(context.get("max_distance")) or context["max_distance"] <= 0
            or not _finite(context.get("fov_angle")) or not 0 < context["fov_angle"] <= 360):
        raise ValueError("Invalid PIR context field of view.")
    coverage = context.get("coverage")
    identifiers = {sensor["name"] for sensor in frame["sensors"] if str(sensor["type"]).upper() == "PIR"}
    if not isinstance(coverage, dict) or set(coverage) != identifiers:
        raise ValueError("PIR context must identify each recorded PIR exactly once.")
    available = {category_name(name, "device", catalogue) for obj in frame["devices"]
                 for name in (obj.get("categories") or [])}
    for categories in coverage.values():
        if categories is None:
            continue
        if (not isinstance(categories, list) or any(not isinstance(name, str) or not name.strip() for name in categories)
                or len(set(categories)) != len(categories)):
            raise ValueError("PIR context needs unique object category lists or unknown geometry.")
        if not allow_extra_categories:
            validate_categories(categories, "device", catalogue)
        if any(category_name(name, "device", catalogue) not in available for name in categories):
            raise ValueError("PIR context refers to an object category absent from this frame.")
    return context
