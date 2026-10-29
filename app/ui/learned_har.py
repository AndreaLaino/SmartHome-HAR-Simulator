"""Small guided UI for category-based, learned cross-house recognition.

Categories describe objects. Activity labels describe recorded observations;
there is deliberately no object-to-activity assignment in this window.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import gc
import queue
import threading
import tkinter as tk
import uuid
from tkinter import ttk

from app.ui.theme import apply_theme_tree


def parse_labels(value: str) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def _config(ctx) -> dict:
    if not isinstance(getattr(ctx, "har_config", None), dict):
        ctx.har_config = {}
    return ctx.har_config


def _objects(ctx):
    if getattr(ctx, "load_active", False):
        return [("device", item) for item in ctx.read_devices] + [
            ("sensor", item) for item in ctx.read_sensors
        ]
    from device import devices
    from sensor import sensors

    return [("device", item) for item in devices] + [("sensor", item) for item in sensors]


def _session_house(session):
    return str(session.get("house_id", "")), str(session.get("house_label") or session.get("house_id", ""))


def _source_house_names(sessions):
    return {session["house_id"]: session["house_label"]
            for session in sorted(sessions, key=lambda item: (str(item.get("created_at") or ""), item.get("session_id", "")))
            if session.get("partition") == "source" and session.get("house_label")}


def model_display_names(models, sessions):
    """Readable UI labels; model IDs, file paths and existing data stay intact."""
    source_names = _source_house_names(sessions)

    def created(model):
        try:
            value = datetime.fromisoformat(model.get("created_at", "").replace("Z", "+00:00"))
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        except (AttributeError, TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)

    ordered = sorted(models, key=lambda model: (created(model), model["model_id"]))
    names, used, counts = {}, set(), {}
    # Reserve persisted names first so legacy fallbacks cannot take their names.
    for model in ordered:
        name = model.get("display_name")
        if isinstance(name, str) and name.strip() and name.strip() not in used:
            names[model["model_id"]] = name.strip()
            used.add(name.strip())
    for model in ordered:
        source_name = model.get("source_house_label")
        if not isinstance(source_name, str) or not source_name.strip():
            source_name = source_names.get(model.get("source_house_id")) or "Unknown_house"
        house = "_".join(source_name.split())
        timestamp = created(model)
        date = timestamp.astimezone().strftime("%Y-%m-%d") if timestamp.year > 1 else "undated"
        base = f"{house}_{date}"
        identifier = model["model_id"]
        if identifier not in names:
            number = counts.get(base, 0) + 1
            name = f"{base}_{number:03d}"
            while name in used:
                number += 1
                name = f"{base}_{number:03d}"
            names[identifier] = name
            used.add(name)
        prefix = base + "_"
        suffix = names[identifier][len(prefix):] if names[identifier].startswith(prefix) else ""
        if suffix.isdecimal():
            counts[base] = max(counts.get(base, 0), int(suffix))
    return {model["model_id"]: names[model["model_id"]] for model in ordered}


def format_evaluation(report: dict) -> str:
    """Present evaluation scores without calling confidence an accuracy."""
    count = report.get("labelled_samples", 0)
    lines = []
    if report.get("scope") == "synthetic":
        lines.append("SYNTHETIC DEMONSTRATION: scripted observations and labels; this is not measured performance on real people.\n")
    lines.append(f"Labelled test observations: {count}")
    for name, label in (("exact_match_accuracy", "Exact activity-set accuracy"), ("micro_f1", "Micro F1")):
        value = report.get(name)
        if isinstance(value, (int, float)):
            lines.append(f"{label}: {100 * value:.1f}%")
    if "test_houses" in report:
        lines.append(f"Target houses: {report['test_houses']}")
    if "excluded_source_sessions" in report:
        excluded = report["excluded_source_sessions"]
        lines.append(f"Source sessions excluded: {len(excluded) if isinstance(excluded, list) else excluded}")
    activities = report.get("per_activity", {})
    if isinstance(activities, dict) and activities:
        lines.append("\nPer activity:")
        for name, scores in activities.items():
            if isinstance(scores, dict):
                parts = [f"{key} {100 * scores[key]:.1f}%" for key in ("precision", "recall", "f1")
                         if isinstance(scores.get(key), (int, float))]
                lines.append(f"  {name}: {', '.join(parts)}")
    if not count:
        lines.append("\nRecord labelled target sessions to measure transfer performance.")
    return "\n".join(lines)


class LearnedHARWindow:
    def __init__(self, ctx):
        from har import catalogue, storage

        self.ctx = ctx
        self.catalogue_api = catalogue
        self.storage = storage
        self.catalogue = catalogue.load_catalogue()
        self.objects = {}
        self.source_houses = {}
        self.model_choices = {}
        self.model_names = {}
        self._jobs = queue.Queue()
        self._busy = False
        self._success_callback = None
        self._closed = False
        self._poll_job = None
        self.window = tk.Toplevel(ctx.window)
        self.window.title("Learned activity recognition")
        self.window.geometry("1120x780")
        self.window.minsize(900, 650)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        ttk.Label(self.window, text=(
            "1. Describe objects with shared categories   →   2. Record observations and true activities   →   "
            "3. Train on a source house and test other houses"), wraplength=1050,
            padding=12).grid(row=0, column=0, sticky="ew")
        self.tabs = ttk.Notebook(self.window)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=12)
        self.category_tab = ttk.Frame(self.tabs, padding=12)
        self.record_tab = ttk.Frame(self.tabs, padding=12)
        self.train_tab = ttk.Frame(self.tabs, padding=12)
        for frame, title in ((self.category_tab, "1 · Categories"),
                             (self.record_tab, "2 · Record sessions"),
                             (self.train_tab, "3 · Train & evaluate")):
            self.tabs.add(frame, text=title)
        self.status = tk.StringVar(value="Categories are object descriptions, not activity assignments.")
        ttk.Label(self.window, textvariable=self.status, wraplength=1050,
                  padding=12).grid(row=2, column=0, sticky="ew")
        self._build_categories()
        self._build_recording()
        self._build_training()
        self.refresh_objects()
        self.refresh_data()
        apply_theme_tree(self.window, "light")
        self._poll_job = self.window.after(500, self._poll)

    def _safe(self, callback):
        try:
            return callback()
        except Exception as error:
            self.status.set(str(error))
            return None

    def _require_not_recording(self):
        from har.runtime import recording_status
        if recording_status(self.ctx).get("recording"):
            raise ValueError("Stop and save the recording before changing object categories or house identity.")

    def _build_categories(self):
        frame = self.category_tab
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(1, weight=1)
        ttk.Label(frame, text=("Assign one or more categories to each object. The same category means the same "
                              "kind of signal in every house; it does not decide an activity."),
                  wraplength=1000).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.object_tree = ttk.Treeview(frame, columns=("kind", "type", "categories"), show="tree headings", selectmode="browse")
        self.object_tree.heading("#0", text="Object")
        for key, title, width in (("kind", "Kind", 65), ("type", "Type", 110), ("categories", "Effective categories", 220)):
            self.object_tree.heading(key, text=title)
            self.object_tree.column(key, width=width, minwidth=65)
        self.object_tree.column("#0", width=130)
        self.object_tree.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.object_tree.bind("<<TreeviewSelect>>", lambda _event: self._select_object())
        side = ttk.Frame(frame)
        side.grid(row=1, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        side.rowconfigure(1, weight=1)
        self.category_hint = tk.StringVar(value="Select an object on the left.")
        ttk.Label(side, textvariable=self.category_hint, wraplength=360).grid(row=0, column=0, sticky="ew")
        list_frame = ttk.Frame(side)
        list_frame.grid(row=1, column=0, sticky="nsew", pady=8)
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.category_list = tk.Listbox(list_frame, selectmode=tk.MULTIPLE, exportselection=False, height=9)
        self.category_list.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.category_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.category_list.configure(yscrollcommand=scroll.set)
        self.category_detail = tk.StringVar()
        ttk.Label(side, textvariable=self.category_detail, wraplength=360).grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.category_list.bind("<<ListboxSelect>>", lambda _event: self._describe_categories())
        ttk.Button(side, text="Apply selected categories to object", command=lambda: self._safe(self.apply_categories)).grid(row=3, column=0, sticky="ew")
        ttk.Button(side, text="Save as defaults for this object type", command=lambda: self._safe(self.save_type_defaults)).grid(row=4, column=0, sticky="ew", pady=5)
        ttk.Button(side, text="Use shared type defaults", command=lambda: self._safe(self.use_type_defaults)).grid(row=5, column=0, sticky="ew")
        ttk.Label(side, text="Type defaults are shared across houses. Custom object assignments are saved in the scenario.",
                  wraplength=360).grid(row=6, column=0, sticky="ew", pady=8)
        dictionary = ttk.LabelFrame(frame, text="Add a shared category", padding=8)
        dictionary.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        dictionary.columnconfigure(3, weight=1)
        self.new_kind = tk.StringVar(value="device")
        self.new_category = tk.StringVar()
        self.new_description = tk.StringVar()
        self.new_measurement = tk.StringVar()
        self.new_unit = tk.StringVar()
        ttk.Combobox(dictionary, textvariable=self.new_kind, values=("device", "sensor"), state="readonly", width=9).grid(row=0, column=0, padx=(0, 8))
        ttk.Label(dictionary, text="Name").grid(row=0, column=1)
        ttk.Entry(dictionary, textvariable=self.new_category, width=24).grid(row=0, column=2, padx=6)
        ttk.Entry(dictionary, textvariable=self.new_description).grid(row=0, column=3, sticky="ew", padx=6)
        ttk.Label(dictionary, text="Description above; sensor measurement / unit below").grid(row=1, column=2, columnspan=2, sticky="w", pady=5)
        ttk.Combobox(dictionary, textvariable=self.new_measurement,
                     values=sorted(self.catalogue_api.MEASUREMENTS), state="readonly", width=24).grid(row=2, column=2, padx=6, sticky="ew")
        ttk.Entry(dictionary, textvariable=self.new_unit, width=12).grid(row=2, column=3, padx=6, sticky="w")
        ttk.Button(dictionary, text="Save category", command=lambda: self._safe(self.add_category)).grid(row=0, column=4, rowspan=3, padx=8)
        ttk.Button(frame, text="Refresh objects / dictionary", command=lambda: self._safe(self.refresh_objects)).grid(row=3, column=0, sticky="w", pady=(8, 0))

    def refresh_objects(self):
        selected = self._selected_object()
        self.catalogue = self.catalogue_api.load_catalogue()
        self.object_tree.delete(*self.object_tree.get_children())
        self.objects.clear()
        for index, (kind, obj) in enumerate(_objects(self.ctx)):
            item_id = str(index)
            self.objects[item_id] = (kind, obj)
            categories = self.catalogue_api.categories_for(obj, kind, self.catalogue)
            self.object_tree.insert("", "end", iid=item_id, text=obj.name,
                                    values=(kind, obj.type, ", ".join(categories) or "Unclassified"))
            if selected is not None and obj is selected[1]:
                self.object_tree.selection_set(item_id)
        self._select_object()

    def _selected_object(self):
        selected = self.object_tree.selection()
        return self.objects.get(selected[0]) if selected else None

    def _select_object(self):
        self.category_list.delete(0, tk.END)
        selected = self._selected_object()
        if selected is None:
            self.category_hint.set("Select an object on the left.")
            self.category_detail.set("")
            return
        kind, obj = selected
        self.category_hint.set(f"{obj.name} · {obj.type}\nSelect compatible {kind} categories:")
        current = self.catalogue_api.categories_for(obj, kind, self.catalogue)
        for index, name in enumerate(sorted(self.catalogue[f"{kind}_categories"])):
            self.category_list.insert(tk.END, name)
            if name in current:
                self.category_list.selection_set(index)
        self._describe_categories()

    def _chosen_categories(self):
        return [self.category_list.get(index) for index in self.category_list.curselection()]

    def _describe_categories(self):
        selected = self._selected_object()
        if selected is None:
            return
        lines = []
        for name in self._chosen_categories():
            description = self.catalogue[f"{selected[0]}_categories"][name]
            if isinstance(description, dict):
                text = description.get("description", "")
                measure = description.get("measurement", "")
                unit = description.get("unit", "")
                lines.append(f"{name}: {text} [{measure}; {unit}]")
            else:
                lines.append(f"{name}: {description}")
        self.category_detail.set("\n".join(lines) or "No category selected: this object will be unclassified.")

    def apply_categories(self):
        self._require_not_recording()
        selected = self._selected_object()
        if selected is None:
            raise ValueError("Select an object first.")
        selected[1].categories = self._chosen_categories()
        self.refresh_objects()
        self.status.set("Object categories applied. Save the scenario to keep this assignment.")

    def save_type_defaults(self):
        self._require_not_recording()
        selected = self._selected_object()
        if selected is None:
            raise ValueError("Select an object first.")
        kind, obj = selected
        updated = deepcopy(self.catalogue)
        type_map = updated[f"{kind}_types"]
        type_name = next((name for name in type_map if name.casefold() == obj.type.casefold()), obj.type)
        type_map[type_name] = self._chosen_categories()
        self.catalogue_api.save_catalogue(updated)
        obj.categories = None
        self.refresh_objects()
        self.status.set(f"Shared defaults saved for {obj.type}. Objects using type defaults follow this mapping across houses.")

    def use_type_defaults(self):
        self._require_not_recording()
        selected = self._selected_object()
        if selected is None:
            raise ValueError("Select an object first.")
        selected[1].categories = None
        self.refresh_objects()
        self.status.set("Object now uses shared type defaults. Save the scenario to keep the change.")

    def add_category(self):
        self._require_not_recording()
        name = self.new_category.get().strip()
        description = self.new_description.get().strip()
        kind = self.new_kind.get()
        if not name or not description:
            raise ValueError("Provide a category name and description.")
        updated = deepcopy(self.catalogue)
        category_map = updated[f"{kind}_categories"]
        if name in category_map:
            raise ValueError("This category already exists. Use a new name to preserve its meaning for existing models.")
        if kind == "sensor":
            measurement, unit = self.new_measurement.get().strip(), self.new_unit.get().strip()
            if not measurement or not unit:
                raise ValueError("Sensor categories require a measurement and unit (for example power / W, or presence / boolean).")
            category_map[name] = {"description": description, "measurement": measurement, "unit": unit}
        else:
            category_map[name] = description
        self.catalogue_api.save_catalogue(updated)
        self.refresh_objects()
        self.new_category.set("")
        self.new_description.set("")
        self.status.set(f"Shared category {name} saved. Assign it consistently in source and target houses.")

    def _build_recording(self):
        frame = self.record_tab
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=("Run Simulation → Manual → Start. Start recording here, then perform actions in the map. "
                              "Apply the true activities when they change. Reset the simulation between independent sessions."),
                  wraplength=990).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 16))
        config = _config(self.ctx)
        self.house_name = tk.StringVar(value=config.get("house_label", ""))
        self.house_identity = tk.StringVar()
        ttk.Label(frame, text="House name").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.house_name).grid(row=1, column=1, sticky="ew", padx=10)
        ttk.Button(frame, text="Save house identity", command=lambda: self._safe(self.save_identity)).grid(row=1, column=2)
        ttk.Label(frame, textvariable=self.house_identity, wraplength=970).grid(row=2, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Button(frame, text="New house identity for a copied scenario", command=lambda: self._safe(self.new_identity)).grid(row=3, column=1, sticky="w", padx=10)
        ttk.Label(frame, text="Recording partition").grid(row=4, column=0, sticky="w", pady=(24, 8))
        self.partition = tk.StringVar(value="source")
        ttk.Combobox(frame, textvariable=self.partition, values=("source", "target"), state="readonly", width=18).grid(row=4, column=1, sticky="w", padx=10, pady=(24, 8))
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="w", pady=8)
        self.start_button = ttk.Button(buttons, text="Start recording", command=lambda: self._safe(self.start_recording))
        self.start_button.pack(side="left", padx=(0, 10))
        self.stop_button = ttk.Button(buttons, text="Stop & save session", command=lambda: self._safe(self.stop_recording))
        self.stop_button.pack(side="left")
        ttk.Separator(frame).grid(row=6, column=0, columnspan=3, sticky="ew", pady=18)
        ttk.Label(frame, text="True activities now").grid(row=7, column=0, sticky="w")
        self.labels = tk.StringVar()
        ttk.Entry(frame, textvariable=self.labels).grid(row=7, column=1, sticky="ew", padx=10)
        ttk.Button(frame, text="Apply labels", command=lambda: self._safe(self.apply_labels)).grid(row=7, column=2)
        ttk.Label(frame, text="Comma-separated, for example: cooking, relaxing. These labels describe what the person is doing.",
                  wraplength=900).grid(row=8, column=0, columnspan=3, sticky="w", pady=8)
        self.no_activity = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="No activity (explicit ground truth)", variable=self.no_activity).grid(row=9, column=1, sticky="w", padx=10)
        ttk.Label(frame, text=("Before the first Apply labels, observations remain unlabelled context. Labels apply from now on, "
                              "never retroactively. An empty text field is not an explicit 'no activity' label."),
                  wraplength=990).grid(row=10, column=0, columnspan=3, sticky="ew", pady=16)
        self.recording_text = tk.StringVar(value="Not recording.")
        ttk.Label(frame, textvariable=self.recording_text, wraplength=990).grid(row=11, column=0, columnspan=3, sticky="ew")
        ttk.Label(frame, text="A trained model is not required to record training data. Save the scenario to preserve its house identity.",
                  wraplength=990).grid(row=12, column=0, columnspan=3, sticky="ew", pady=16)
        self._update_identity()

    def _update_identity(self):
        config = _config(self.ctx)
        self.house_identity.set(f"House ID: {config.get('house_id') or 'not assigned'}")

    def save_identity(self):
        self._require_not_recording()
        name = self.house_name.get().strip()
        if not name:
            raise ValueError("Provide a house name.")
        config = _config(self.ctx)
        config.setdefault("house_id", str(uuid.uuid4()))
        config["house_label"] = name
        self._update_identity()
        self.status.set("House identity updated. Save the scenario to keep it.")

    def new_identity(self):
        self._require_not_recording()
        name = self.house_name.get().strip()
        if not name:
            raise ValueError("Provide the new house name first.")
        config = _config(self.ctx)
        config.update(house_id=str(uuid.uuid4()), house_label=name, model_id=None)
        self._update_identity()
        self.status.set("New house identity created; model deactivated. Save this scenario as a separate house.")
        self._update_model_status()

    def start_recording(self):
        from har.runtime import start_recording
        if getattr(self.ctx, "timer_app_instance", None) is None:
            raise ValueError("Choose Simulation → Manual first. Then start recording here and press Start in the simulation.")
        self.save_identity()
        start_recording(self.ctx, partition=self.partition.get())
        self.labels.set("")
        self.no_activity.set(False)
        self.status.set("Recording started. Apply true activity labels, then interact with the simulation.")
        self._update_recording()

    def apply_labels(self):
        from har.runtime import set_recording_labels
        labels = parse_labels(self.labels.get())
        if self.no_activity.get():
            if labels:
                raise ValueError("Clear activity names when selecting 'No activity'.")
        elif not labels:
            raise ValueError("Enter an activity, or explicitly select 'No activity'.")
        set_recording_labels(self.ctx, labels)
        self.status.set("Ground truth applied from this point: " + (", ".join(labels) or "no activity"))
        self._update_recording()

    def stop_recording(self):
        from har.runtime import stop_recording
        path = stop_recording(self.ctx)
        self.refresh_data()
        self._update_recording()
        self.status.set(f"Session saved: {path}")

    def _update_recording(self):
        from har.runtime import recording_status
        state = recording_status(self.ctx)
        recording = bool(state.get("recording"))
        self.start_button.configure(state="disabled" if recording else "normal")
        self.stop_button.configure(state="normal" if recording else "disabled")
        labels = state.get("labels")
        text = "unlabelled context" if labels is None else (", ".join(labels) or "no activity")
        self.recording_text.set(f"{'Recording' if recording else 'Not recording'} · Frames: {state.get('frames', 0)} · Ground truth: {text}")

    def _build_training(self):
        frame = self.train_tab
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(5, weight=1)
        ttk.Label(frame, text=("Train from at least 3 independent labelled SOURCE sessions. Complete sessions are kept separate "
                              "for training, validation and source testing. Target data is used only for evaluation."),
                  wraplength=990).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        ttk.Label(frame, text="Source house").grid(row=1, column=0, sticky="w")
        self.source = tk.StringVar()
        self.source_combo = ttk.Combobox(frame, textvariable=self.source, state="readonly")
        self.source_combo.grid(row=1, column=1, sticky="ew", padx=10)
        ttk.Button(frame, text="Refresh sessions / models", command=lambda: self._safe(self.refresh_data)).grid(row=1, column=2)
        self.session_summary = tk.StringVar()
        ttk.Label(frame, textvariable=self.session_summary, wraplength=650).grid(row=2, column=0, columnspan=2, sticky="ew", pady=8)
        self.demo_button = ttk.Button(frame, text="Add synthetic demo sessions", command=lambda: self._safe(self.add_demo_sessions))
        self.demo_button.grid(row=2, column=2, pady=8)
        self.source_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_session_summary())
        train_controls = ttk.Frame(frame)
        train_controls.grid(row=3, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(train_controls, text="Temporal context (simulated seconds)").pack(side="left")
        self.context_seconds = tk.StringVar(value="600")
        ttk.Entry(train_controls, textvariable=self.context_seconds, width=8).pack(side="left", padx=10)
        self.train_button = ttk.Button(train_controls, text="Train & evaluate on target houses", command=lambda: self._safe(self.train))
        self.train_button.pack(side="left", padx=10)
        ttk.Label(frame, text="Results compare predictions against labelled activities. Confidence is not measured accuracy.",
                  wraplength=990).grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)
        report_frame = ttk.Frame(frame)
        report_frame.grid(row=5, column=0, columnspan=3, sticky="nsew")
        report_frame.columnconfigure(0, weight=1)
        report_frame.rowconfigure(0, weight=1)
        self.report_text = tk.Text(report_frame, height=11, wrap="word", state="disabled")
        self.report_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(report_frame, command=self.report_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.report_text.configure(yscrollcommand=scrollbar.set)
        ttk.Label(frame, text="Saved model").grid(row=6, column=0, sticky="w", pady=(14, 5))
        self.model = tk.StringVar()
        self.model_combo = ttk.Combobox(frame, textvariable=self.model, state="readonly")
        self.model_combo.grid(row=6, column=1, sticky="ew", padx=10, pady=(14, 5))
        self.activate_button = ttk.Button(frame, text="Activate in this house", command=lambda: self._safe(self.activate_model))
        self.activate_button.grid(row=6, column=2, pady=(14, 5))
        actions = ttk.Frame(frame)
        actions.grid(row=7, column=1, columnspan=2, sticky="w", padx=10)
        self.evaluate_button = ttk.Button(actions, text="Evaluate selected model on target sessions", command=lambda: self._safe(self.evaluate_selected))
        self.evaluate_button.pack(side="left")
        ttk.Button(actions, text="Deactivate model", command=lambda: self._safe(self.deactivate_model)).pack(side="left", padx=10)
        self.model_status = tk.StringVar()
        ttk.Label(frame, textvariable=self.model_status, wraplength=990).grid(row=8, column=0, columnspan=3, sticky="ew", pady=8)
        self._update_model_status()

    def _update_model_status(self):
        model_id = _config(self.ctx).get("model_id")
        name = self.model_names.get(model_id, "unavailable (refresh saved models)") if model_id else \
            "none (no learned predictions)"
        self.model_status.set(f"Active model in this house: {name}")

    def add_demo_sessions(self):
        from har.demo import demo_sessions

        if self._busy:
            raise ValueError("Wait for the current experiment to finish before adding demo sessions.")
        sessions = demo_sessions(sessions_per_house=5, seed=42)
        for session in sessions:
            self.storage.save_session(session)
        self.refresh_data()
        for choice, house_id in self.source_houses.items():
            if house_id == "demo-source":
                self.source.set(choice)
                break
        self._update_session_summary()
        self._set_report("Synthetic demonstration only: these activities and observations were generated by a script.\n"
                         "Click Train & evaluate to try the pipeline. Results do not measure performance on real people.\n"
                         "Repeating this button replaces the same demo sessions; it does not change your recorded houses.")
        self.status.set(f"Added {len(sessions)} synthetic demo sessions. The current house and active model are unchanged.")

    def refresh_data(self):
        self.sessions = self.storage.list_sessions()
        self.source_houses.clear()
        for session in self.sessions:
            house_id, label = _session_house(session)
            if house_id and session.get("partition") == "source":
                self.source_houses[f"{label} [{house_id[:8]}]"] = house_id
        choices = sorted(self.source_houses)
        self.source_combo.configure(values=choices)
        if self.source.get() not in self.source_houses:
            self.source.set(choices[0] if choices else "")
        selected_id = self.model_choices.get(self.model.get())
        self.model_names = model_display_names(self.storage.list_models(), self.sessions)
        self.model_choices = {name: identifier for identifier, name in self.model_names.items()}
        self.model_combo.configure(values=list(self.model_choices))
        preferred_id = selected_id if selected_id in self.model_names else _config(self.ctx).get("model_id")
        self.model.set(self.model_names.get(preferred_id, next(iter(self.model_choices), "")))
        self._update_session_summary()
        self._update_model_status()

    def _update_session_summary(self):
        source_id = self.source_houses.get(self.source.get())
        source = [s for s in getattr(self, "sessions", []) if s.get("house_id") == source_id and s.get("partition") == "source"]
        targets = [s for s in getattr(self, "sessions", []) if s.get("house_id") != source_id and s.get("partition") == "target"]
        self.session_summary.set(f"Source sessions: {len(source)} · Target sessions in other houses: {len(targets)}")

    def _set_report(self, text):
        self.report_text.configure(state="normal")
        self.report_text.delete("1.0", tk.END)
        self.report_text.insert("1.0", text)
        self.report_text.configure(state="disabled")

    def _run_worker(self, job, success):
        if self._busy:
            raise ValueError("An experiment is already running.")
        self._busy = True
        for button in (self.train_button, self.evaluate_button):
            button.configure(state="disabled")
        self.status.set("Running experiment… You can continue using the simulator.")
        # Dispose of closed Tk windows on the Tk thread before training imports
        # allocate enough objects to trigger cyclic collection in the worker.
        gc.collect()
        self._success_callback = success
        results = self._jobs

        def work():
            try:
                results.put((job(), None))
            except Exception as error:
                results.put((None, str(error)))
        threading.Thread(target=work, name="learned-har-experiment", daemon=True).start()

    def train(self):
        from har.learning import train_source, evaluate_model
        source_id = self.source_houses.get(self.source.get())
        if not source_id:
            raise ValueError("Record and save source sessions first.")
        try:
            seconds = float(self.context_seconds.get())
        except ValueError:
            raise ValueError("Temporal context must be a positive number of seconds.") from None
        if not 0 < seconds <= 86400:
            raise ValueError("Temporal context must be between 0 and 86400 seconds.")
        sessions = self.storage.list_sessions()
        storage = self.storage

        def job():
            model = train_source(sessions, source_id, window_seconds=seconds, seed=42)
            model["source_house_label"] = _source_house_names(sessions)[source_id]
            model["display_name"] = model_display_names([*storage.list_models(), model], sessions)[model["model_id"]]
            storage.save_model(model)
            report = evaluate_model(model, sessions)
            path = storage.DATA_DIR / "reports" / f"{model['model_id']}_evaluation.json"
            storage.atomic_json(path, report)
            return model, report, path

        def success(result):
            model, report, path = result
            self.refresh_data()
            for choice, model_id in self.model_choices.items():
                if model_id == model["model_id"]:
                    self.model.set(choice)
            source_report = model.get("source_test_metrics")
            prefix = "Model saved. Activate it below to use learned recognition in this house.\n\n"
            if source_report:
                prefix += "Source hold-out:\n" + format_evaluation(source_report) + "\n\nTarget evaluation:\n"
            self._set_report(prefix + format_evaluation(report) + f"\n\nReport: {path}")
            self.status.set("Training completed. Target labels were used only to evaluate the saved source model.")
        self._run_worker(job, success)

    def evaluate_selected(self):
        from har.learning import evaluate_model
        model_id = self.model_choices.get(self.model.get())
        if not model_id:
            raise ValueError("Select a saved model first.")
        sessions = self.storage.list_sessions()
        storage = self.storage

        def job():
            model = storage.load_model(model_id)
            report = evaluate_model(model, sessions)
            path = storage.DATA_DIR / "reports" / f"{model_id}_evaluation.json"
            storage.atomic_json(path, report)
            return report, path

        def success(result):
            report, path = result
            self._set_report(format_evaluation(report) + f"\n\nReport: {path}")
            self.status.set("Target evaluation completed; the model was not retrained.")
        self._run_worker(job, success)

    def activate_model(self):
        from har.learning import validate_model
        model_id = self.model_choices.get(self.model.get())
        if not model_id:
            raise ValueError("Select a saved model first.")
        model = validate_model(self.storage.load_model(model_id))
        catalogue = self.catalogue_api.load_catalogue()
        self.catalogue_api.validate_catalogue_compatibility(model["catalogue"], catalogue)
        extra = set()
        for kind, obj in _objects(self.ctx):
            for category in self.catalogue_api.categories_for(obj, kind, catalogue):
                resolved = self.catalogue_api.category_name(category, kind, model["catalogue"])
                if resolved not in model["catalogue"][f"{kind}_categories"]:
                    extra.add(category)
        _config(self.ctx)["model_id"] = model_id
        self._update_model_status()
        text = "Learned model activated. Run the simulation; save the scenario to keep this selection."
        if extra:
            text += " Extra categories unused by this model: " + ", ".join(sorted(extra)) + "."
        self.status.set(text)

    def deactivate_model(self):
        _config(self.ctx)["model_id"] = None
        self._update_model_status()
        self.status.set("Learned model deactivated. Save the scenario to keep this choice.")

    def _poll(self):
        if self._closed:
            return
        while True:
            try:
                result, error = self._jobs.get_nowait()
            except queue.Empty:
                break
            self._busy = False
            success = self._success_callback
            self._success_callback = None
            for button in (self.train_button, self.evaluate_button):
                button.configure(state="normal")
            if error:
                self.status.set(error)
            elif success is not None:
                self._safe(lambda: success(result))
        self._safe(self._update_recording)
        self._poll_job = self.window.after(500, self._poll)

    def close(self):
        # Recording belongs to the simulation context and survives this window.
        self._closed = True
        self._success_callback = None
        if self._poll_job is not None:
            self.window.after_cancel(self._poll_job)
        if getattr(self.ctx, "_learned_har_window", None) is self:
            self.ctx._learned_har_window = None
        self.window.destroy()


def open_learned_har(ctx):
    existing = getattr(ctx, "_learned_har_window", None)
    if existing is not None and existing.window.winfo_exists():
        existing.window.lift()
        return existing
    window = LearnedHARWindow(ctx)
    ctx._learned_har_window = window
    return window
