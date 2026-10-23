"""Supervised category-based HAR with source-only fitting and held-out tests.

Models contain portable JSON trees. Category names define measurements, not
activity rules; activity classes come exclusively from labelled source examples.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
from datetime import datetime, timezone
from uuid import uuid4

from har.catalogue import validate_catalogue, validate_catalogue_compatibility
from har.features import FeatureEncoder, encode_session
from har.storage import safe_identifier, validate_session


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _observation_fingerprint(session):
    return _fingerprint([{key: value for key, value in frame.items() if key != "labels"}
                         for frame in session["frames"]])


def _check_dictionary(session, catalogue):
    validate_catalogue_compatibility(catalogue, session["catalogue"])
    # A recording freezes resolved categories, so later type-default changes
    # cannot rewrite the observations used for training or evaluation.
    for frame in session["frames"]:
        for kind in ("device", "sensor"):
            for obj in frame[f"{kind}s"]:
                categories = obj.get("categories")
                if not isinstance(categories, list) or any(not isinstance(name, str) for name in categories):
                    raise ValueError("Recorded objects need resolved category lists.")
                if any(name not in session["catalogue"][f"{kind}_categories"] for name in categories):
                    raise ValueError("A recorded object uses a category absent from its frozen dictionary.")


def _export_forest(forest, label_count):
    classes = forest.classes_ if label_count > 1 else [forest.classes_]
    result = []
    for estimator in forest.estimators_:
        tree = estimator.tree_
        positive = []
        for node in tree.value:
            probabilities = []
            for output, labels in enumerate(classes):
                values = node[output, :len(labels)]
                total = float(values.sum())
                index = list(labels).index(1)
                probabilities.append(float(values[index]) / total if total else 0.0)
            positive.append(probabilities)
        result.append({"left": tree.children_left.tolist(), "right": tree.children_right.tolist(),
                       "feature": tree.feature.tolist(), "threshold": tree.threshold.tolist(), "positive": positive})
    return result


def validate_model(model):
    try:
        return _validate_model(model)
    except (TypeError, KeyError, AttributeError, IndexError) as error:
        raise ValueError("Invalid learned model structure.") from error


def _validate_model(model):
    if not isinstance(model, dict) or model.get("schema_version") != 1 or model.get("kind") != "category_random_forest":
        raise ValueError("This is not a learned category-based HAR model.")
    safe_identifier(model.get("model_id"))
    safe_identifier(model.get("source_house_id"))
    catalogue = validate_catalogue(model.get("catalogue"))
    labels = model.get("labels")
    if not isinstance(labels, list) or not labels or any(not isinstance(label, str) or not label.strip() for label in labels):
        raise ValueError("The model needs activity labels learned from source examples.")
    if len(set(labels)) != len(labels):
        raise ValueError("Model activity labels must be unique.")
    used_sessions = set()
    for key in ("source_training_sessions", "source_validation_sessions", "source_test_sessions"):
        identifiers = model.get(key)
        if not isinstance(identifiers, list) or not identifiers:
            raise ValueError("The model must identify its separate source training, validation and test sessions.")
        for identifier in identifiers:
            safe_identifier(identifier)
            if identifier in used_sessions:
                raise ValueError("Model source partitions overlap.")
            used_sessions.add(identifier)
    if set(model.get("source_session_fingerprints", {})) != used_sessions:
        raise ValueError("The model is missing its frozen source-session fingerprints.")
    for key in ("source_simulation_ids", "source_observation_fingerprints"):
        if not isinstance(model.get(key), list) or not model[key] or any(not isinstance(value, str) for value in model[key]):
            raise ValueError("The model must preserve independent source-session provenance.")
    encoder = FeatureEncoder(catalogue, model.get("window_seconds"))
    if model.get("feature_names") != encoder.feature_names:
        raise ValueError("Model input schema does not match its category dictionary.")
    for field in ("threshold", "minimum_certainty"):
        value = model.get(field)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"Invalid model {field}.")
    trees = model.get("trees")
    if not isinstance(trees, list) or not trees:
        raise ValueError("The model has no trained trees.")
    for tree in trees:
        if not isinstance(tree, dict) or any(not isinstance(tree.get(key), list)
                                             for key in ("left", "right", "feature", "threshold", "positive")):
            raise ValueError("Invalid trained tree arrays.")
        size = len(tree.get("left", []))
        if not size or any(len(tree.get(key, [])) != size for key in ("right", "feature", "threshold", "positive")):
            raise ValueError("Invalid trained tree arrays.")
        for index in range(size):
            left, right = tree["left"][index], tree["right"][index]
            if any(not isinstance(value, int) or isinstance(value, bool)
                   for value in (left, right, tree["feature"][index])):
                raise ValueError("Trained tree indices must be integers.")
            if (left == -1) != (right == -1):
                raise ValueError("Invalid trained tree children.")
            if left != -1 and (not 0 <= left < size or not 0 <= right < size
                               or not 0 <= tree["feature"][index] < len(encoder.feature_names)):
                raise ValueError("Invalid trained tree input or child index.")
            values = tree["positive"][index]
            if not isinstance(values, list) or len(values) != len(labels) or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
                raise ValueError("Invalid trained probabilities.")
            if not isinstance(tree["threshold"][index], (int, float)) or not math.isfinite(tree["threshold"][index]):
                raise ValueError("Invalid tree threshold.")
        pending, reached = [0], set()
        while pending:
            index = pending.pop()
            if index in reached:
                raise ValueError("Trained trees must not have cycles or shared children.")
            reached.add(index)
            if tree["left"][index] != -1:
                pending.extend((tree["left"][index], tree["right"][index]))
        if len(reached) != size:
            raise ValueError("Trained tree has unreachable nodes.")
    return model


def predict_vector(model, vector):
    if len(vector) != len(model["feature_names"]) or any(not math.isfinite(value) for value in vector):
        raise ValueError("Observation features do not match the trained model.")
    # sklearn fits and traverses trees using float32 inputs; use the same
    # conversion for the portable JSON predictor, including near split points.
    import numpy as np
    with np.errstate(over="ignore"):
        vector = np.asarray(vector, dtype=np.float32).tolist()
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("Observation features exceed the model's numeric range.")
    probabilities = [0.0] * len(model["labels"])
    for tree in model["trees"]:
        index, visited = 0, 0
        while tree["left"][index] != -1:
            visited += 1
            if visited > len(tree["left"]):
                raise ValueError("Invalid cycle in trained tree.")
            feature = tree["feature"][index]
            index = tree["left"][index] if vector[feature] <= tree["threshold"][index] else tree["right"][index]
        for column, value in enumerate(tree["positive"][index]):
            probabilities[column] += value / len(model["trees"])
    scores = dict(zip(model["labels"], probabilities))
    confidence = min(max(value, 1 - value) for value in probabilities)
    abstained = confidence + 1e-12 < model["minimum_certainty"]
    activities = [label for label, probability in scores.items() if probability >= model["threshold"]]
    return {"activities": [] if abstained else activities, "probabilities": scores, "confidence": confidence,
            "abstained": abstained, "status": "uncertain" if abstained else "ready",
            "message": "Model uncertain." if abstained else "Trained model prediction (uncalibrated forest votes)."}


def _score(model, sessions):
    expected, predicted, records = [], [], []
    for session in sessions:
        _check_dictionary(session, model["catalogue"])
        names, rows = encode_session(session, model["catalogue"], model["window_seconds"])
        if names != model["feature_names"]:
            raise ValueError("Session feature schema differs from the trained model.")
        for vector, labels in rows:
            result = predict_vector(model, vector)
            expected.append(set(labels))
            predicted.append(set(result["activities"]))
            records.append({"session_id": session["session_id"], "house_id": session["house_id"],
                            "expected": labels, **result})
    classes = sorted(set(model["labels"]) | {label for labels in expected for label in labels})
    per_activity = {}
    tp_total = fp_total = fn_total = 0
    for label in classes:
        tp = sum(label in truth and label in guess for truth, guess in zip(expected, predicted))
        fp = sum(label not in truth and label in guess for truth, guess in zip(expected, predicted))
        fn = sum(label in truth and label not in guess for truth, guess in zip(expected, predicted))
        precision, recall = tp / (tp + fp) if tp + fp else 0.0, tp / (tp + fn) if tp + fn else 0.0
        per_activity[label] = {"precision": precision, "recall": recall,
                               "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
                               "support": tp + fn, "tp": tp, "fp": fp, "fn": fn}
        tp_total, fp_total, fn_total = tp_total + tp, fp_total + fp, fn_total + fn
    denominator = 2 * tp_total + fp_total + fn_total
    return {"labelled_samples": len(expected),
            "exact_match_accuracy": sum(truth == guess and not row["abstained"]
                                        for truth, guess, row in zip(expected, predicted, records)) / len(expected) if expected else None,
            "micro_f1": 2 * tp_total / denominator if expected and denominator else None,
            "macro_f1": sum(row["f1"] for row in per_activity.values()) / len(classes) if expected and classes else None,
            "per_activity": per_activity, "unknown_activities": sorted(set(classes) - set(model["labels"])),
            "coverage": sum(not row["abstained"] for row in records) / len(records) if records else None,
            "predictions": records}


def train_source(sessions, source_house_id, *, window_seconds=600, seed=42):
    """Fit on source sessions; target observations and labels are never inspected."""
    safe_identifier(source_house_id)
    source = [copy.deepcopy(session) for session in sessions
              if session.get("house_id") == source_house_id and session.get("partition") == "source"]
    if len(source) < 3:
        raise ValueError("Record at least 3 independent source sessions, resetting the simulation between sessions.")
    identities, traces = set(), set()
    for session in source:
        validate_session(session)
        identity = session.get("simulation_session_id", session["session_id"])
        if identity in identities:
            raise ValueError("Several recordings belong to one simulation session. Reset between independent source sessions.")
        identities.add(identity)
        trace = _observation_fingerprint(session)
        if trace in traces:
            raise ValueError("Copied observation traces are not independent source sessions.")
        traces.add(trace)
    source.sort(key=lambda session: session["session_id"])
    random.Random(seed).shuffle(source)
    held_out = max(1, len(source) // 5)
    training, validation, testing = source[2 * held_out:], source[:held_out], source[held_out:2 * held_out]
    catalogue = validate_catalogue(training[0]["catalogue"])
    names = FeatureEncoder(catalogue, window_seconds).feature_names
    rows = []
    for session in training:
        _check_dictionary(session, catalogue)
        _, encoded = encode_session(session, catalogue, window_seconds)
        rows.extend(encoded)
    labels = sorted({label for _, truth in rows for label in truth})
    if not labels:
        raise ValueError("Training sessions need labelled positive activities as well as explicit no-activity examples.")
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
    x = np.asarray([vector for vector, _ in rows], dtype=np.float32)
    y = np.asarray([[int(label in truth) for label in labels] for _, truth in rows], dtype=np.int8)
    if any(len(set(y[:, column])) < 2 for column in range(len(labels))):
        raise ValueError("Each training activity needs positive and negative examples. Label idle frames as No activity.")
    forest = RandomForestClassifier(n_estimators=128, max_depth=12, min_samples_leaf=2,
                                    class_weight="balanced_subsample", random_state=seed, n_jobs=1)
    forest.fit(x, y[:, 0] if len(labels) == 1 else y)
    model = {"schema_version": 1, "kind": "category_random_forest", "model_id": uuid4().hex,
             "created_at": datetime.now(timezone.utc).isoformat(), "source_house_id": source_house_id,
             "labels": labels, "catalogue": catalogue, "feature_names": names,
             "window_seconds": float(window_seconds), "threshold": .5, "minimum_certainty": .55,
             "trees": _export_forest(forest, len(labels)),
             "source_training_sessions": [session["session_id"] for session in training],
             "source_validation_sessions": [session["session_id"] for session in validation],
             "source_test_sessions": [session["session_id"] for session in testing],
             "source_session_fingerprints": {session["session_id"]: _fingerprint(session) for session in source},
             "source_simulation_ids": sorted(identities),
             "source_observation_fingerprints": sorted(traces),
             "training_provenance": sorted({session.get("provenance", "human-labelled") for session in training}),
             "seed": seed, "training_samples": len(rows),
             "confidence_note": "Forest vote probabilities are uncalibrated, including on another house."}
    validate_model(model)
    model["source_validation_metrics"] = _score(model, validation)
    model["source_test_metrics"] = _score(model, testing)
    return model


def evaluate_model(model, sessions):
    validate_model(model)
    source_id = model["source_house_id"]
    target, source_test, ignored = [], [], []
    source_simulations = set(model.get("source_simulation_ids", []))
    source_traces = set(model.get("source_observation_fingerprints", []))
    for session in sessions:
        if session.get("house_id") == source_id:
            source_simulations.add(session.get("simulation_session_id", session.get("session_id")))
    seen = set()
    for session in sessions:
        session_id = session.get("session_id")
        if session_id in seen:
            raise ValueError("A session was supplied more than once.")
        seen.add(session_id)
        if session.get("house_id") == source_id:
            ignored.append(session_id)
            if session_id in model["source_test_sessions"]:
                if _fingerprint(session) != model["source_session_fingerprints"].get(session_id):
                    raise ValueError("A held-out source session changed after training.")
                validate_session(session)
                source_test.append(session)
        elif session.get("partition") == "target":
            validate_session(session)
            if session.get("simulation_session_id", session_id) in source_simulations:
                raise ValueError("A target session shares a source simulation identity. Record it independently.")
            if _observation_fingerprint(session) in source_traces:
                raise ValueError("A target session copies source observations. Record it independently.")
            target.append(session)
    report = _score(model, target)
    report.update({"model_id": model["model_id"], "source_house_id": source_id,
                   "test_houses": len({session["house_id"] for session in target}),
                   "target_sessions": len(target), "excluded_source_sessions": ignored,
                   "target_independent_sessions": len({session.get("simulation_session_id", session["session_id"]) for session in target}),
                   "scope": "synthetic" if ("scripted-synthetic" in model.get("training_provenance", [])
                                              or any(session.get("provenance") == "scripted-synthetic" for session in target)) else "human-labelled",
                   "source_test": _score(model, source_test),
                   "note": "Only labelled frames are scored; unlabelled frames supply temporal context. Target data is never used to train."})
    return report
