"""Controlled source-house/target-house activity-recognition experiments."""

from __future__ import annotations

import json
import math
import queue
import threading
import tkinter as tk
from tkinter import ttk

from app.ui.theme import apply_theme_tree


_SYNTHETIC = "Demo — scripted synthetic ground truth"
_HUMAN = "Registered human-labelled datasets"


def _percent(value):
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "N/A"


def _run_names(runs):
    if not runs:
        return "None"
    return ", ".join(str(item.get("run_id", item)) if isinstance(item, dict) else str(item)
                     for item in runs)


def _report_text(report, parameters):
    """Human-readable context supplements the comparison table and JSON export."""
    synthetic = report.get("scope") == "synthetic"
    lines = [
        "SCRIPTED SYNTHETIC BENCHMARK" if synthetic else "HUMAN-LABELLED DATASET EXPERIMENT",
        ("Ground truth comes from scripted synthetic activities. These results do not measure "
         "accuracy on human behaviour." if synthetic else
         "Ground truth comes from manually labelled frames in the registered datasets."),
        "",
        f"Source house: {report.get('source_house_id', 'N/A')}",
    ]
    for key, label in (("training_runs", "Source training runs"),
                       ("validation_runs", "Source validation runs"),
                       ("source_test_runs", "Held-out source test runs"),
                       ("target_test_runs", "Held-out target test runs")):
        runs = report.get(key) or []
        lines.append(f"{label} ({len(runs)}): {_run_names(runs)}")
    lines.extend([
        "",
        "The source training, validation and test sessions are separate. Target-house test "
        "labels are excluded from training and parameter selection.",
        "Exact match requires the complete predicted activity set to match the labelled set. "
        "Micro F1 combines activity decisions; macro F1 averages activity scores. Coverage is "
        "reported separately from accuracy.",
        "",
        "Run parameters:",
        json.dumps(parameters, indent=2, sort_keys=True),
        "",
        "Target results by activity:",
    ])
    for method_id, method in report.get("methods", {}).items():
        lines.append(f"\n{method.get('label', method_id)}")
        target = method.get("target") or {}
        activities = target.get("per_activity") or {}
        unsupported = target.get("unsupported_labels") or []
        if unsupported:
            lines.append("  Unsupported target activities: " + ", ".join(unsupported))
        if not activities:
            lines.append("  No scored activity labels.")
        for name, metrics in sorted(activities.items()):
            lines.append(f"  {name}: precision {_percent(metrics.get('precision'))}; "
                         f"recall {_percent(metrics.get('recall'))}; "
                         f"F1 {_percent(metrics.get('f1'))}; support {metrics.get('support', 0)}")
        houses = target.get("per_house") or {}
        if houses:
            lines.append("  Target houses:")
            for name, metrics in sorted(houses.items()):
                lines.append(f"    {name}: {metrics.get('labelled_samples', 0)} labelled samples; "
                             f"exact match {_percent(metrics.get('exact_match_accuracy'))}; "
                             f"micro F1 {_percent(metrics.get('micro_f1'))}")
    if report.get("calibration"):
        lines.extend(["", "Source-validation calibration:", json.dumps(report["calibration"], indent=2, sort_keys=True)])
    lines.extend(["", "Limitations:"])
    limitations = report.get("limitations") or []
    lines.extend(f"• {item}" for item in limitations)
    if not limitations:
        lines.append("No additional limitations were included in this report.")
    lines.extend(["", f"Model fingerprint: {report.get('model_fingerprint', 'N/A')}",
                  f"Saved results: {report.get('output_dir', 'N/A')}"])
    return "\n".join(lines)


