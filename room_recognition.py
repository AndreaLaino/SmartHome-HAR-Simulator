from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from typing import Iterable

from models import Device, Door, Point, Room, Sensor, Wall


ROOM_TYPES = (
    "Unknown",
    "Bathroom",
    "Bedroom",
    "Dining room",
    "Hall",
    "Kitchen",
    "Living room",
    "Office",
    "Laundry",
)

_DEVICE_SCORES = {
    "oven": {"Kitchen": 6},
    "dishwasher": {"Kitchen": 5},
    "coffee machine": {"Kitchen": 4},
    "fridge": {"Kitchen": 5},
    "refrigerator": {"Kitchen": 5},
    "washing machine": {"Laundry": 6, "Bathroom": 2},
    "computer": {"Office": 4},
    "pc": {"Office": 4},
    "tv": {"Living room": 5},
}

_POINT_KEYWORDS = {
    "Bathroom": ("bath", "bathtub", "shower", "sink", "wc", "toilet"),
    "Bedroom": ("bed", "bedside"),
    "Dining room": ("dining",),
    "Kitchen": ("kitchen", "oven", "fridge", "dishwasher"),
    "Living room": ("couch", "sofa", "tv"),
    "Office": ("desk", "computer", "office"),
    "Hall": ("hall", "entrance", "shoeshelf"),
    "Laundry": ("laundry", "washing"),
}


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).replace("_", " ").strip().lower())


