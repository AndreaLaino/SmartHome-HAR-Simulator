"""Editors for weighted object capabilities and explainable activity rules."""

import copy
import tkinter as tk
from tkinter import ttk

from activity_recognition import recognition_settings
from device_properties import format_properties, parse_properties, validate_weight
from app.ui.theme import apply_theme_tree


def _settings_for_house(ctx):
    from recognition_library import context_runtime, synchronize_runtime
    return recognition_settings(synchronize_runtime(context_runtime(ctx)))


def open_properties_editor(ctx, obj, kind):
    from app.ui.device_inspector import current_devices, current_sensors

    window = tk.Toplevel(ctx.window)
    window.title(f"Properties: {obj.name}")
    body = tk.Frame(window, padx=16, pady=16)
    body.pack(fill=tk.BOTH, expand=True)
    tk.Label(body, text=f"{obj.name} · {obj.type}", anchor="w").pack(fill=tk.X)
    tk.Label(body, text="Properties and weights (0–1)", anchor="w").pack(fill=tk.X, pady=(12, 4))
    properties = tk.Entry(body, width=70)
    properties.pack(fill=tk.X)
    properties.insert(0, format_properties(obj.properties))
    from recognition_library import load_library
    try:
        shared = load_library()
        profiles = {key: value for key, value in shared["profiles"].items() if value["kind"] == kind}
    except (OSError, ValueError):
        shared, profiles = None, {}
    tk.Label(body, text="Property profile (shared profiles follow library updates)", anchor="w").pack(fill=tk.X, pady=(8, 4))
    profile_choice = ttk.Combobox(body, values=["Local properties", *profiles], state="readonly")
    current_profile = getattr(obj, "recognition_profile", None)
    profile_choice.set(current_profile if current_profile in profiles else "Local properties")
    profile_choice.pack(fill=tk.X)
    tk.Label(body, text="Example: cooking=1, heating=0.8 or working=0.85, relaxing=0.55.\n"
             "Names are shared across houses. A weight of 0 disables that property.",
             justify=tk.LEFT, anchor="w").pack(fill=tk.X, pady=8)
    tk.Label(body, text="Recognition weight for this object (0 disables it)", anchor="w").pack(fill=tk.X)
    reliability = tk.Spinbox(body, from_=0, to=1, increment=.05, width=12)
    reliability.delete(0, tk.END)
    reliability.insert(0, f"{obj.recognition_weight:g}")
    reliability.pack(anchor="w", pady=4)
    association = None
    if kind == "sensor":
        tk.Label(body, text="Observed device / fixture (optional except for Smart Meters)", anchor="w").pack(fill=tk.X, pady=(10, 4))
        association = ttk.Combobox(body, values=["", *(device.name for device in current_devices(ctx))], state="readonly")
        association.set(obj.associated_device or "")
        association.pack(fill=tk.X)
    status = tk.Label(body, text="", anchor="w", fg="#b42318")
    status.pack(fill=tk.X, pady=8)

    def select_profile(_event=None):
        profile = profiles.get(profile_choice.get())
        properties.configure(state="normal")
        reliability.configure(state="normal")
        if profile is not None:
            properties.delete(0, tk.END)
            properties.insert(0, format_properties(profile["properties"]))
            reliability.delete(0, tk.END)
            reliability.insert(0, str(profile["recognition_weight"]))
            properties.configure(state="readonly")
            reliability.configure(state="readonly")

    profile_choice.bind("<<ComboboxSelected>>", select_profile)
    if current_profile in profiles:
        select_profile()

    def save():
        source = current_sensors(ctx) if kind == "sensor" else current_devices(ctx)
        if not any(value is obj for value in source):
            status.configure(text="This object is no longer in the current house.")
            return
        try:
            updated = parse_properties(properties.get())
            weight = validate_weight(reliability.get())
            if association is not None and obj.type == "Smart Meter" and not association.get():
                raise ValueError("Select the device observed by this Smart Meter.")
        except ValueError as error:
            status.configure(text=str(error))
            return
        obj.properties = updated
        obj.recognition_weight = weight
        selected_profile = profile_choice.get()
        obj.recognition_profile = selected_profile if selected_profile in profiles else "local"
        if obj.recognition_profile != "local":
            ctx.recognition_library_link.update({"enabled": True, "library_id": shared["library_id"]})
        if association is not None:
            obj.associated_device = association.get() or None
        ctx._movement_redo_stack = []
        window.destroy()

    actions = tk.Frame(body)
    actions.pack(fill=tk.X)
    tk.Button(actions, text="Apply", command=save).pack(side=tk.RIGHT)
    tk.Button(actions, text="Cancel", command=window.destroy).pack(side=tk.RIGHT, padx=8)
    window._property_entry = properties
    window._recognition_weight_entry = reliability
    window._property_profile_choice = profile_choice
    apply_theme_tree(window, "light")
    return window


