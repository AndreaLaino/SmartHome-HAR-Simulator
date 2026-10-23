"""Metrics for held-out multi-label activity-recognition experiments.

Abstention affects exact accuracy and coverage, while activity confusion counts
retain every actual prediction, including partial predictions on abstained rows.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from numbers import Real


def _labels(value, name):
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise ValueError(f"{name} must be a collection of activity labels.")
    result = set()
    for label in value:
        if not isinstance(label, str) or not label.strip() or label != label.strip():
            raise ValueError(f"{name} contains an invalid activity label.")
        result.add(label)
    return frozenset(result)


def _number(value, name, *, probability=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number.")
    try:
        result = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number.") from error
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number.")
    if probability and not 0 <= result <= 1:
        raise ValueError(f"{name} must be between 0 and 1.")
    return result


def _identifier(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string without surrounding whitespace.")
    return value


def _validate_records(records):
    if isinstance(records, (str, bytes, Mapping)) or not isinstance(records, Iterable):
        raise ValueError("records must be a collection of labelled prediction records.")
    validated = []
    required = ("expected", "predicted", "abstained", "house_id", "run_id", "time_minutes")
    for index, record in enumerate(records):
        prefix = f"record {index}"
        if not isinstance(record, Mapping):
            raise ValueError(f"{prefix} must be a mapping.")
        missing = [name for name in required if name not in record]
        if missing:
            raise ValueError(f"{prefix} is missing: {', '.join(missing)}.")
        if not isinstance(record["abstained"], bool):
            raise ValueError(f"{prefix} abstained must be a boolean.")
        row = {
            "expected": _labels(record["expected"], f"{prefix} expected"),
            "predicted": _labels(record["predicted"], f"{prefix} predicted"),
            "abstained": record["abstained"],
            "house_id": _identifier(record["house_id"], f"{prefix} house_id"),
            "run_id": _identifier(record["run_id"], f"{prefix} run_id"),
            "time_minutes": _number(record["time_minutes"], f"{prefix} time_minutes"),
        }
        if "confidence" in record:
            row["confidence"] = _number(record["confidence"], f"{prefix} confidence", probability=True)
        if "probabilities" in record:
            probabilities = record["probabilities"]
            if not isinstance(probabilities, Mapping):
                raise ValueError(f"{prefix} probabilities must be a label-to-probability mapping.")
            row["probabilities"] = {}
            for label, value in probabilities.items():
                _labels((label,), f"{prefix} probabilities")
                row["probabilities"][label] = _number(value, f"{prefix} probability for {label}", probability=True)
        validated.append(row)
    return validated


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _aggregate(records, known_labels, labels):
    samples = len(records)
    accepted = sum(not row["abstained"] for row in records)
    correct = sum(not row["abstained"] and row["expected"] == row["predicted"] for row in records)
    activity_metrics = {}
    for label in sorted(labels):
        tp = sum(label in row["expected"] and label in row["predicted"] for row in records)
        fp = sum(label not in row["expected"] and label in row["predicted"] for row in records)
        fn = sum(label in row["expected"] and label not in row["predicted"] for row in records)
        tn = samples - tp - fp - fn
        activity_metrics[label] = {
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
            "f1": _ratio(2 * tp, 2 * tp + fp + fn),
            "support": tp + fn,
        }
    tp = sum(item["tp"] for item in activity_metrics.values())
    fp = sum(item["fp"] for item in activity_metrics.values())
    fn = sum(item["fn"] for item in activity_metrics.values())
    scored_f1 = [item["f1"] for item in activity_metrics.values() if item["f1"] is not None]
    expected_labels = set().union(*(row["expected"] for row in records)) if records else set()
    complete_probabilities = bool(samples and known_labels) and all(
        known_labels.issubset(row.get("probabilities", {})) for row in records)
    brier = None
    if complete_probabilities:
        brier = sum((row["probabilities"][label] - int(label in row["expected"])) ** 2
                    for row in records for label in known_labels) / (samples * len(known_labels))
    return {
        "labelled_samples": samples,
        "exact_match_accuracy": _ratio(correct, samples),
        "micro_f1": _ratio(2 * tp, 2 * tp + fp + fn),
        "macro_f1": sum(scored_f1) / len(scored_f1) if scored_f1 else None,
        "coverage": _ratio(accepted, samples),
        "selective_accuracy": _ratio(correct, accepted),
        "per_activity": activity_metrics,
        "test_houses": len({row["house_id"] for row in records}),
        "test_sessions": len({(row["house_id"], row["run_id"]) for row in records}),
        "unsupported_labels": sorted(expected_labels - known_labels),
        "brier_score": brier,
        "brier_scope": ("Mean squared probability error across all labelled samples and known activity "
                        "labels only; unsupported labels are excluded. Available only when every sample "
                        "provides probabilities for every known label."),
    }


def summarize_predictions(records, known_labels):
    """Summarize labelled predictions without hiding unknown labels or abstentions.

    Empty datasets have no accuracy, F1, coverage or Brier score. Classes with no
    true or predicted positives do not contribute to macro F1. Unknown target
    activities contribute false negatives exactly like known activities.

    ``records`` and their collections are never modified. Sessions are counted
    by ``(house_id, run_id)`` so independently named runs in different houses
    remain distinct.
    """
    known = _labels(known_labels, "known_labels")
    rows = _validate_records(records)
    labels = set(known)
    for row in rows:
        labels.update(row["expected"])
        labels.update(row["predicted"])
    result = _aggregate(rows, known, labels)
    by_house = {}
    for row in rows:
        by_house.setdefault(row["house_id"], []).append(row)
    result["per_house"] = {house: _aggregate(house_rows, known, labels)
                           for house, house_rows in sorted(by_house.items())}
    return result