def open_har_experiment(ctx):
    """Open a nonblocking experiment window; all computation stays off Tk's thread."""
    previous = getattr(ctx, "_har_experiment_window", None)
    if previous is not None and previous.winfo_exists():
        previous.lift()
        return previous

    window = tk.Toplevel(ctx.window)
    ctx._har_experiment_window = window
    window.title("Activity recognition — source / target experiment")
    window.geometry("1180x820")
    window.minsize(1000, 720)
    body = tk.Frame(window, padx=12, pady=10)
    body.pack(fill=tk.BOTH, expand=True)
    heading = tk.Label(body, text="Compare reference transfer, direct property rules and learned classifiers "
                       "on separate source and target houses.", anchor="w", justify=tk.LEFT,
                       wraplength=1120)
    heading.pack(fill=tk.X)
    explanation = tk.Label(body, text="Train and tune on source-house sessions. Test on held-out source sessions "
                           "and different target houses. Demo ground truth is scripted; it does not measure "
                           "recognition of human behaviour.", anchor="w", justify=tk.LEFT,
                           wraplength=1120)
    explanation.pack(fill=tk.X, pady=(3, 9))

    settings = tk.LabelFrame(body, text="Experiment settings", padx=10, pady=8)
    settings.pack(fill=tk.X)
    settings.columnconfigure(1, weight=1)
    tk.Label(settings, text="Data source").grid(row=0, column=0, sticky="w", padx=(0, 8))
    source = ttk.Combobox(settings, values=(_SYNTHETIC, _HUMAN), state="readonly", width=48)
    source.set(_SYNTHETIC)
    source.grid(row=0, column=1, columnspan=5, sticky="ew", pady=3)
    fields = {"source": source}
    for column, (key, label, value) in enumerate((("seed", "Random seed", "42"),
                                                 ("window_minutes", "Window (sim. min)", "10"),
                                                 ("sessions_per_house", "Demo sessions / house", "20"))):
        tk.Label(settings, text=label).grid(row=1, column=column * 2, sticky="w", padx=(0, 8), pady=4)
        entry = tk.Entry(settings, width=12)
        entry.insert(0, value)
        entry.grid(row=1, column=column * 2 + 1, sticky="ew", padx=(0, 18), pady=4)
        fields[key] = entry
    tk.Label(settings, text="Minimum decision certainty").grid(row=2, column=0, sticky="w", padx=(0, 8))
    probability = tk.Entry(settings, width=12)
    probability.insert(0, "0.5")
    probability.grid(row=2, column=1, sticky="ew", padx=(0, 18), pady=3)
    fields["minimum_probability"] = probability
    strict = tk.BooleanVar(window, value=True)
    time_of_day = tk.BooleanVar(window, value=False)
    fields["strict_cooking"] = strict
    fields["use_time_of_day"] = time_of_day
    strict_checkbox = tk.Checkbutton(settings, text="Cooking requires kitchen presence", variable=strict)
    strict_checkbox.grid(row=2, column=2, columnspan=2, sticky="w")
    time_checkbox = tk.Checkbutton(settings, text="Use time of day as a feature", variable=time_of_day)
    time_checkbox.grid(row=2, column=4, columnspan=2, sticky="w")

    controls = tk.Frame(body)
    controls.pack(fill=tk.X, pady=8)
    run_button = tk.Button(controls, text="Run experiment")
    run_button.pack(side=tk.LEFT)
    copy_button = tk.Button(controls, text="Copy results folder", state=tk.DISABLED)
    copy_button.pack(side=tk.LEFT, padx=8)
    progress = ttk.Progressbar(controls, mode="indeterminate", length=150)
    progress.pack(side=tk.LEFT, padx=6)
    tk.Label(controls, text="Each completed run saves a report in its own results folder.",
             anchor="w").pack(side=tk.LEFT, padx=8)

    comparison = tk.LabelFrame(body, text="Held-out comparison — percentages are measured on the selected dataset",
                               padx=6, pady=6)
    comparison.pack(fill=tk.X)
    columns = ("method", "source_samples", "source_accuracy", "source_f1", "target_samples",
               "target_accuracy", "target_f1", "target_macro", "target_coverage")
    tree = ttk.Treeview(comparison, columns=columns, show="headings", height=4)
    for key, label, width in (("method", "Method", 205), ("source_samples", "Source n", 68),
                              ("source_accuracy", "Source exact", 95), ("source_f1", "Source micro F1", 105),
                              ("target_samples", "Target n", 68), ("target_accuracy", "Target exact", 95),
                              ("target_f1", "Target micro F1", 105), ("target_macro", "Target macro F1", 105),
                              ("target_coverage", "Target coverage", 105)):
        tree.heading(key, text=label)
        tree.column(key, width=width, minwidth=width, stretch=key == "method",
                    anchor="w" if key == "method" else "center")
    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(comparison, orient=tk.HORIZONTAL, command=tree.xview)
    scrollbar.grid(row=1, column=0, sticky="ew")
    tree.configure(xscrollcommand=scrollbar.set)
    comparison.columnconfigure(0, weight=1)

    details_box = tk.LabelFrame(body, text="Data splits, activity results and limitations", padx=6, pady=6)
    details_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
    details = tk.Text(details_box, wrap=tk.WORD, height=12, state=tk.DISABLED)
    details.grid(row=0, column=0, sticky="nsew")
    details_scroll = ttk.Scrollbar(details_box, orient=tk.VERTICAL, command=details.yview)
    details_scroll.grid(row=0, column=1, sticky="ns")
    details.configure(yscrollcommand=details_scroll.set)
    details_box.columnconfigure(0, weight=1)
    details_box.rowconfigure(0, weight=1)
    status = tk.StringVar(window, value="Ready. Select the demo or register a source house and human-labelled runs.")
    status_label = tk.Label(body, textvariable=status, anchor="w", justify=tk.LEFT, wraplength=1120)
    status_label.pack(fill=tk.X, pady=(8, 0))
    tk.Button(body, text="Close", command=window.destroy).pack(anchor="e", pady=(5, 0))

    messages = queue.Queue()
    poll_id = None
    closed = False
    window._experiment_busy = False
    window._experiment_last_report = None
    editable = (source, *[fields[key] for key in ("seed", "window_minutes", "sessions_per_house",
                                                 "minimum_probability")], strict_checkbox, time_checkbox)

    def set_details(text):
        details.configure(state=tk.NORMAL)
        details.delete("1.0", tk.END)
        details.insert("1.0", text)
        details.configure(state=tk.DISABLED)

    def update_source(_event=None):
        if not window._experiment_busy:
            fields["sessions_per_house"].configure(state=tk.NORMAL if source.get() == _SYNTHETIC else tk.DISABLED)

    source.bind("<<ComboboxSelected>>", update_source)

    def set_running(running):
        window._experiment_busy = running
        run_button.configure(state=tk.DISABLED if running else tk.NORMAL)
        for widget in editable:
            widget.configure(state=tk.DISABLED if running else ("readonly" if widget is source else tk.NORMAL))
        if running:
            copy_button.configure(state=tk.DISABLED)
            progress.start(15)
        else:
            progress.stop()
            update_source()
            copy_button.configure(state=tk.NORMAL if window._experiment_last_report else tk.DISABLED)

    def poll():
        nonlocal poll_id
        poll_id = None
        if closed:
            return
        try:
            successful, payload, parameters = messages.get_nowait()
        except queue.Empty:
            poll_id = window.after(100, poll)
            return
        set_running(False)
        if not successful:
            status.set(f"Experiment failed: {payload}")
            return
        report = payload
        window._experiment_last_report = report
        tree.delete(*tree.get_children())
        for method_id, method in report.get("methods", {}).items():
            source_metrics = method.get("source") or {}
            target_metrics = method.get("target") or {}
            tree.insert("", tk.END, iid=method_id, values=(
                method.get("label", method_id), source_metrics.get("labelled_samples", 0),
                _percent(source_metrics.get("exact_match_accuracy")), _percent(source_metrics.get("micro_f1")),
                target_metrics.get("labelled_samples", 0), _percent(target_metrics.get("exact_match_accuracy")),
                _percent(target_metrics.get("micro_f1")), _percent(target_metrics.get("macro_f1")),
                _percent(target_metrics.get("coverage"))))
        set_details(_report_text(report, parameters))
        copy_button.configure(state=tk.NORMAL if report.get("output_dir") else tk.DISABLED)
        kind = "Scripted synthetic benchmark" if report.get("scope") == "synthetic" else "Human-labelled experiment"
        status.set(f"{kind} complete. Reports saved to: {report.get('output_dir', 'N/A')}")

    def run():
        nonlocal poll_id
        if window._experiment_busy:
            return
        try:
            parameters = {"seed": int(fields["seed"].get()),
                          "window_minutes": float(fields["window_minutes"].get()),
                          "minimum_probability": float(fields["minimum_probability"].get()),
                          "strict_cooking": strict.get(), "use_time_of_day": time_of_day.get()}
            if not 0 <= parameters["seed"] < 2 ** 32:
                raise ValueError("Random seed must be an integer from 0 to 4294967295.")
            if not math.isfinite(parameters["window_minutes"]) or parameters["window_minutes"] <= 0:
                raise ValueError("Window length must be a positive finite number of simulated minutes.")
            if not math.isfinite(parameters["minimum_probability"]) or not 0 <= parameters["minimum_probability"] <= 1:
                raise ValueError("Probability threshold must be between 0 and 1.")
            synthetic = source.get() == _SYNTHETIC
            if synthetic:
                parameters["sessions_per_house"] = int(fields["sessions_per_house"].get())
                if parameters["sessions_per_house"] <= 0:
                    raise ValueError("Demo sessions per house must be a positive integer.")
        except (ValueError, OverflowError) as error:
            status.set(f"Check experiment settings: {error}")
            return
        window._experiment_last_report = None
        tree.delete(*tree.get_children())
        set_details("Experiment running. The completed report will appear here.")
        set_running(True)
        status.set("Running scripted synthetic benchmark…" if synthetic else "Running human-labelled dataset experiment…")
        # Dispose of unreachable cycles from closed Tk windows on Tk's thread
        # before the numerical worker allocates enough objects to trigger GC.
        import gc
        gc.collect()

        def worker():
            try:
                import har_experiment as experiment

                function = experiment.run_synthetic_benchmark if synthetic else experiment.run_library_experiment
                report = function(**parameters)
            except Exception as error:
                messages.put((False, str(error) or type(error).__name__, parameters))
            else:
                messages.put((True, report, parameters))

        threading.Thread(target=worker, name="HAR source-target experiment", daemon=True).start()
        poll_id = window.after(100, poll)

    def copy_path():
        report = window._experiment_last_report or {}
        path = report.get("output_dir")
        if path:
            window.clipboard_clear()
            window.clipboard_append(str(path))
            status.set(f"Results folder copied: {path}")

    def destroyed(event):
        nonlocal closed, poll_id
        if event.widget is window:
            closed = True
            if poll_id is not None:
                try:
                    window.after_cancel(poll_id)
                except tk.TclError:
                    pass
                poll_id = None

    window.bind("<Destroy>", destroyed, add="+")
    def resized(event):
        for label in (heading, explanation, status_label):
            label.configure(wraplength=max(300, event.width - 24))

    body.bind("<Configure>", resized, add="+")
    run_button.configure(command=run)
    copy_button.configure(command=copy_path)
    window._experiment_fields = fields
    window._experiment_run_button = run_button
    window._experiment_tree = tree
    window._experiment_status = status
    window._experiment_report = details
    window._experiment_copy_button = copy_button
    set_details("No experiment has been run.\n\nThe demo creates independently scripted activity labels. "
                "For human-labelled evaluation, register a source house as the reference, record "
                "multiple reference sessions, and record held-out test sessions in different houses.\n\n"
                "Methods: reference-role transfer rules; direct property rules; Random Forest using "
                "semantic features; and Random Forest using raw source sensor identities.")
    apply_theme_tree(window, getattr(ctx, "settings", None))
    return window
