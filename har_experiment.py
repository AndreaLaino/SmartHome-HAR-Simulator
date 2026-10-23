"""Controlled, reproducible source-house/target-house HAR experiments.

Training, sigmoid calibration and testing use separate complete sessions.
Target labels are used only for scoring. Frozen JSON inputs and JSON forest
models make saved experiments replayable without reading the live library.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import html
import json
import math
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

import recognition_library as library
from activity_recognition import recognition_settings
from har_features import extract_feature_rows
from har_metrics import summarize_predictions


METHODS = {
    "reference_rules": "Reference-transfer rules",
    "direct_rules": "Direct-property rules",
    "semantic_rf": "Random Forest on matched semantic roles",
    "raw_rf": "Random Forest on source sensor identities",
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _parameters(seed, window_minutes, minimum_probability, strict_cooking, use_time_of_day):
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 32:
        raise ValueError("Random seed must be an integer from 0 to 4294967295.")
    if not isinstance(strict_cooking, bool) or not isinstance(use_time_of_day, bool):
        raise ValueError("Cooking context and time-of-day options must be booleans.")
    window_minutes, minimum_probability = float(window_minutes), float(minimum_probability)
    if not math.isfinite(window_minutes) or window_minutes <= 0:
        raise ValueError("Window length must be positive and finite.")
    if not math.isfinite(minimum_probability) or not 0 <= minimum_probability <= 1:
        raise ValueError("Minimum decision certainty must be between 0 and 1.")
    return {"seed": seed, "window_minutes": window_minutes, "minimum_probability": minimum_probability,
            "strict_cooking": strict_cooking, "use_time_of_day": use_time_of_day}


def split_sessions(datasets, source_house_id, *, seed=42):
    """Split source sessions 60/20/20, reserve every target test house intact."""
    if not isinstance(source_house_id, str) or not source_house_id:
        raise ValueError("Choose a source/reference house.")
    seen, sessions, observation_traces, source, source_test, target, ignored = set(), set(), set(), [], [], [], []
    for dataset in datasets:
        run_id, house_id = dataset.get("run_id"), dataset.get("house_id")
        if not isinstance(run_id, str) or not run_id or run_id != run_id.strip() or run_id in seen:
            raise ValueError("Every independent session needs a unique nonempty run ID.")
        seen.add(run_id)
        if not isinstance(house_id, str) or not house_id or house_id != house_id.strip() or dataset.get("house", {}).get("house_id") != house_id:
            raise ValueError(f"Session {run_id} has an inconsistent house identity.")
        session_id = dataset.get("session_id", run_id)
        if not isinstance(session_id, str) or not session_id or session_id != session_id.strip():
            raise ValueError("Session identities must be nonempty strings.")
        session_key = (house_id, session_id)
        if session_key in sessions:
            raise ValueError("Multiple runs come from the same simulation session. Reset the simulation between independent sessions; do not split one session into train and test runs.")
        sessions.add(session_key)
        frames = dataset.get("frames")
        if not isinstance(frames, list) or not frames:
            raise ValueError(f"Session {run_id} has no observation frames.")
        labelled = 0
        for frame in frames:
            if "expected_activities" not in frame:
                continue
            labels = frame["expected_activities"]
            if not isinstance(labels, list) or not all(isinstance(label, str) and label.strip() and label == label.strip() for label in labels):
                raise ValueError(f"Session {run_id} has invalid ground-truth labels.")
            labelled += 1
        if not labelled:
            raise ValueError(f"Session {run_id} has no human/scripted labels; unlabelled context alone cannot be scored.")
        partition = dataset.get("partition")
        if house_id == source_house_id:
            observations = [{key: frame.get(key) for key in ("time_minutes", "sensor_states", "device_states", "door_states", "avatar_position", "person_inside")}
                            for frame in sorted(frames, key=lambda frame: frame["time_minutes"])]
            digest = fingerprint(observations)
            if digest in observation_traces:
                raise ValueError("Two source sessions contain identical observation traces. Record independent, varied sessions instead of copying a run.")
            observation_traces.add(digest)
        if house_id == source_house_id and partition == "reference":
            source.append(dataset)
        elif house_id == source_house_id and partition == "test":
            source_test.append(dataset)
        elif house_id != source_house_id and partition == "test":
            target.append(dataset)
        else:
            ignored.append(run_id)
    if len(source) < 5:
        raise ValueError("Record at least 5 independent Reference sessions in the source house for training, validation and testing.")
    if not target:
        raise ValueError("Record held-out test sessions in at least one different target house.")
    source.sort(key=lambda item: item["run_id"])
    random.Random(seed).shuffle(source)
    heldout = max(1, len(source) // 5)
    split = {"train": source[heldout * 2:], "validation": source[:heldout],
             "source_test": source[heldout:heldout * 2] + sorted(source_test, key=lambda item: item["run_id"]),
             "target_test": sorted(target, key=lambda item: item["run_id"]), "ignored_runs": ignored}
    train_labels = {label for dataset in split["train"] for frame in dataset["frames"]
                    for label in frame.get("expected_activities", [])}
    source_labels = {label for dataset in source for frame in dataset["frames"]
                     for label in frame.get("expected_activities", [])}
    if not train_labels:
        raise ValueError("Source training sessions need at least one positive activity label as well as observations.")
    if source_labels - train_labels:
        raise ValueError("The source training split has no examples of: " + ", ".join(sorted(source_labels - train_labels))
                         + ". Record more independent source sessions or choose another split seed.")
    return split


def _raw_schema(training):
    identities = {(kind, obj["name"]) for dataset in training for kind, key in (("sensor", "sensors"), ("device", "devices"))
                  for obj in dataset["house"].get(key, [])}
    return [{"kind": kind, "name": name} for kind, name in sorted(identities)]


def _samples(dataset, extracted):
    labels = {float(frame["time_minutes"]): sorted(set(frame["expected_activities"]))
              for frame in dataset["frames"] if "expected_activities" in frame}
    return [{**row, "expected": labels[row["time_minutes"]], "run_id": dataset["run_id"], "house_id": dataset["house_id"]}
            for row in extracted["rows"] if row["time_minutes"] in labels]


def _learned_records(samples, model, minimum_probability):
    from har_classifier import predict_probabilities
    probabilities = predict_probabilities(model, [sample["features"] for sample in samples])
    records = []
    for sample, values in zip(samples, probabilities):
        certainty = min(max(value, 1 - value) for value in values.values())
        abstained = certainty + 1e-12 < minimum_probability
        predicted = sorted(label for label, value in values.items() if value >= .5) if not abstained else []
        records.append({"house_id": sample["house_id"], "run_id": sample["run_id"], "time_minutes": sample["time_minutes"],
                        "expected": sample["expected"], "predicted": predicted, "confidence": certainty,
                        "probabilities": values, "abstained": abstained})
    return records


def _rule_records(samples, minimum_probability):
    return [{"house_id": sample["house_id"], "run_id": sample["run_id"], "time_minutes": sample["time_minutes"],
             "expected": sample["expected"], "predicted": sample["rule_prediction"],
             "confidence": sample["rule_confidence"],
             "abstained": sample["abstained"] or sample["rule_confidence"] + 1e-12 < minimum_probability,
             "reference_matches": sample["reference_matches"]} for sample in samples]


def _output_directory(output_dir):
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = library.LIBRARY_DIR / "reports" / "experiments" / f"{stamp}_{uuid.uuid4().hex[:8]}"
    directory = Path(output_dir).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Choose an empty results directory; an existing experiment will not be overwritten.")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _inference_code_fingerprint():
    import numpy
    directory = Path(__file__).resolve().parent
    files = ("har_experiment.py", "har_metrics.py", "har_features.py", "har_classifier.py", "activity_recognition.py", "reference_transfer.py",
             "device_properties.py", "lighting.py", "room_recognition.py", "utils.py", "recognition_library.py")
    return fingerprint({"sources": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in files},
                        "numpy_version": numpy.__version__})


def _write_reports(directory, report, predictions):
    def percent(value):
        return f"{value:.1%}" if value is not None else "N/A"
    lines = ["# Source-house / target-house recognition experiment", "",
             "**SCRIPTED SYNTHETIC DATA: these results do not measure human or real-world recognition accuracy.**"
             if report["scope"] == "synthetic" else "Evaluation against manually supplied activity labels.", "",
             f"Source house: {report['source_house_id']}",
             f"Sessions: {len(report['training_runs'])} train, {len(report['validation_runs'])} validation, "
             f"{len(report['source_test_runs'])} held-out source, {len(report['target_test_runs'])} target.", "",
             "| Method | Source exact | Source micro F1 | Target exact | Target micro F1 | Target macro F1 | Coverage |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    with (directory / "comparison.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["method", "split", "labelled_samples", "exact_match_accuracy", "micro_f1", "macro_f1", "coverage", "selective_accuracy"])
        for method in report["methods"].values():
            source, target = method["source"], method["target"]
            lines.append(f"| {method['label']} | {percent(source['exact_match_accuracy'])} | {percent(source['micro_f1'])} | "
                         f"{percent(target['exact_match_accuracy'])} | {percent(target['micro_f1'])} | "
                         f"{percent(target['macro_f1'])} | {percent(target['coverage'])} |")
            for split_name in ("source", "target"):
                metrics = method[split_name]
                writer.writerow([method["label"], split_name, *[metrics[key] for key in (
                    "labelled_samples", "exact_match_accuracy", "micro_f1", "macro_f1", "coverage", "selective_accuracy")]])
    lines.extend(["", "## Limits and interpretation", "", *[f"- {item}" for item in report["limitations"]], "",
                  f"Frozen model fingerprint: `{report['model_fingerprint']}`", "",
                  "Full predictions, independent labels, feature rows, model JSON and frozen inputs are saved beside this report."])
    markdown = "\n".join(lines) + "\n"
    (directory / "report.md").write_text(markdown, encoding="utf-8")
    table_rows = []
    for method in report["methods"].values():
        target = method["target"]
        cells = [method["label"], str(target["labelled_samples"]), percent(target["exact_match_accuracy"]),
                 percent(target["micro_f1"]), percent(target["macro_f1"]), percent(target["coverage"])]
        table_rows.append("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in cells) + "</tr>")
    page = ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<title>Source / target HAR experiment</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#18212b}"
            "table{border-collapse:collapse;width:100%}td,th{padding:12px;border:1px solid #cbd5e1;text-align:left}"
            "th{background:#eaf0f6}pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6}</style>"
            "<h1>Source / target activity recognition</h1>"
            f"<p><strong>{'Scripted synthetic benchmark. Results do not establish real-world accuracy.' if report['scope'] == 'synthetic' else 'Human-labelled held-out dataset evaluation.'}</strong></p>"
            "<table><thead><tr><th>Method</th><th>Target n</th><th>Exact set accuracy</th><th>Micro F1</th><th>Macro F1</th><th>Coverage</th></tr></thead>"
            "<tbody>" + "".join(table_rows) + "</tbody></table><h2>Protocol and interpretation</h2><pre>"
            + html.escape(markdown) + "</pre></html>")
    (directory / "report.html").write_text(page, encoding="utf-8")
    with (directory / "per_activity.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["method", "split", "activity", "tp", "fp", "fn", "tn", "precision", "recall", "f1", "support"])
        for method_id, method in report["methods"].items():
            for split_name in ("source", "target"):
                for activity, values in method[split_name]["per_activity"].items():
                    writer.writerow([method_id, split_name, activity, *[values[key] for key in ("tp", "fp", "fn", "tn", "precision", "recall", "f1", "support")]])
    library.atomic_json(directory / "predictions.json", predictions)
    library.atomic_json(directory / "report.json", report)


def run_experiment(datasets, config, source_house_id, *, output_dir=None, seed=42,
                   window_minutes=10.0, strict_cooking=True, use_time_of_day=False,
                   minimum_probability=.5, profile_library=None, scope="human-labelled",
                   ground_truth_provenance=None, _saved_models=None, _expected_features=None):
    """Fit on source training, calibrate on source validation, score disjoint tests."""
    parameters = _parameters(seed, window_minutes, minimum_probability, strict_cooking, use_time_of_day)
    if output_dir is not None:
        destination = Path(output_dir)
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("Choose an empty results directory; an existing experiment will not be overwritten.")
    datasets = copy.deepcopy(list(datasets))
    if any(dataset.get("scope") == "synthetic" for dataset in datasets):
        scope = "synthetic"
    profiles = copy.deepcopy(profile_library)
    settings = recognition_settings(copy.deepcopy(config))
    settings["require_kitchen_presence"] = strict_cooking
    settings["minimum_confidence"] = minimum_probability
    split = split_sessions(datasets, source_house_id, seed=seed)
    target_houses = {dataset["house_id"] for dataset in split["target_test"]}
    if any(binding["reference"].get("house_id") in target_houses for binding in settings["reference_bindings"].values()):
        raise ValueError("A reference association comes from a held-out target house. Use source-house references before claiming source-only transfer.")
    schema = _raw_schema(split["train"])
    labels = sorted({label for dataset in split["train"] for frame in dataset["frames"] for label in frame.get("expected_activities", [])})
    extraction_options = {"window_minutes": window_minutes, "use_time_of_day": use_time_of_day,
                          "profile_library": profiles, "strict_cooking": strict_cooking}
    feature_names, samples = {}, {name: {} for name in ("reference", "direct", "raw")}
    for mode in samples:
        for split_name in ("train", "validation", "source_test", "target_test"):
            # Only reference/raw representations train classifiers. The direct
            # baseline is a rule ablation, not an additional learned model.
            if mode == "direct" and split_name in {"train", "validation"}:
                continue
            rows = []
            for dataset in split[split_name]:
                extracted = extract_feature_rows(dataset, settings, mode=mode,
                                                 raw_schema=schema if mode == "raw" else None, **extraction_options)
                if mode in feature_names and feature_names[mode] != extracted["feature_names"]:
                    raise ValueError("Feature schema changed between houses or sessions.")
                feature_names[mode] = extracted["feature_names"]
                rows.extend(_samples(dataset, extracted))
            samples[mode][split_name] = rows
    feature_fingerprint = fingerprint({mode: {split_name: [
        {key: row[key] for key in ("run_id", "house_id", "time_minutes", "features")} for row in rows]
        for split_name, rows in parts.items()} for mode, parts in samples.items()})
    if _expected_features is not None and feature_fingerprint != _expected_features:
        raise ValueError("Feature extraction differs from the saved experiment. Its frozen feature fingerprint no longer matches.")
    from har_classifier import fit_classifier
    models = {}
    for method, mode in (("semantic_rf", "reference"), ("raw_rf", "raw")):
        if _saved_models is None:
            train, validation = samples[mode]["train"], samples[mode]["validation"]
            models[method] = fit_classifier(
                [row["features"] for row in train], [[int(label in row["expected"]) for label in labels] for row in train],
                [row["features"] for row in validation], [[int(label in row["expected"]) for label in labels] for row in validation],
                labels=labels, feature_names=feature_names[mode], seed=seed)
        else:
            models[method] = copy.deepcopy(_saved_models[method])
            if models[method]["labels"] != labels or models[method]["feature_names"] != feature_names[mode]:
                raise ValueError("Saved model label/feature schema does not match the frozen experiment.")
    methods, predictions = {}, {}
    for method_id, label in METHODS.items():
        predictions[method_id] = {}
        metrics = {}
        for split_name, report_name in (("source_test", "source"), ("target_test", "target")):
            mode = "direct" if method_id == "direct_rules" else "raw" if method_id == "raw_rf" else "reference"
            rows = samples[mode][split_name]
            records = (_learned_records(rows, models[method_id], minimum_probability) if method_id in models
                       else _rule_records(rows, minimum_probability))
            predictions[method_id][report_name] = records
            metrics[report_name] = summarize_predictions(records, labels)
        methods[method_id] = {"label": label, **metrics}
    limitations = [
        "Target labels are used only for scoring; source training, sigmoid calibration and final source tests use disjoint sessions.",
        "Overlapping windows within a session are correlated. Sample counts are frames; session and house counts define the independent groups.",
        "Frame-weighted metrics depend on sampling cadence. The demo balances class sample counts by sampling positive phases more densely than long idle intervals.",
        "Rule confidence is heuristic. RF votes are sigmoid-calibrated on source validation only when both classes have enough examples; target calibration is unverified.",
        "The RF positive-label threshold is fixed at 0.5. Minimum decision certainty can abstain; abstentions remain in the exact-accuracy denominator.",
        "Unknown target activities count as errors and are listed as unsupported; the classifier cannot learn a class absent from source training.",
        "The raw baseline retains source identities; the semantic classifier uses matched roles and past-only observations without names, labels or rule prediction scores as features.",
        "The direct-property ablation uses the same observations and context gates; its target capabilities may not contain the source activity-labelled properties.",
        "Full future activity duration and label-derived episode boundaries are excluded from online features.",
    ]
    if scope == "synthetic":
        limitations.insert(0, "Scripted synthetic observations and scenario-intent labels demonstrate controlled behavior only; these are not real-world or human-activity accuracy estimates.")
    if not strict_cooking:
        limitations.append("Kitchen-PIR cooking context is disabled. Scripted unattended-appliance negatives may therefore be recognized by the broader rule.")
    if any(not dataset.get("session_id") for dataset in datasets):
        limitations.append("Some legacy datasets have only run IDs, without independently recorded session identities. Their actual session independence must be checked separately.")
    overlap = sorted({obj["type"] for dataset in split["target_test"] for obj in dataset["house"].get("devices", [])}
                     & {binding["reference"]["type"] for binding in settings["reference_bindings"].values() if binding["kind"] == "device"})
    if overlap:
        limitations.append("Some target device types already have catalogue references: " + ", ".join(overlap) + ". These roles are not wholly unseen device-type transfers.")
    model_fingerprint = fingerprint({"models": models, "settings": settings, "profiles": profiles, "parameters": parameters, "raw_schema": schema})
    report = {"schema_version": 1, "scope": scope, "source_house_id": source_house_id,
              "created_at_utc": datetime.now(timezone.utc).isoformat(), "parameters": parameters,
              "training_runs": [item["run_id"] for item in split["train"]],
              "validation_runs": [item["run_id"] for item in split["validation"]],
              "source_test_runs": [item["run_id"] for item in split["source_test"]],
              "target_test_runs": [item["run_id"] for item in split["target_test"]],
              "ignored_runs": split["ignored_runs"], "known_labels": labels, "methods": methods,
              "model_fingerprint": model_fingerprint, "dataset_fingerprint": fingerprint(datasets),
              "feature_fingerprint": feature_fingerprint, "inference_code_fingerprint": _inference_code_fingerprint(),
              "ground_truth_provenance": ground_truth_provenance or (
                  "Includes scripted synthetic scenario-intent labels, not recognition predictions." if scope == "synthetic"
                  else "Human-supplied frame labels; not recognition predictions."),
              "limitations": limitations, "target_reference_type_overlap": overlap,
              "calibration": {method: model["calibrators"] for method, model in models.items()}}
    directory = _output_directory(output_dir)
    report["output_dir"] = str(directory)
    manifest = {"schema_version": 1, "datasets": datasets, "config": settings, "profile_library": profiles,
                "source_house_id": source_house_id, "parameters": parameters, "scope": scope,
                "ground_truth_provenance": report["ground_truth_provenance"], "model_fingerprint": model_fingerprint,
                "dataset_fingerprint": report["dataset_fingerprint"], "feature_fingerprint": feature_fingerprint,
                "inference_code_fingerprint": report["inference_code_fingerprint"]}
    library.atomic_json(directory / "experiment.json", manifest)
    library.atomic_json(directory / "models.json", models)
    library.atomic_json(directory / "features.json", {"feature_names": feature_names, "raw_schema": schema,
                                                     "samples": {mode: {name: [{key: value for key, value in row.items()
                                                                                 if key != "reference_matches"} for row in rows]
                                                                        for name, rows in splits.items()} for mode, splits in samples.items()}})
    _write_reports(directory, report, predictions)
    return report


def run_library_experiment(*, output_dir=None, **parameters):
    shared = copy.deepcopy(library.load_library())
    datasets = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((library.LIBRARY_DIR / "datasets").glob("*.json"))]
    return run_experiment(datasets, shared["recognition_config"], shared.get("reference_house_id"),
                          profile_library=shared, output_dir=output_dir, scope="human-labelled", **parameters)


def run_synthetic_benchmark(*, output_dir=None, seed=42, sessions_per_house=20, **parameters):
    from har_benchmark import build_synthetic_benchmark
    benchmark = build_synthetic_benchmark(seed=seed, sessions_per_house=sessions_per_house)
    return run_experiment(benchmark["datasets"], benchmark["config"], benchmark["source_house_id"],
                          output_dir=output_dir, seed=seed, scope="synthetic",
                          ground_truth_provenance=benchmark["ground_truth_provenance"], **parameters)


def replay_experiment(directory, *, output_dir=None):
    directory = Path(directory)
    manifest = json.loads((directory / "experiment.json").read_text(encoding="utf-8"))
    if manifest.get("inference_code_fingerprint") != _inference_code_fingerprint():
        raise ValueError("Recognition/feature code changed since this experiment. Run a new experiment to compare the updated implementation.")
    if fingerprint(manifest["datasets"]) != manifest["dataset_fingerprint"]:
        raise ValueError("The saved observations/labels changed. Create a new experiment rather than replaying a modified manifest.")
    models = json.loads((directory / "models.json").read_text(encoding="utf-8"))
    expected = fingerprint({"models": models, "settings": manifest["config"], "profiles": manifest["profile_library"],
                            "parameters": manifest["parameters"], "raw_schema": _raw_schema(
                                split_sessions(manifest["datasets"], manifest["source_house_id"], seed=manifest["parameters"]["seed"])["train"])})
    if expected != manifest["model_fingerprint"]:
        raise ValueError("The frozen model/configuration changed; its fingerprint no longer matches.")
    return run_experiment(manifest["datasets"], manifest["config"], manifest["source_house_id"],
                          profile_library=manifest["profile_library"], output_dir=output_dir, scope=manifest["scope"],
                          ground_truth_provenance=manifest["ground_truth_provenance"],
                          _saved_models=models, _expected_features=manifest["feature_fingerprint"], **manifest["parameters"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--demo", action="store_true", help="Run independently scripted synthetic source/target sessions")
    source.add_argument("--library", action="store_true", help="Use registered reference and held-out human-labelled sessions")
    source.add_argument("--replay", type=Path, help="Replay frozen JSON models and observations without fitting")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--window-minutes", type=float, default=10)
    parser.add_argument("--minimum-probability", type=float, default=.5)
    parser.add_argument("--time-of-day", action="store_true")
    parser.add_argument("--broad-cooking", action="store_true", help="Allow cooking without observed Kitchen PIR context")
    args = parser.parse_args(argv)
    try:
        if args.replay:
            report = replay_experiment(args.replay, output_dir=args.output)
        else:
            parameters = {"output_dir": args.output, "seed": args.seed, "window_minutes": args.window_minutes,
                          "minimum_probability": args.minimum_probability, "strict_cooking": not args.broad_cooking,
                          "use_time_of_day": args.time_of_day}
            report = (run_synthetic_benchmark(sessions_per_house=args.sessions, **parameters) if args.demo
                      else run_library_experiment(**parameters))
    except (ValueError, OSError, KeyError) as error:
        parser.error(str(error))
    print(json.dumps({"scope": report["scope"], "output_dir": report["output_dir"],
                      "methods": {name: {"target_exact": result["target"]["exact_match_accuracy"],
                                          "target_micro_f1": result["target"]["micro_f1"]}
                                  for name, result in report["methods"].items()}}, indent=2))


if __name__ == "__main__":
    main()
