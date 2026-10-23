"""Source-only Random Forest fitting and portable JSON probability prediction.

Fitted estimators are never pickled. Prediction uses only exported tree arrays
and optional sigmoid parameters learned from a separate validation partition.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from numbers import Integral, Real

import numpy as np


_FOREST_PARAMETERS = {"n_estimators": 128, "min_samples_leaf": 2, "max_depth": 12,
                      "n_jobs": 1, "class_weight": "balanced_subsample"}
_LOGIT_EPSILON = 1e-6


def _names(value, field):
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise ValueError(f"{field} must be a collection of names.")
    names = list(value)
    if not names or any(not isinstance(name, str) or not name.strip() or name != name.strip() for name in names):
        raise ValueError(f"{field} must contain non-empty names without surrounding whitespace.")
    if len(names) != len(set(names)):
        raise ValueError(f"{field} must contain unique names.")
    return names


def _matrix(value, columns, field, *, allow_empty):
    try:
        array = np.asarray(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a rectangular numeric matrix.") from error
    if array.ndim == 1 and array.size == 0 and allow_empty:
        array = np.empty((0, columns), dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != columns:
        raise ValueError(f"{field} must have shape (samples, {columns}).")
    if not allow_empty and not len(array):
        raise ValueError(f"{field} must contain at least one sample.")
    if array.dtype.kind not in "biuf":
        raise ValueError(f"{field} must contain numeric values.")
    with np.errstate(over="ignore", invalid="ignore"):
        converted = array.astype(np.float32)
    if not np.isfinite(converted).all():
        raise ValueError(f"{field} must contain finite values within the float32 range.")
    return converted


def _targets(value, rows, columns, field):
    try:
        array = np.asarray(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a rectangular binary matrix.") from error
    if array.ndim == 1 and ((columns == 1 and len(array) == rows) or (rows == 0 and not array.size)):
        array = array.reshape((rows, columns))
    if array.ndim != 2 or array.shape != (rows, columns):
        raise ValueError(f"{field} must have shape ({rows}, {columns}).")
    if array.dtype.kind not in "biuf" or not np.isfinite(array).all() or not ((array == 0) | (array == 1)).all():
        raise ValueError(f"{field} must contain only binary 0/1 targets.")
    return array.astype(np.int8)


def _logit(probabilities):
    clipped = np.clip(probabilities, _LOGIT_EPSILON, 1 - _LOGIT_EPSILON)
    return np.log(clipped / (1 - clipped))


def _sigmoid(value):
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1 + exponential)


def _export_forest(forest, labels, feature_names, seed, sklearn_version):
    classes = forest.classes_ if len(labels) > 1 else [forest.classes_]
    class_mapping = [[int(item) for item in output] for output in classes]
    trees = []
    for estimator in forest.estimators_:
        tree = estimator.tree_
        values = []
        for node in tree.value:
            outputs = []
            for index, mapping in enumerate(class_mapping):
                raw = np.asarray(node[index, :len(mapping)], dtype=float)
                total = float(raw.sum())
                if total <= 0:
                    raise ValueError("The fitted tree contains a node without class mass.")
                outputs.append((raw / total).tolist())
            values.append(outputs)
        trees.append({"children_left": tree.children_left.tolist(),
                      "children_right": tree.children_right.tolist(),
                      "feature": tree.feature.tolist(), "threshold": tree.threshold.tolist(),
                      "value": values})
    return {"schema_version": 1, "classifier": "RandomForestClassifier", "labels": list(labels),
            "feature_names": list(feature_names), "class_mapping": class_mapping, "trees": trees,
            "n_trees": len(trees), "seed": seed, "sklearn_version": sklearn_version,
            "forest_parameters": {**_FOREST_PARAMETERS, "random_state": seed}, "calibrators": {}}


def _integer(value, field):
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{field} must be an integer.")
    return int(value)


def _finite(value, field):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field} must be a finite number.")
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise ValueError(f"{field} must be a finite number.") from error
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number.")
    return number


def _compiled_trees(model, labels, feature_names):
    mappings = model.get("class_mapping")
    if not isinstance(mappings, list) or len(mappings) != len(labels):
        raise ValueError("Model class_mapping does not match its labels.")
    positive_indices = []
    for mapping in mappings:
        if not isinstance(mapping, list) or not mapping:
            raise ValueError("Every output needs a binary class mapping.")
        parsed = [_integer(item, "class mapping") for item in mapping]
        if any(item not in (0, 1) for item in parsed) or len(parsed) != len(set(parsed)):
            raise ValueError("Class mappings must contain distinct binary class values.")
        positive_indices.append(parsed.index(1) if 1 in parsed else None)
    trees = model.get("trees")
    if not isinstance(trees, list) or not trees:
        raise ValueError("Model must contain at least one tree.")
    if _integer(model.get("n_trees"), "n_trees") != len(trees):
        raise ValueError("Model n_trees does not match its tree arrays.")
    compiled = []
    for tree in trees:
        if not isinstance(tree, Mapping):
            raise ValueError("Model trees must be mappings of arrays.")
        keys = ("children_left", "children_right", "feature", "threshold", "value")
        if any(not isinstance(tree.get(key), list) for key in keys):
            raise ValueError("Each model tree needs children, feature, threshold and value arrays.")
        size = len(tree["children_left"])
        if not size or any(len(tree[key]) != size for key in keys):
            raise ValueError("Tree arrays must have the same non-zero length.")
        left = [_integer(item, "left child") for item in tree["children_left"]]
        right = [_integer(item, "right child") for item in tree["children_right"]]
        features = [_integer(item, "split feature") for item in tree["feature"]]
        thresholds = [_finite(item, "split threshold") for item in tree["threshold"]]
        positive = np.zeros((size, len(labels)), dtype=float)
        for node in range(size):
            if left[node] == -1 and right[node] == -1:
                pass
            elif not (0 <= left[node] < size and 0 <= right[node] < size
                      and 0 <= features[node] < len(feature_names)):
                raise ValueError("Tree child or split feature index is out of bounds.")
            outputs = tree["value"][node]
            if not isinstance(outputs, list) or len(outputs) != len(labels):
                raise ValueError("Tree node values do not match output labels.")
            for output, (values, mapping, positive_index) in enumerate(zip(outputs, mappings, positive_indices)):
                if not isinstance(values, list) or len(values) != len(mapping):
                    raise ValueError("Tree class probabilities do not match class_mapping.")
                parsed = [_finite(item, "tree class probability") for item in values]
                if any(item < 0 or item > 1 for item in parsed) or not math.isclose(sum(parsed), 1, abs_tol=1e-8):
                    raise ValueError("Tree class probabilities must be normalized to one.")
                if positive_index is not None:
                    positive[node, output] = parsed[positive_index]
        # A valid decision tree cannot revisit a node on any path. Validate all
        # nodes, including unreachable ones, before attempting prediction.
        colours = [0] * size
        for start in range(size):
            if colours[start]:
                continue
            stack = [(start, False)]
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    colours[node] = 2
                elif colours[node] == 1:
                    raise ValueError("Tree child arrays contain a cycle.")
                elif colours[node] == 0:
                    colours[node] = 1
                    stack.append((node, True))
                    if left[node] != -1:
                        stack.extend(((right[node], False), (left[node], False)))
        compiled.append((left, right, features, thresholds, positive))
    return compiled


def _raw_probabilities(model, matrix, labels, feature_names):
    trees = _compiled_trees(model, labels, feature_names)
    result = np.zeros((len(matrix), len(labels)), dtype=float)
    for left, right, features, thresholds, positive in trees:
        for index, row in enumerate(matrix):
            node = 0
            steps = 0
            while left[node] != -1:
                node = left[node] if float(row[features[node]]) <= thresholds[node] else right[node]
                steps += 1
                if steps >= len(left):
                    raise ValueError("Tree traversal exceeded its node count.")
            result[index] += positive[node]
    return result / len(trees)


def fit_classifier(train_x, train_y, validation_x, validation_y, *, labels, feature_names, seed=42):
    """Fit trees on training rows; fit eligible sigmoid calibrators on validation rows only."""
    names = _names(labels, "labels")
    features = _names(feature_names, "feature_names")
    seed = _integer(seed, "seed")
    if not 0 <= seed < 2 ** 32:
        raise ValueError("seed must be from 0 to 4294967295.")
    training = _matrix(train_x, len(features), "train_x", allow_empty=False)
    training_targets = _targets(train_y, len(training), len(names), "train_y")
    validation = _matrix(validation_x, len(features), "validation_x", allow_empty=True)
    validation_targets = _targets(validation_y, len(validation), len(names), "validation_y")

    import sklearn
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression

    forest = RandomForestClassifier(**_FOREST_PARAMETERS, random_state=seed)
    forest.fit(training, training_targets[:, 0] if len(names) == 1 else training_targets)
    model = _export_forest(forest, names, features, seed, sklearn.__version__)
    model["training_samples"] = len(training)
    model["validation_samples"] = len(validation)
    model["calibration_policy"] = ("Sigmoid on clipped forest-vote logits; fitted only on source validation "
                                   "rows with at least five positive and five negative examples, and both "
                                   "classes present in training. Otherwise explicitly uncalibrated.")
    votes = _raw_probabilities(model, validation, names, features)
    for index, label in enumerate(names):
        positive = int(validation_targets[:, index].sum())
        negative = len(validation) - positive
        both_training_classes = len(np.unique(training_targets[:, index])) == 2
        calibrator = {"method": "uncalibrated", "positive_count": positive, "negative_count": negative,
                      "coefficient": None, "intercept": None, "reason": ""}
        if not both_training_classes:
            calibrator["reason"] = "Training contains only one class for this activity."
        elif positive < 5 or negative < 5:
            calibrator["reason"] = "Source validation needs at least five positive and five negative examples."
        else:
            regression = LogisticRegression(C=1, solver="lbfgs", max_iter=200, random_state=seed)
            regression.fit(_logit(votes[:, index]).reshape(-1, 1), validation_targets[:, index])
            calibrator.update(method="sigmoid", coefficient=float(regression.coef_[0, 0]),
                              intercept=float(regression.intercept_[0]), reason="")
        model["calibrators"][label] = calibrator
    return model


def predict_probabilities(model, X):
    """Predict from portable JSON tree data; no estimator loading or fitting occurs."""
    if not isinstance(model, Mapping) or model.get("schema_version") != 1:
        raise ValueError("Unsupported classifier model schema.")
    labels = _names(model.get("labels"), "model labels")
    features = _names(model.get("feature_names"), "model feature_names")
    matrix = _matrix(X, len(features), "X", allow_empty=True)
    probabilities = _raw_probabilities(model, matrix, labels, features)
    calibrators = model.get("calibrators")
    if not isinstance(calibrators, Mapping) or set(calibrators) != set(labels):
        raise ValueError("Model calibrators do not match its labels.")
    for index, label in enumerate(labels):
        calibrator = calibrators[label]
        if not isinstance(calibrator, Mapping) or calibrator.get("method") not in {"sigmoid", "uncalibrated"}:
            raise ValueError("Unknown classifier calibration method.")
        if calibrator["method"] == "sigmoid":
            coefficient = _finite(calibrator.get("coefficient"), "calibrator coefficient")
            intercept = _finite(calibrator.get("intercept"), "calibrator intercept")
            logits = _logit(probabilities[:, index])
            probabilities[:, index] = [_sigmoid(coefficient * float(value) + intercept) for value in logits]
    return [{label: float(probabilities[row, index]) for index, label in enumerate(labels)}
            for row in range(len(probabilities))]
