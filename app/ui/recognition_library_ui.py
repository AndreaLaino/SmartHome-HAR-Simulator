"""Shared property model, cross-house transfer and labelled evaluation controls."""

import copy
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import recognition_library as store
from activity_recognition import recognition_settings
from device_properties import format_properties, parse_properties, validate_weight
from recognition_evaluation import evaluate_library, label_current_frame, transfer_report
from app.ui.theme import apply_theme_tree
from app.ui.reference_rules import ReferenceRulesEditor


def open_recognition_library(ctx):
    """House-oriented entry point; the complete editor remains under Advanced."""
    previous = getattr(ctx, "_guided_recognition_library_window", None)
    if previous is not None and previous.winfo_exists():
        previous.lift()
        previous._reload_library()
        return previous
    store.ensure_library()
    window = tk.Toplevel(ctx.window)
    ctx._guided_recognition_library_window = window
    window.title("Shared recognition library")
    window.geometry("1000x660")
    window.minsize(880, 600)
    body = tk.Frame(window, padx=16, pady=14)
    body.pack(fill=tk.BOTH, expand=True)
    tk.Label(body, text="Choose a reference house, register the current house, then associate compatible devices.",
             anchor="w", justify=tk.LEFT, wraplength=800).pack(fill=tk.X)
    current = tk.LabelFrame(body, text="Current house", padx=10, pady=10)
    current.pack(fill=tk.X, pady=10)
    current.columnconfigure(1, weight=1)
    tk.Label(current, text="House name:").grid(row=0, column=0, sticky="w")
    label = tk.Entry(current, width=28)
    label.insert(0, ctx.recognition_library_link.get("label") or Path(ctx.current_file or "House").stem)
    label.grid(row=0, column=1, sticky="ew", padx=8)
    model = tk.StringVar(window)
    tk.Label(current, textvariable=model, anchor="w", wraplength=400).grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
    reference = tk.StringVar(window)
    tk.Label(body, textvariable=reference, anchor="w", wraplength=800).pack(fill=tk.X)
    houses_box = tk.LabelFrame(body, text="Registered houses", padx=8, pady=8)
    houses_box.pack(fill=tk.BOTH, expand=True, pady=8)
    houses_box.rowconfigure(0, weight=1)
    houses_box.columnconfigure(0, weight=1)
    houses = ttk.Treeview(houses_box, columns=("role", "devices", "sensors"), selectmode="browse", height=6)
    houses.heading("#0", text="House")
    houses.column("#0", width=320, minwidth=180)
    for key, title, width in (("role", "Role", 230), ("devices", "Devices", 80), ("sensors", "Sensors", 80)):
        houses.heading(key, text=title)
        houses.column(key, width=width, minwidth=70)
    houses.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(houses_box, orient=tk.VERTICAL, command=houses.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    houses.configure(yscrollcommand=scrollbar.set)
    tk.Label(body, text="Choosing a reference house selects source objects for existing shared associations. "
             "It does not publish that house's local rules; use Advanced… to publish them.\n"
             "Save each scenario to keep its house identity and links. Local recognition settings take priority over shared settings.",
             anchor="w", justify=tk.LEFT, wraplength=800).pack(fill=tk.X, pady=4)
    status = tk.StringVar(window)
    tk.Label(body, textvariable=status, anchor="w", justify=tk.LEFT, wraplength=800).pack(fill=tk.X, pady=4)
    current_identity = (ctx.recognition_library_link.get("house_id"), ctx.recognition_library_link.get("label"))

    def refresh():
        nonlocal current_identity
        shared = store.load_library()
        snapshots = [store.read_house(path.stem) for path in (store.LIBRARY_DIR / "houses").glob("*.json")]
        selected = houses.selection()
        houses.delete(*houses.get_children())
        reference_id = shared.get("reference_house_id")
        reference_name = "Not set" if not reference_id else "Unavailable (register/update the reference house)"
        for snapshot in sorted(snapshots, key=lambda item: item["label"].casefold()):
            house_id = snapshot["house_id"]
            roles = []
            if house_id == reference_id:
                reference_name = snapshot["label"]
                roles.append("Reference")
            if house_id == ctx.recognition_library_link.get("house_id"):
                roles.append("Current house")
            houses.insert("", tk.END, iid=house_id, text=snapshot["label"],
                          values=(" / ".join(roles) or "Destination", len(snapshot["devices"]), len(snapshot["sensors"])))
        if selected and houses.exists(selected[0]):
            houses.selection_set(selected[0])
        reference.set(f"Reference house: {reference_name}")
        link = ctx.recognition_library_link
        identity = (link.get("house_id"), link.get("label"))
        if identity != current_identity:
            label.delete(0, tk.END)
            label.insert(0, link.get("label") or Path(ctx.current_file or "House").stem)
            current_identity = identity
        model.set(f"{'Registered' if link.get('house_id') else 'Not registered'} · "
                  f"Model: {'shared' if link.get('enabled') else 'local'}")

    def action(callback):
        def invoke():
            try:
                message = callback()
                refresh()
                if message:
                    status.set(message)
            except (OSError, ValueError, KeyError, TypeError) as error:
                status.set(str(error))
        return invoke

    def register(*, new=False, reference=False):
        store.register_house(ctx, label.get(), new=new, reference=reference)
        return "House registered. Save the scenario to keep its identity."

    def select_reference():
        if not houses.selection():
            raise ValueError("Select a registered house first.")
        store.set_reference_house(houses.selection()[0])
        return "Reference house selected. Its local rules have not been published."

    def connect():
        store.connect_house(ctx)
        return "Connected to the shared model. Existing local rules and custom object properties are preserved."

    for text, callback, row, column in (
            ("Register / update house", lambda: register(), 0, 2),
            ("Register as a new house", lambda: register(new=True), 0, 3),
            ("Connect shared model", connect, 1, 2),
            ("Set current as reference", lambda: register(reference=True), 1, 3)):
        tk.Button(current, text=text, command=action(callback)).grid(row=row, column=column, padx=(6, 0), pady=3, sticky="ew")
    tk.Button(houses_box, text="Use selected house as reference", command=action(select_reference)).grid(
        row=1, column=0, sticky="w", pady=(8, 0))

    def advanced():
        editor = open_advanced_recognition_library(ctx)
        editor.bind("<Destroy>", lambda event: action(lambda: None)()
                    if event.widget is editor and window.winfo_exists() else None, add="+")
        return editor

    from app.ui.reference_rules import open_reference_rules
    bottom = tk.Frame(body)
    bottom.pack(fill=tk.X, pady=(6, 0))
    tk.Button(bottom, text="Reference device rules…", command=lambda: open_reference_rules(ctx)).pack(side=tk.LEFT)
    tk.Button(bottom, text="Advanced…", command=advanced).pack(side=tk.LEFT, padx=8)
    tk.Button(bottom, text="Refresh", command=action(lambda: None)).pack(side=tk.LEFT)
    tk.Button(bottom, text="Close", command=window.destroy).pack(side=tk.RIGHT)
    window._house_name = label
    window._houses_tree = houses
    window._reference_summary = reference
    window._library_status = status
    window._open_advanced_library = advanced
    window._reload_library = action(lambda: None)
    window._reload_library()
    apply_theme_tree(window, "light")
    return window


def open_advanced_recognition_library(ctx):
    previous = getattr(ctx, "_recognition_library_window", None)
    if previous is not None and previous.winfo_exists():
        previous.lift()
        return previous
    shared = store.ensure_library()
    window = tk.Toplevel(ctx.window)
    ctx._recognition_library_window = window
    window.title("Shared recognition library — Advanced")
    window.geometry("1180x820")
    window.minsize(940, 720)
    body = tk.Frame(window, padx=12, pady=10)
    body.pack(fill=tk.BOTH, expand=True)
    tk.Label(body, text="Recognition transfers known reference-object roles through weighted property matches. Held-out houses measure transfer accuracy.",
             anchor="w", wraplength=1100).pack(fill=tk.X)
    path_text = tk.Entry(body, readonlybackground="white")
    path_text.insert(0, str(store.LIBRARY_DIR))
    path_text.configure(state="readonly")
    path_text.pack(fill=tk.X, pady=4)
    summary = tk.StringVar(window)
    tk.Label(body, textvariable=summary, anchor="w", wraplength=1100).pack(fill=tk.X, pady=4)
    row = tk.Frame(body)
    row.pack(fill=tk.X)
    tk.Label(row, text="House name:").pack(side=tk.LEFT)
    label = tk.Entry(row, width=28)
    label.insert(0, ctx.recognition_library_link.get("label") or Path(ctx.current_file or "House").stem)
    label.pack(side=tk.LEFT, padx=6)
    keep_overrides = tk.BooleanVar(window, value=True)
    tk.Checkbutton(row, text="Keep house model overrides when connecting", variable=keep_overrides).pack(side=tk.LEFT)
    house_actions = tk.Frame(body)
    house_actions.pack(fill=tk.X, pady=6)
    tabs = ttk.Notebook(body)
    tabs.pack(fill=tk.BOTH, expand=True)
    model_tab, transfer_tab, validation_tab = (tk.Frame(tabs, padx=10, pady=10) for _ in range(3))
    for frame, title in ((model_tab, "Shared model"), (transfer_tab, "Transfer report"), (validation_tab, "Accuracy / ground truth")):
        tabs.add(frame, text=title)
    references_tab = tk.Frame(tabs, padx=10, pady=10)
    tabs.add(references_tab, text="Reference rules")
    status = tk.StringVar(window)
    tk.Label(body, textvariable=status, anchor="w", justify=tk.LEFT, wraplength=1100).pack(fill=tk.X, pady=(8, 0))

    def overview():
        link = ctx.recognition_library_link
        name = "None"
        if shared.get("reference_house_id"):
            try:
                name = store.read_house(shared["reference_house_id"])["label"]
            except (OSError, ValueError):
                name = "Unavailable (register/update the reference house)"
        summary.set(f"Library revision {shared['revision']} · Reference: {name} · Current house: "
                    f"{link.get('house_id', 'not registered')} · Shared model: {'connected' if link.get('enabled') else 'local'} · "
                    f"House rule overrides: {len(ctx.recognition_config.get('rules', {}))} · "
                    f"Local reference associations: {'yes' if 'reference_bindings' in ctx.recognition_config else 'no'}")

    def action(callback):
        def invoke():
            nonlocal shared
            try:
                old_revision = shared["revision"]
                message = callback()
                shared = store.load_library()
                overview()
                selected = {labels.get(index) for index in labels.curselection()}
                labels.delete(0, tk.END)
                for name in shared["recognition_config"]["rules"]:
                    labels.insert(tk.END, name)
                    if name in selected:
                        labels.selection_set(tk.END)
                if old_revision != shared["revision"]:
                    reference_editor.reload()
                    metrics.set("Shared model/reference changed. Previous metrics are stale; re-evaluate held-out runs.")
                    metric_tree.delete(*metric_tree.get_children())
                    transfer_tree.delete(*transfer_tree.get_children())
                    transfer_summary.set("Shared model changed. Recompute the transfer report.")
                if message:
                    status.set(message)
            except (OSError, ValueError, KeyError, TypeError) as error:
                status.set(str(error))
        return invoke

    def register(*, new=False, reference=False):
        store.register_house(ctx, label.get(), reference=reference, new=new)
        partition.set("Reference" if reference else "Held-out test")
        return "House registered. Save its scenario CSV to keep its identity and shared profile links."

    def connect():
        store.connect_house(ctx)
        if not keep_overrides.get():
            ctx.recognition_config.clear()
        return "Connected to shared profiles and rules. Custom object properties stay local unless you select a shared profile."

    def detach():
        store.connect_house(ctx, enabled=False)
        return "Disconnected. Current effective properties and rules are now frozen locally."

    for text, callback in (("Register / update house", lambda: register()),
                           ("Register as a new house", lambda: register(new=True)),
                           ("Set as reference house", lambda: register(reference=True)),
                           ("Connect shared model", connect), ("Disconnect", detach)):
        tk.Button(house_actions, text=text, command=action(callback)).pack(side=tk.LEFT, padx=(0, 6))

    profiles_box = tk.LabelFrame(model_tab, text="Reusable weighted property profiles", padx=10, pady=8)
    profiles_box.pack(fill=tk.X)
    profile = ttk.Combobox(profiles_box, values=list(shared["profiles"]), width=45)
    profile.grid(row=0, column=1, sticky="ew", pady=3)
    tk.Label(profiles_box, text="Profile key (e.g. device:Air fryer)").grid(row=0, column=0, sticky="w")
    profile_weights = tk.Entry(profiles_box, width=65)
    profile_weights.grid(row=1, column=1, sticky="ew", pady=3)
    tk.Label(profiles_box, text="Properties / weights (0–1)").grid(row=1, column=0, sticky="w")
    reliability = tk.Entry(profiles_box)
    reliability.grid(row=2, column=1, sticky="ew", pady=3)
    tk.Label(profiles_box, text="Object recognition weight (0–1)").grid(row=2, column=0, sticky="w")
    profiles_box.columnconfigure(1, weight=1)

    def replace(entry, value):
        entry.delete(0, tk.END)
        entry.insert(0, str(value))

    def load_profile(_event=None):
        item = shared["profiles"].get(profile.get())
        if item:
            replace(profile_weights, format_properties(item["properties"]))
            replace(reliability, item["recognition_weight"])

    def save_profile():
        nonlocal shared
        key = profile.get().strip()
        kind, separator, name = key.partition(":")
        if kind not in {"device", "sensor"} or not separator or not name.strip():
            raise ValueError("Use device:Profile name or sensor:Profile name.")
        updated = copy.deepcopy(shared)
        updated["profiles"][key] = {"kind": kind, "label": name, "properties": parse_properties(profile_weights.get()),
                                     "recognition_weight": validate_weight(reliability.get())}
        shared = store.save_library(updated, shared["revision"])
        profile.configure(values=list(shared["profiles"]))
        return "Shared profile saved. Connected objects using it follow this revision; old accuracy reports need re-evaluation."

    profile.bind("<<ComboboxSelected>>", load_profile)
    profile.set("device:Microwave")
    load_profile()
    tk.Button(profiles_box, text="Save shared profile", command=action(save_profile)).grid(row=3, column=1, sticky="w", pady=4)

    rules_box = tk.LabelFrame(model_tab, text="Shared activity model", padx=10, pady=8)
    rules_box.pack(fill=tk.X, pady=10)
    rules_box.columnconfigure(1, weight=1)
    fields = {}
    for row_index, (key, text) in enumerate((("name", "Activity"), ("weights", "Evidence weights"),
                                            ("threshold", "Rule threshold (0–1)"), ("weight", "Activity weight (0–1)"),
                                            ("minimum_confidence", "Minimum confidence; abstain below (0–1)"),
                                            ("meal_window_minutes", "Cooking memory (simulated minutes)"),
                                            ("sleep_minutes", "Continuous sleep evidence (simulated minutes)"))):
        tk.Label(rules_box, text=text).grid(row=row_index, column=0, sticky="w", pady=3)
        entry = ttk.Combobox(rules_box) if key == "name" else tk.Entry(rules_box)
        entry.grid(row=row_index, column=1, sticky="ew", pady=3)
        fields[key] = entry

    def load_rule(_event=None):
        settings = recognition_settings(shared["recognition_config"])
        rule = settings["rules"].get(fields["name"].get())
        if rule:
            replace(fields["weights"], format_properties(rule["weights"]))
            replace(fields["threshold"], rule["threshold"])
            replace(fields["weight"], rule["weight"])
        for key in ("minimum_confidence", "meal_window_minutes", "sleep_minutes"):
            replace(fields[key], settings[key])

    def save_rule():
        nonlocal shared
        updated = copy.deepcopy(shared)
        name = fields["name"].get().strip()
        if not name:
            raise ValueError("Enter an activity name.")
        config = updated["recognition_config"]
        config.setdefault("rules", {})[name] = {"weights": parse_properties(fields["weights"].get()),
                                                "threshold": validate_weight(fields["threshold"].get()),
                                                "weight": validate_weight(fields["weight"].get())}
        for key in ("minimum_confidence", "meal_window_minutes", "sleep_minutes"):
            config[key] = float(fields[key].get())
        updated["recognition_config"] = recognition_settings(config)
        shared = store.save_library(updated, shared["revision"])
        fields["name"].configure(values=list(config["rules"]))
        return "Shared model saved. Reference and target houses can now be evaluated using the same rules."

    def publish_rules():
        nonlocal shared
        updated = copy.deepcopy(shared)
        updated["recognition_config"] = recognition_settings(store.synchronize_runtime(store.context_runtime(ctx)))
        shared = store.save_library(updated, shared["revision"])
        fields["name"].configure(values=list(shared["recognition_config"]["rules"]))
        load_rule()
        return "Current house's effective rules published to the shared model. Property profiles are edited separately above."

    fields["name"].configure(values=list(shared["recognition_config"]["rules"]))
    fields["name"].set("eating while relaxing")
    fields["name"].bind("<<ComboboxSelected>>", load_rule)
    load_rule()
    rule_actions = tk.Frame(rules_box)
    rule_actions.grid(row=7, column=1, sticky="w", pady=4)
    tk.Button(rule_actions, text="Save shared rule", command=action(save_rule)).pack(side=tk.LEFT)
    tk.Button(rule_actions, text="Publish current house rules", command=action(publish_rules)).pack(side=tk.LEFT, padx=8)

    def reference_sources():
        if shared.get("reference_house_id"):
            try:
                source_house = store.read_house(shared["reference_house_id"])
            except (OSError, ValueError):
                source_house = None
            if source_house:
                for kind, objects in (("device", source_house["devices"]), ("sensor", source_house["sensors"])):
                    for obj in objects:
                        yield kind, f"Reference house: {obj['name']} ({obj['type']})", {
                            "name": obj["name"], "type": obj["type"], "properties": copy.deepcopy(obj["properties"]),
                            "recognition_weight": obj["recognition_weight"], "house_id": source_house["house_id"]}
        for key, item in shared["profiles"].items():
            yield item["kind"], f"Catalogue: {key}", {"name": item["label"], "type": key.split(":", 1)[1],
                                                      "properties": copy.deepcopy(item["properties"]),
                                                      "recognition_weight": item["recognition_weight"], "house_id": None}

    def save_reference_bindings(bindings):
        nonlocal shared
        updated = copy.deepcopy(shared)
        updated["recognition_config"]["reference_bindings"] = bindings
        shared = store.save_library(updated, shared["revision"])

    reference_editor = ReferenceRulesEditor(references_tab, get_config=lambda: shared["recognition_config"],
                                            save_bindings=save_reference_bindings, get_sources=reference_sources, action=action)
    reference_editor.pack(fill=tk.BOTH, expand=True)

    transfer_summary = tk.StringVar(window, value="Compare this house with the registered reference. Coverage is not measured accuracy.")
    tk.Label(transfer_tab, textvariable=transfer_summary, anchor="w", wraplength=1080).pack(fill=tk.X)
    transfer_tree = ttk.Treeview(transfer_tab, columns=("reference", "coverage", "ready", "missing"), height=13)
    transfer_tree.heading("#0", text="Activity")
    for key, title, width in (("reference", "Reference supports", 110), ("coverage", "Property coverage", 120),
                               ("ready", "Observable here", 110), ("missing", "Missing evidence", 400)):
        transfer_tree.heading(key, text=title)
        transfer_tree.column(key, width=width)
    transfer_tree.pack(fill=tk.BOTH, expand=True, pady=8)
    matches = tk.Text(transfer_tab, height=6, wrap=tk.WORD)
    matches.pack(fill=tk.X)

    def compare():
        try:
            reference = store.read_house(shared["reference_house_id"]) if shared.get("reference_house_id") else None
        except (OSError, ValueError):
            reference = None
        runtime = store.context_runtime(ctx)
        target = store.house_snapshot(runtime, ctx.recognition_library_link.get("house_id", "preview"), label.get() or "Current house")
        report = transfer_report(reference, target, shared["recognition_config"])
        transfer_tree.delete(*transfer_tree.get_children())
        for name, result in report["activities"].items():
            transfer_tree.insert("", tk.END, iid=name, text=name,
                                 values=("Yes" if result["supported_in_reference"] else "No", f"{result['property_coverage']:.0%}",
                                         "Yes" if result["transferable"] else "No", ", ".join(
                                             [*result["missing"], *result.get("missing_components", [])])))
        matches.delete("1.0", tk.END)
        for item in report["device_equivalents"]:
            matches.insert(tk.END, f"{item['reference_device']} ({item['reference_type']}) → {item['target_device']} "
                           f"({item['target_type']}), role '{item['role']}': {item['property_match']:.0%} relevant-property match\n")
        transfer_summary.set(f"Reference: {report['reference_house'] or 'not set'} → {report['target_house']}. {report['note']}")
        store.atomic_json(store.LIBRARY_DIR / "reports" / "transfer.json", report)
        return "Transfer report saved. Layout and device names are never copied from the reference house."

    tk.Button(transfer_tab, text="Compare properties and sensor coverage", command=action(compare)).pack(anchor="w", pady=8)

    tk.Label(validation_tab, text="Select the activities actually occurring, then label the current simulation frame. Predictions are never used as ground truth.\n"
             "Reference-house runs are excluded from held-out accuracy. Unlabelled context preserves sequence history but is not scored.",
             anchor="w", justify=tk.LEFT, wraplength=1060).pack(fill=tk.X)
    labels = tk.Listbox(validation_tab, selectmode=tk.EXTENDED, exportselection=False, height=4)
    for name in shared["recognition_config"]["rules"]:
        labels.insert(tk.END, name)
    labels.pack(fill=tk.X, pady=6)
    none = tk.BooleanVar(window, value=False)
    tk.Checkbutton(validation_tab, text="Ground truth: no activity", variable=none).pack(anchor="w")
    is_reference = bool(ctx.recognition_library_link.get("house_id")) and ctx.recognition_library_link.get("house_id") == shared.get("reference_house_id")
    partition = tk.StringVar(window, value="Reference" if is_reference else "Held-out test")
    ttk.Combobox(validation_tab, textvariable=partition, values=("Reference", "Held-out test"), state="readonly").pack(anchor="w", pady=6)

    def record():
        selected = [labels.get(index) for index in labels.curselection()]
        if not selected and not none.get():
            raise ValueError("Select ground-truth activities, or explicitly check No activity.")
        if selected and none.get():
            raise ValueError("Choose activity labels or No activity, not both.")
        path = label_current_frame(ctx, selected, partition="reference" if partition.get() == "Reference" else "test")
        metrics.set("New ground-truth labels recorded. Re-evaluate to update measured accuracy.")
        return f"Human-labelled frame saved in {path.name}. Reference data is kept separate from held-out evaluation."

    tk.Button(validation_tab, text="Record human-labelled frame", command=action(record)).pack(anchor="w")
    metrics = tk.StringVar(window, value="Measured accuracy: not evaluated. No labelled held-out examples have been evaluated in this view.")
    tk.Label(validation_tab, textvariable=metrics, anchor="w", justify=tk.LEFT, wraplength=1060).pack(fill=tk.X, pady=8)
    metric_tree = ttk.Treeview(validation_tab, columns=("precision", "recall", "f1", "support"), height=6)
    metric_tree.heading("#0", text="Activity")
    for key, title in (("precision", "Precision"), ("recall", "Recall"), ("f1", "F1"), ("support", "Labelled positives")):
        metric_tree.heading(key, text=title)
    metric_tree.pack(fill=tk.BOTH, expand=True)

    def evaluate():
        report = evaluate_library()
        fmt = lambda value: f"{value:.1%}" if value is not None else "N/A"
        metrics.set(f"Exact activity-set accuracy: {fmt(report['exact_match_accuracy'])} · Micro-F1: {fmt(report['micro_f1'])} · "
                    f"{report['labelled_samples']} labelled frames, {report['test_houses']} held-out houses. "
                    f"{len(report['excluded_reference_runs'])} reference runs excluded. Shared revision {report['library_revision']}.")
        metric_tree.delete(*metric_tree.get_children())
        for name, item in report["per_activity"].items():
            metric_tree.insert("", tk.END, text=name, values=(fmt(item["precision"]), fmt(item["recall"]), fmt(item["f1"]), item["support"]))
        return "Evaluation saved in reports/evaluation.json. These metrics apply to the labelled sample, not a guarantee for unseen homes."

    tk.Button(validation_tab, text="Evaluate current shared model on held-out houses", command=action(evaluate)).pack(anchor="w", pady=6)
    def open_experiment():
        from app.ui.har_experiment import open_har_experiment
        open_har_experiment(ctx)
    tk.Button(validation_tab, text="Train / compare source and target houses…", command=open_experiment).pack(anchor="w")

    def reload():
        nonlocal shared
        shared = store.load_library()
        profile.configure(values=list(shared["profiles"]))
        fields["name"].configure(values=list(shared["recognition_config"]["rules"]))
        load_profile()
        load_rule()
        reference_editor.reload()
        return "Shared library reloaded."

    bottom = tk.Frame(body)
    bottom.pack(fill=tk.X, pady=(8, 0))
    tk.Button(bottom, text="Reload shared library", command=action(reload)).pack(side=tk.LEFT)
    tk.Button(bottom, text="Close", command=window.destroy).pack(side=tk.RIGHT)
    window.bind("<Destroy>", lambda event: setattr(ctx, "_recognition_library_window", None) if event.widget is window else None, add="+")
    window._shared_profile_choice = profile
    window._reference_editor = reference_editor
    window._library_tabs = tabs
    window._shared_profile_weights = profile_weights
    window._shared_rule_fields = fields
    window._transfer_tree = transfer_tree
    window._ground_truth_labels = labels
    window._ground_truth_none = none
    window._validation_partition = partition
    window._metric_summary = metrics
    window._house_name = label
    window._library_status = status
    overview()
    apply_theme_tree(window, "light")
    return window