def open_activity_recognition(ctx):
    previous = getattr(ctx, "_recognition_window", None)
    if previous is not None and previous.winfo_exists():
        previous.lift()
        return previous
    window = tk.Toplevel(ctx.window)
    ctx._recognition_window = window
    window.title("Activity recognition")
    window.geometry("1080x720")
    window.minsize(820, 600)
    body = tk.Frame(window, padx=14, pady=12)
    body.pack(fill=tk.BOTH, expand=True)
    tk.Label(body, text="Confidence combines rule support and available evidence; inferred activities receive a penalty. It is not a calibrated probability.",
             anchor="w", wraplength=1000).pack(fill=tk.X)
    tk.Label(body, text="Cooking → relaxing infers eating while relaxing. Sleep requires bedroom presence, bed weight, all lights OFF and zero light power.",
             anchor="w", wraplength=1000).pack(fill=tk.X, pady=(4, 10))
    tree = ttk.Treeview(body, columns=("score", "confidence", "status", "evidence", "sensors"), height=12)
    tree.heading("#0", text="Activity")
    tree.column("#0", width=180)
    for name, title, width in (("score", "Support", 75), ("confidence", "Confidence*", 90), ("status", "Status", 110),
                               ("evidence", "Evidence / missing conditions", 320), ("sensors", "Sensors / context", 180)):
        tree.heading(name, text=title)
        tree.column(name, width=width)
    tree.pack(fill=tk.BOTH, expand=True)
    editor = tk.LabelFrame(body, text="Activity rules · changes are saved with the house", padx=10, pady=10)
    editor.pack(fill=tk.X, pady=10)
    editor.columnconfigure(1, weight=1)
    entries = {}
    for row, (key, title) in enumerate((("name", "Activity (select or enter a new name)"),
                                       ("weights", "Evidence weights"), ("threshold", "Recognition threshold (0–1)"),
                                       ("weight", "Activity weight (0–1; 0 disables it)"),
                                       ("meal_window_minutes", "Cooking → eating window (simulated minutes)"),
                                       ("sleep_minutes", "Continuous sleep evidence (simulated minutes)"))):
        tk.Label(editor, text=title, anchor="w").grid(row=row, column=0, sticky="w", pady=3)
        entry = ttk.Combobox(editor) if key == "name" else tk.Entry(editor)
        entry.grid(row=row, column=1, sticky="ew", padx=8, pady=3)
        entries[key] = entry

    def set_entry(key, value):
        entries[key].delete(0, tk.END)
        entries[key].insert(0, str(value))

    def load_rule(_event=None):
        settings = _settings_for_house(ctx)
        rule = settings["rules"].get(entries["name"].get())
        if rule is not None:
            set_entry("weights", format_properties(rule["weights"]))
            set_entry("threshold", rule["threshold"])
            set_entry("weight", rule["weight"])

    settings = _settings_for_house(ctx)
    entries["name"].configure(values=list(settings["rules"]))
    entries["name"].set("eating while relaxing")
    for key in ("meal_window_minutes", "sleep_minutes"):
        set_entry(key, settings[key])
    entries["name"].bind("<<ComboboxSelected>>", load_rule)
    load_rule()
    status = tk.Label(body, text="", anchor="w")
    status.pack(fill=tk.X)

    def apply():
        try:
            config = copy.deepcopy(ctx.recognition_config)
            name = entries["name"].get().strip()
            if not name:
                raise ValueError("Enter an activity name.")
            config.setdefault("rules", {})[name] = {
                "weights": parse_properties(entries["weights"].get()),
                "threshold": validate_weight(entries["threshold"].get()),
                "weight": validate_weight(entries["weight"].get()),
            }
            for key in ("meal_window_minutes", "sleep_minutes"):
                config[key] = float(entries[key].get())
            checked = recognition_settings(config)
        except ValueError as error:
            status.configure(text=str(error), fg="#b42318")
            return
        ctx.recognition_config.clear()
        ctx.recognition_config.update(config)
        entries["name"].configure(values=list(checked["rules"]))
        status.configure(text="Applied. Save the house to keep these settings.", fg="#166534")

    actions = tk.Frame(body)
    actions.pack(fill=tk.X)
    tk.Button(actions, text="Apply rule and timing", command=apply).pack(side=tk.LEFT)
    def open_shared():
        from app.ui.recognition_library_ui import open_recognition_library
        open_recognition_library(ctx)
    tk.Button(actions, text="Shared library / transfer / accuracy…", command=open_shared).pack(side=tk.LEFT, padx=8)
    def use_shared_rule():
        name = entries["name"].get()
        ctx.recognition_config.get("rules", {}).pop(name, None)
        load_rule()
        status.configure(text="House override removed; the shared/default rule is in use.", fg="#166534")
    tk.Button(actions, text="Use shared rule", command=use_shared_rule).pack(side=tk.LEFT)
    tk.Button(actions, text="Close", command=window.destroy).pack(side=tk.RIGHT)
    job = None

    def refresh():
        nonlocal job
        results = ctx.house_state.activity_state().get("recognition_results", {})
        try:
            rules = _settings_for_house(ctx)["rules"]
        except (OSError, ValueError) as error:
            status.configure(text=str(error), fg="#b42318")
            rules = {}
        for name in rules:
            result = results.get(name, {})
            score = f"{result['score']:.0%}" if "score" in result else "—"
            state = "Recognized" if result.get("active") else "Waiting" if result else "No observations"
            if result.get("active") and result.get("inferred"):
                state = "Inferred"
            evidence = "; ".join(result.get("evidence", []))
            if result.get("missing"):
                evidence += " | Needs: " + ", ".join(result["missing"])
            if result.get("suppressed_by"):
                state = "Included"
                evidence += " | Included in: " + result["suppressed_by"]
            if result.get("abstained"):
                state = "Low confidence"
            confidence = f"{result['confidence']:.0%}" if "confidence" in result else "—"
            values = (score, confidence, state, evidence, ", ".join(result.get("sources", [])))
            if tree.exists(name):
                tree.item(name, values=values)
            else:
                tree.insert("", tk.END, iid=name, text=name, values=values)
        job = window.after(500, refresh)

    def close(event):
        if event.widget is window:
            if job is not None:
                window.after_cancel(job)
            ctx._recognition_window = None

    window.bind("<Destroy>", close, add="+")
    window._recognition_entries = entries
    window._recognition_tree = tree
    refresh()
    apply_theme_tree(window, "light")
    return window