def _keyword_counts(points: list[Point]) -> Counter:
    counts = Counter()
    for point in points:
        name = _normalize_label(point.name)
        for room_type, keywords in _POINT_KEYWORDS.items():
            for keyword in keywords:
                if keyword in name:
                    counts[(room_type, keyword)] += 1
                    break
    return counts


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Return True when a point is inside or on the edge of a polygon."""
    if len(polygon) < 3:
        return False

    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        cross = (x - xi) * (yj - yi) - (y - yi) * (xj - xi)
        if abs(cross) < 1e-7 and min(xi, xj) <= x <= max(xi, xj) and min(yi, yj) <= y <= max(yi, yj):
            return True
        if (yi > y) != (yj > y):
            x_intersection = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_intersection:
                inside = not inside
        j = i
    return inside


def polygon_area(polygon: list[tuple[float, float]]) -> float:
    return sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(polygon, polygon[1:] + polygon[:1])
    ) / 2.0


def _canonical_polygon(polygon: list[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    vertices = [(round(x, 4), round(y, 4)) for x, y in polygon]
    variants = []
    for sequence in (vertices, list(reversed(vertices))):
        for index in range(len(sequence)):
            variants.append(tuple(sequence[index:] + sequence[:index]))
    return min(variants)


def _room_id(polygon: list[tuple[float, float]]) -> str:
    canonical = repr(_canonical_polygon(polygon)).encode("utf-8")
    return f"room-{hashlib.sha1(canonical).hexdigest()[:10]}"


def _raw_segments(walls: Iterable[Wall], doors: Iterable[Door]):
    for segment in list(walls) + list(doors):
        start = (float(segment.x1), float(segment.y1))
        end = (float(segment.x2), float(segment.y2))
        if start != end:
            yield start, end


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _point_on_segment(point, start, end, tolerance=1e-6) -> bool:
    length = _distance(start, end)
    if length <= tolerance:
        return False
    cross = abs(
        (point[0] - start[0]) * (end[1] - start[1])
        - (point[1] - start[1]) * (end[0] - start[0])
    )
    if cross / length > tolerance:
        return False
    return (
        min(start[0], end[0]) - tolerance <= point[0] <= max(start[0], end[0]) + tolerance
        and min(start[1], end[1]) - tolerance <= point[1] <= max(start[1], end[1]) + tolerance
    )


def _project_on_segment(point, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return start, 0.0
    ratio = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq
    projection = (start[0] + ratio * dx, start[1] + ratio * dy)
    return projection, ratio


def _planar_segments(
    walls: Iterable[Wall],
    doors: Iterable[Door],
    snap_tolerance: float = 2.0,
):
    raw = list(_raw_segments(walls, doors))
    endpoints = list(dict.fromkeys(point for segment in raw for point in segment))

    parent = list(range(len(endpoints)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first, second):
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    for first in range(len(endpoints)):
        for second in range(first + 1, len(endpoints)):
            if _distance(endpoints[first], endpoints[second]) <= snap_tolerance:
                union(first, second)

    groups: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for index, point in enumerate(endpoints):
        groups[find(index)].append(point)
    representatives = {
        root: (
            sum(point[0] for point in group) / len(group),
            sum(point[1] for point in group) / len(group),
        )
        for root, group in groups.items()
    }
    endpoint_map = {
        point: representatives[find(index)]
        for index, point in enumerate(endpoints)
    }
    snapped = [(endpoint_map[start], endpoint_map[end]) for start, end in raw]

    occurrence_count: dict[tuple[float, float], int] = defaultdict(int)
    for start, end in snapped:
        occurrence_count[start] += 1
        occurrence_count[end] += 1

    projected = {}
    normalized_endpoints = set(point for segment in snapped for point in segment)
    for point in normalized_endpoints:
        best = point
        best_distance = snap_tolerance + 1.0
        if occurrence_count[point] == 1:
            for start, end in snapped:
                if point in (start, end):
                    continue
                projection, ratio = _project_on_segment(point, start, end)
                distance = _distance(point, projection)
                if -1e-9 <= ratio <= 1.0 + 1e-9 and distance <= snap_tolerance and distance < best_distance:
                    best = projection
                    best_distance = distance
        projected[point] = best

    snapped = [(projected[start], projected[end]) for start, end in snapped]
    vertices = set(point for segment in snapped for point in segment)
    for start, end in snapped:
        split_points = [point for point in vertices if _point_on_segment(point, start, end)]
        split_points.sort(key=lambda point: _distance(start, point))
        for first, second in zip(split_points, split_points[1:]):
            if first != second:
                yield first, second


def find_room_polygons(
    walls: Iterable[Wall],
    doors: Iterable[Door],
) -> list[list[tuple[float, float]]]:
    """Find bounded faces in the planar graph formed by walls and doors."""
    adjacency: dict[tuple[float, float], set[tuple[float, float]]] = defaultdict(set)
    for start, end in _planar_segments(walls, doors):
        adjacency[start].add(end)
        adjacency[end].add(start)

    ordered = {
        vertex: sorted(
            neighbours,
            key=lambda neighbour: math.atan2(neighbour[1] - vertex[1], neighbour[0] - vertex[0]),
        )
        for vertex, neighbours in adjacency.items()
    }
    visited: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    faces: list[list[tuple[float, float]]] = []

    for start, neighbours in ordered.items():
        for end in neighbours:
            edge = (start, end)
            if edge in visited:
                continue

            face = []
            current_start, current_end = edge
            while (current_start, current_end) not in visited:
                visited.add((current_start, current_end))
                face.append(current_start)
                next_neighbours = ordered.get(current_end, [])
                if not next_neighbours or current_start not in next_neighbours:
                    face = []
                    break
                index = next_neighbours.index(current_start)
                next_vertex = next_neighbours[(index - 1) % len(next_neighbours)]
                current_start, current_end = current_end, next_vertex

            if len(face) >= 3 and (current_start, current_end) == edge:
                faces.append(face)

    candidates = [face for face in faces if abs(polygon_area(face)) >= 1e-6]
    if len(candidates) <= 1:
        return []

    # Every bounded face is discovered once, together with the exterior face.
    exterior = max(candidates, key=lambda face: abs(polygon_area(face)))
    rooms = [face for face in candidates if face is not exterior]
    return sorted(rooms, key=lambda face: (min(y for _, y in face), min(x for x, _ in face)))


def _objects_in_polygon(objects, polygon):
    return [obj for obj in objects if point_in_polygon(float(obj.x), float(obj.y), polygon)]


def _classify_room(
    sensors: list[Sensor],
    devices: list[Device],
    points: list[Point],
) -> tuple[str, float]:
    scores: dict[str, float] = defaultdict(float)
    device_counts = Counter(_normalize_label(device.type) for device in devices)
    keyword_counts = _keyword_counts(points)

    for device_type, count in device_counts.items():
        for room_type, score in _DEVICE_SCORES.get(device_type, {}).items():
            scores[room_type] += score * count

    for (room_type, _keyword), count in keyword_counts.items():
        scores[room_type] += 2 * count

    computer_count = device_counts["computer"] + device_counts["pc"]
    desk_count = sum(
        count
        for (room_type, keyword), count in keyword_counts.items()
        if room_type == "Office" and keyword == "desk"
    )
    chair_count = sum(
        1
        for point in points
        if any(word in _normalize_label(point.name) for word in ("chair", "seat"))
    )
    bed_count = sum(
        count
        for (room_type, keyword), count in keyword_counts.items()
        if room_type == "Bedroom" and keyword in ("bed", "bedside")
    )

    # Repeated workstation elements provide independent evidence. There is no
    # fixed "three computers means office" threshold.
    workstation_count = computer_count + desk_count
    if computer_count:
        scores["Office"] += 1.5 * desk_count
        scores["Office"] += min(chair_count, workstation_count)
        if workstation_count > 1:
            scores["Office"] += math.log2(workstation_count) * 2

    # Conflicting evidence reduces confidence instead of forcing a fixed label.
    scores["Office"] -= 1.5 * bed_count
    scores["Bedroom"] -= 0.75 * computer_count

    sensor_types = [str(sensor.type) for sensor in sensors]
    if sensor_types.count("Weight") >= 1:
        scores["Bedroom"] += 1
        scores["Office"] += 0.5
    if "Temperature" in sensor_types:
        scores["Kitchen"] += 0.5
        scores["Bathroom"] += 0.5
    if sensor_types and all(sensor_type in ("PIR", "Switch") for sensor_type in sensor_types):
        scores["Hall"] += 1

    if not scores:
        return "Unknown", 0.0

    positive_scores = {
        room_type: max(0.0, score)
        for room_type, score in scores.items()
    }
    ranked = sorted(positive_scores.items(), key=lambda item: (-item[1], item[0]))
    best_type, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score < 2:
        return "Unknown", min(best_score / 2.0, 0.49)

    confidence = best_score / (best_score + second_score + 1.0)
    return best_type, round(min(confidence, 0.99), 2)


def recognize_rooms(
    walls: Iterable[Wall],
    doors: Iterable[Door],
    sensors: Iterable[Sensor],
    devices: Iterable[Device],
    points: Iterable[Point],
    overrides: dict[str, str] | None = None,
) -> list[Room]:
    sensor_list = list(sensors)
    device_list = list(devices)
    point_list = list(points)
    overrides = overrides or {}
    rooms = []

    for polygon in find_room_polygons(walls, doors):
        room_sensors = _objects_in_polygon(sensor_list, polygon)
        room_devices = _objects_in_polygon(device_list, polygon)
        room_points = _objects_in_polygon(point_list, polygon)
        inferred_type, confidence = _classify_room(room_sensors, room_devices, room_points)
        room_id = _room_id(polygon)
        manual_type = overrides.get(room_id)
        if manual_type not in ROOM_TYPES or manual_type == "Unknown":
            manual_type = None
        rooms.append(
            Room(
                room_id=room_id,
                polygon=polygon,
                inferred_type=inferred_type,
                manual_type=manual_type,
                confidence=confidence,
                sensor_names=[sensor.name for sensor in room_sensors],
                device_names=[device.name for device in room_devices],
                point_names=[point.name for point in room_points],
            )
        )
    return rooms
