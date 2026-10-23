"""Editors for explicit source-object associations used during recognition."""

import copy
import json
import tkinter as tk
from tkinter import ttk

from activity_recognition import recognition_settings
from device_properties import format_properties, parse_properties, property_key, validate_weight
from reference_transfer import (anchor_reference_bindings, compatible_reference_activities,
                                import_compatible_references, match_reference_roles, validate_reference_bindings)
from app.ui.theme import apply_theme_tree


class ReferenceRulesEditor(tk.Frame):
    def __init__(self, parent, *, get_config, save_bindings, get_sources, action):
        super().__init__(parent)
        self.get_config = get_config
        self.save_bindings = save_bindings
        self.get_sources = get_sources
        self.sources = {}
        self.reference = None
        tk.Label(self, text="A known reference object supplies a rule role. Observed targets fill that role only after a property match.\n"
                 "Example: Oven → cooking is stored here; Microwave → cooking is derived at runtime. Removing the oven association blocks that transfer.",
                 justify=tk.LEFT, anchor="w", wraplength=1060).pack(fill=tk.X, pady=(0, 8))
        form = tk.Frame(self)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)
        self.fields = {}
        definitions = (("id", "Reference rule ID", True), ("feature", "Evidence role (e.g. cooking)", False),
                       ("kind", "Observed object kind", True), ("source", "Known reference object", True),
                       ("properties", "Frozen reference properties", False),
                       ("weights", "Relevant property weights", False),
                       ("required", "Required target properties (comma separated)", False),
                       ("minimum_similarity", "Minimum property similarity (0–1)", False),
                       ("strength", "Reference evidence strength (0–1)", False),
                       ("weight", "Association weight (0 disables it)", False))
        for index, (key, label, combo) in enumerate(definitions):
            tk.Label(form, text=label, anchor="w").grid(row=index, column=0, sticky="w", pady=3)
            widget = ttk.Combobox(form) if combo else tk.Entry(form)
            widget.grid(row=index, column=1, sticky="ew", padx=(10, 0), pady=3)
            self.fields[key] = widget
        self.fields["kind"].configure(values=("device", "sensor"), state="readonly")
        self.fields["source"].configure(state="readonly")
        self.fields["id"].bind("<<ComboboxSelected>>", self.load_binding)
        self.fields["kind"].bind("<<ComboboxSelected>>", self.change_kind)
        self.fields["source"].bind("<<ComboboxSelected>>", self.select_source)
        tk.Label(self, text="Similarity is weighted coverage of the reference's relevant properties. Extra target properties do not lower it.\n"
                 "Keep an essential property required to prevent unrelated devices from matching through a generic capability such as heating.",
                 justify=tk.LEFT, anchor="w", wraplength=1060).pack(fill=tk.X, pady=8)
        buttons = tk.Frame(self)
        buttons.pack(fill=tk.X)
        tk.Button(buttons, text="Save reference rule", command=action(self.save)).pack(side=tk.LEFT)
        tk.Button(buttons, text="Delete reference rule", command=action(self.delete)).pack(side=tk.LEFT, padx=8)
        self.reload()

    def set_field(self, key, value):
        widget = self.fields[key]
        if isinstance(widget, ttk.Combobox):
            widget.set(str(value))
        else:
            widget.delete(0, tk.END)
            widget.insert(0, str(value))

    def load_sources(self):
        self.sources = {label: copy.deepcopy(reference) for kind, label, reference in self.get_sources()
                        if kind == self.fields["kind"].get()}
        if self.reference is not None:
            key = f"Stored reference: {self.reference['name']} ({self.reference['type']})"
            self.sources[key] = copy.deepcopy(self.reference)
        self.fields["source"].configure(values=list(self.sources))

    def change_kind(self, _event=None):
        self.reference = None
        self.fields["source"].set("")
        self.load_sources()

    def select_source(self, _event=None):
        self.reference = copy.deepcopy(self.sources.get(self.fields["source"].get()))
        if self.reference is None:
            return
        properties = self.reference["properties"]
        self.set_field("properties", format_properties(properties))
        feature = property_key(self.fields["feature"].get())
        weights = {feature: 1} if properties.get(feature, 0) > 0 else {prop: 1 for prop, weight in properties.items() if weight > 0}
        self.set_field("weights", format_properties(weights))
        self.set_field("required", feature if feature in weights else "")

    def load_binding(self, _event=None):
        bindings = recognition_settings(self.get_config())["reference_bindings"]
        binding = bindings.get(self.fields["id"].get())
        if binding is None:
            return
        self.reference = copy.deepcopy(binding["reference"])
        for field in ("feature", "kind", "minimum_similarity", "strength", "weight"):
            self.set_field(field, binding[field])
        self.set_field("properties", format_properties(self.reference["properties"]))
        self.set_field("weights", format_properties(binding["property_weights"]))
        self.set_field("required", ", ".join(binding["required_properties"]))
        self.load_sources()
        self.fields["source"].set(f"Stored reference: {self.reference['name']} ({self.reference['type']})")

    def reload(self):
        bindings = recognition_settings(self.get_config())["reference_bindings"]
        self.fields["id"].configure(values=list(bindings))
        selected = self.fields["id"].get()
        if selected not in bindings:
            selected = next(iter(bindings), "")
        self.fields["id"].set(selected)
        if selected:
            self.load_binding()
        else:
            self.reference = None
            for key in ("feature", "properties", "weights", "required"):
                self.set_field(key, "")
            self.set_field("kind", "device")
            self.set_field("minimum_similarity", .5)
            self.set_field("strength", 1)
            self.set_field("weight", 1)
            self.load_sources()
            self.fields["source"].set("")

    def save(self):
        key = self.fields["id"].get().strip()
        if not key or self.reference is None:
            raise ValueError("Enter a rule ID and select its known reference object.")
        bindings = copy.deepcopy(recognition_settings(self.get_config())["reference_bindings"])
        reference = copy.deepcopy(self.reference)
        reference["properties"] = parse_properties(self.fields["properties"].get())
        bindings[key] = {"feature": self.fields["feature"].get(), "kind": self.fields["kind"].get(),
                         "reference": reference, "property_weights": parse_properties(self.fields["weights"].get()),
                         "required_properties": [item.strip() for item in self.fields["required"].get().split(",") if item.strip()],
                         **{field: validate_weight(self.fields[field].get()) for field in ("minimum_similarity", "strength", "weight")}}
        self.save_bindings(validate_reference_bindings(bindings))
        self.reload()
        return "Reference association saved. Targets must match this reference before supplying its evidence role."

    def delete(self):
        bindings = copy.deepcopy(recognition_settings(self.get_config())["reference_bindings"])
        key = self.fields["id"].get()
        if key not in bindings:
            raise ValueError("Select an existing reference rule.")
        del bindings[key]
        self.save_bindings(bindings)
        self.reload()
        return "Reference association deleted. Its properties alone can no longer activate that evidence role."


def current_house_sources(ctx):
    from recognition_library import context_runtime
    runtime = context_runtime(ctx)
    for kind, objects in (("device", runtime["devices"]), ("sensor", runtime["sensors"])):
        for obj in objects:
            yield kind, f"Current house: {obj.name} ({obj.type})", {
                "name": obj.name, "type": obj.type, "properties": copy.deepcopy(obj.properties),
                "recognition_weight": obj.recognition_weight, "house_id": ctx.recognition_library_link.get("house_id")}


def open_advanced_reference_rules(ctx):
    from app.ui.activity_properties import _settings_for_house
    window = tk.Toplevel(ctx.window)
    window.title("Advanced reference device rules")
    window.geometry("1080x620")
    body = tk.Frame(window, padx=14, pady=12)
    body.pack(fill=tk.BOTH, expand=True)
    status = tk.StringVar(window)

    def action(callback):
        def invoke():
            try:
                status.set(callback())
            except (ValueError, OSError, KeyError, TypeError) as error:
                status.set(str(error))
        return invoke

    def save(bindings):
        ctx.recognition_config["reference_bindings"] = copy.deepcopy(bindings)

    editor = ReferenceRulesEditor(body, get_config=lambda: _settings_for_house(ctx), save_bindings=save,
                                  get_sources=lambda: current_house_sources(ctx), action=action)
    editor.pack(fill=tk.BOTH, expand=True)
    tk.Label(body, text="Changes are house overrides. Save the scenario CSV to keep them, or publish the house model to share them.", anchor="w").pack(fill=tk.X, pady=6)
    tk.Label(body, textvariable=status, anchor="w", wraplength=1020).pack(fill=tk.X)
    tk.Button(body, text="Close", command=window.destroy).pack(anchor="e")
    window._reference_editor = editor
    apply_theme_tree(window, "light")
    return window


def guided_reference_sources(ctx, config):
    """Available references, independent of the house's active associations.

    A house object is offered only if an existing association can be anchored to
    it. Its type or properties never invent a new activity association.
    Browsing catalogue references does not enable them in the current house.
    """
    from recognition_library import LIBRARY_DIR, context_runtime, load_library, read_house

    choices = {}
    unavailable = []

    def add(configuration, origin, *, actual=None):
        settings = recognition_settings(configuration)
        groups = {}
        for key, binding in settings["reference_bindings"].items():
            if binding["kind"] != "device":
                continue
            reference = binding["reference"]
            if actual is not None:
                obj = actual.get((reference["name"], reference["type"]))
                if (obj is None or reference["properties"] != obj["properties"]
                        or reference.get("recognition_weight", 1) != obj["recognition_weight"]):
                    continue
            identity = json.dumps(reference, sort_keys=True)
            groups.setdefault(identity, {})[key] = copy.deepcopy(binding)
        for bindings in groups.values():
            reference = next(iter(bindings.values()))["reference"]
            label = f"{origin}: {reference['name']} ({reference['type']})"
            unique = label
            suffix = 2
            while unique in choices:
                unique = f"{label} [{suffix}]"
                suffix += 1
            selected = copy.deepcopy(settings)
            selected["reference_bindings"] = bindings
            choices[unique] = selected

    def add_house(snapshot, origin):
        configuration = recognition_settings(snapshot.get("recognition_config", {}))
        configuration["reference_bindings"] = anchor_reference_bindings(
            configuration["reference_bindings"], snapshot)
        available = {(obj["name"], obj["type"]): obj for obj in snapshot["devices"]}
        # Unavailable roles retain their catalogue reference when anchoring.
        configuration["reference_bindings"] = {
            key: binding for key, binding in configuration["reference_bindings"].items()
            if binding["reference"].get("house_id") == snapshot["house_id"]}
        add(configuration, origin, actual=available)

    add(config, "Stored reference")
    # Available source devices are not limited to roles already imported into
    # this house. In particular, an oven-only scenario must still offer TV as a
    # source for the next association. Compatibility filters activities only.
    if (LIBRARY_DIR / "library.json").exists():
        try:
            add(load_library()["recognition_config"], "Shared catalogue")
        except (OSError, ValueError, KeyError, TypeError) as error:
            unavailable.append(f"Shared catalogue: {error}")
    else:
        add(recognition_settings(), "Built-in catalogue")
    runtime = context_runtime(ctx)
    snapshot = {"house_id": ctx.recognition_library_link.get("house_id"),
                "recognition_config": config,
                "devices": [dict(name=obj.name, type=obj.type, properties=copy.deepcopy(obj.properties),
                                 recognition_weight=obj.recognition_weight) for obj in runtime["devices"]],
                "sensors": [dict(name=obj.name, type=obj.type, properties=copy.deepcopy(obj.properties),
                                 recognition_weight=obj.recognition_weight) for obj in runtime["sensors"]]}
    add_house(snapshot, "Current house")
    for path in sorted((LIBRARY_DIR / "houses").glob("*.json")):
        try:
            house = read_house(path.stem)
            add_house(house, f"House {house['label']} [{path.stem[:8]}]")
        except (OSError, ValueError, KeyError, TypeError) as error:
            unavailable.append(f"{path.stem}: {error}")
    return choices, unavailable


class GuidedReferenceRulesEditor(tk.Frame):
    """Select a pair first; only compatible known activities can be checked."""

    def __init__(self, parent, ctx, *, advanced, connections_parent):
        super().__init__(parent)
        self.ctx = ctx
        self.sources = {}
        self.targets = {}
        self.options = {}
        self.activity_variables = {}
        self.status = tk.StringVar(self)
        self.selection_text = tk.StringVar(self, value="Select compatible activities…")
        tk.Label(self, text="Choose source and destination, then select compatible activities.",
                 anchor="w", justify=tk.LEFT, wraplength=1000).pack(fill=tk.X, pady=(0, 14))
        form = tk.Frame(self)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)
        self.fields = {}
        for row, (key, title) in enumerate((("source", "Source device / reference house"),
                                             ("target", "Destination device / current house"))):
            tk.Label(form, text=title, anchor="w").grid(row=row, column=0, sticky="w", pady=8)
            combo = ttk.Combobox(form, state="readonly", width=64)
            combo.grid(row=row, column=1, sticky="ew", padx=(14, 0), pady=8)
            combo.bind("<<ComboboxSelected>>", self.refresh_activities)
            self.fields[key] = combo
        tk.Label(form, text="Compatible activities (select one or more)", anchor="w").grid(row=2, column=0, sticky="w", pady=8)
        self.activities_button = ttk.Menubutton(form, textvariable=self.selection_text)
        self.activities_button.grid(row=2, column=1, sticky="ew", padx=(14, 0), pady=8)
        self.activities_menu = tk.Menu(self.activities_button, tearoff=False)
        self.activities_button.configure(menu=self.activities_menu)
        details_frame = tk.Frame(self)
        details_frame.pack(fill=tk.BOTH, expand=True, pady=12)
        self.details = tk.Text(details_frame, height=10, wrap=tk.WORD, padx=10, pady=10)
        self.details.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(details_frame, orient=tk.VERTICAL, command=self.details.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.details.configure(yscrollcommand=scrollbar.set, state="disabled")
        tk.Label(self, text="Recognition still requires sensor observations and context. "
                 "References can be reused by compatible devices.",
                 anchor="w", justify=tk.LEFT, wraplength=1000).pack(fill=tk.X, pady=(0, 8))
        tk.Label(self, textvariable=self.status, anchor="w", justify=tk.LEFT, wraplength=1000).pack(fill=tk.X, pady=(0, 8))
        actions = tk.Frame(self)
        actions.pack(fill=tk.X)
        self.save_button = tk.Button(actions, text="Use selected reference roles", command=self.save)
        self.save_button.grid(row=0, column=0, sticky="w")
        tk.Button(actions, text="Refresh devices", command=self.reload).grid(row=0, column=1, sticky="w", padx=8)
        self.advanced_button = tk.Button(actions, text="Advanced reference rules…", command=advanced)
        self.advanced_button.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        listing = tk.Frame(connections_parent)
        listing.pack(fill=tk.BOTH, expand=True)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        self.connections = ttk.Treeview(listing, columns=("source", "role", "targets"), show="headings",
                                        selectmode="extended", height=10)
        for key, title, width in (("source", "Source", 115), ("role", "Activity / role", 105),
                                  ("targets", "Destinations", 150)):
            self.connections.heading(key, text=title)
            self.connections.column(key, width=width, minwidth=75, stretch=True)
        self.connections.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(listing, orient=tk.VERTICAL, command=self.connections.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(listing, orient=tk.HORIZONTAL, command=self.connections.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.connections.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.remove_button = tk.Button(connections_parent, text="Remove selected", command=self.remove_connections,
                                       state="disabled")
        self.remove_button.pack(anchor="w", pady=8)
        removal_note = tk.Label(connections_parent, text="Removes the reference from this house, "
                                "affecting all devices that use it. Save the scenario to keep changes.",
                                anchor="w", justify=tk.LEFT, wraplength=230)
        removal_note.pack(fill=tk.X)
        connections_parent.bind("<Configure>", lambda event: removal_note.configure(
            wraplength=max(150, event.width - 22)) if event.widget is connections_parent else None)
        self.connections.bind("<<TreeviewSelect>>", lambda _event: self.remove_button.configure(
            state="normal" if self.connections.selection() else "disabled"))
        self.bind("<Configure>", self.resize_labels)
        self.reload()

    def resize_labels(self, event):
        if event.widget is self:
            for child in self.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(wraplength=max(320, event.width - 8))

    def get_config(self):
        from app.ui.activity_properties import _settings_for_house
        return _settings_for_house(self.ctx)

    def set_details(self, value):
        self.details.configure(state="normal")
        self.details.delete("1.0", tk.END)
        self.details.insert("1.0", value)
        self.details.configure(state="disabled")

    def clear_activities(self):
        self.options = {}
        self.activity_variables = {}
        self.activities_menu.delete(0, tk.END)
        self.activities_button.configure(state="disabled")
        self.save_button.configure(state="disabled")
        self.selection_text.set("No compatible activities")

    def reload(self):
        from app.ui.device_inspector import current_devices
        old_source, old_target = self.fields["source"].get(), self.fields["target"].get()
        self.clear_activities()
        try:
            self.config = self.get_config()
            self.sources, unavailable = guided_reference_sources(self.ctx, self.config)
            self.targets = {f"{obj.name} ({obj.type})": obj for obj in current_devices(self.ctx)}
            for key, choices, old in (("source", self.sources, old_source), ("target", self.targets, old_target)):
                self.fields[key].configure(values=list(choices))
                self.fields[key].set(old if old in choices else "")
            self.refresh_activities()
            self.refresh_connections(self.config)
            if unavailable:
                self.status.set(f"{len(unavailable)} reference source(s) could not be read. Other references remain available.")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.sources, self.targets = {}, {}
            self.status.set(str(error))
            self.set_details("Refresh devices after correcting the model or house configuration.")

    def refresh_connections(self, config):
        from app.ui.device_inspector import current_devices
        selected = set(self.connections.selection())
        self.connections.delete(*self.connections.get_children())
        devices = current_devices(self.ctx)
        for key, binding in config["reference_bindings"].items():
            if binding["kind"] != "device":
                continue
            targets = [obj.name for obj in devices if match_reference_roles(obj, "device", {key: binding})]
            reference = binding["reference"]
            self.connections.insert("", tk.END, iid=key,
                values=(f"{reference['name']} ({reference['type']})", binding["feature"], ", ".join(targets) or "None"))
            if key in selected:
                self.connections.selection_add(key)
        self.remove_button.configure(state="normal" if self.connections.selection() else "disabled")

    def remove_connections(self):
        try:
            bindings = copy.deepcopy(self.get_config()["reference_bindings"])
            selected = [key for key in self.connections.selection() if key in bindings]
            if not selected:
                return
            for key in selected:
                del bindings[key]
            self.ctx.recognition_config["reference_bindings"] = bindings
            self.reload()
            self.status.set("Selected references removed from this house. Save the scenario CSV to keep these changes.")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.status.set(str(error))

    def refresh_activities(self, _event=None):
        self.clear_activities()
        source = self.sources.get(self.fields["source"].get())
        target = self.targets.get(self.fields["target"].get())
        if source is None or target is None:
            self.status.set("Choose a source and a destination. Use Advanced reference rules to define a missing known association.")
            self.set_details("Only devices with existing reference associations are offered as sources.")
            return
        try:
            self.options = compatible_reference_activities(source, target, self.get_config())
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.status.set(str(error))
            return
        reference = next(iter(source["reference_bindings"].values()))["reference"]
        lines = [f"Source: {self.fields['source'].get()}",
                 f"Source properties: {format_properties(reference['properties'])}",
                 f"Destination: {target.name} ({target.type})",
                 f"Destination properties: {format_properties(target.properties)}", ""]
        for name, option in sorted(self.options.items()):
            variable = tk.BooleanVar(self, value=False)
            self.activity_variables[name] = variable
            self.activities_menu.add_checkbutton(label=f"{name} · {option['similarity']:.0%} property similarity",
                                                 variable=variable, command=self.update_selection)
            for match in option["matches"]:
                properties = ", ".join(f"{prop} {value:.0%}" for prop, value in match["matched_properties"].items())
                lines.append(f"{name}: {match['similarity']:.0%} property similarity · role {match['feature']}\n  {properties}")
        if self.options:
            self.activities_button.configure(state="normal")
            self.selection_text.set("Select compatible activities…")
            self.status.set("Compatibility checked automatically. Select the activities to make their reference roles available in this house.")
        else:
            lines.append("No compatible activity for this pair. Required properties, similarity thresholds and recognition weights must match an existing activity rule.")
            self.status.set("No compatible activities. Choose another source or destination, or inspect the advanced reference rules.")
        self.set_details("\n".join(lines))

    def update_selection(self):
        selected = [name for name, variable in self.activity_variables.items() if variable.get()]
        self.selection_text.set(", ".join(selected) if selected else "Select compatible activities…")
        self.save_button.configure(state="normal" if selected else "disabled")

    def save(self):
        from app.ui.device_inspector import current_devices
        selected = [name for name, variable in self.activity_variables.items() if variable.get()]
        source_label, target_label = self.fields["source"].get(), self.fields["target"].get()
        try:
            # Re-read the pair before writing: an open property/advanced editor
            # may have changed it since the compatibility list was displayed.
            config = self.get_config()
            sources, _unavailable = guided_reference_sources(self.ctx, config)
            targets = {f"{obj.name} ({obj.type})": obj for obj in current_devices(self.ctx)}
            if source_label not in sources or target_label not in targets:
                raise ValueError("The source or destination is no longer available. Refresh and choose again.")
            bindings = import_compatible_references(sources[source_label], targets[target_label], config, selected)
            self.ctx.recognition_config["reference_bindings"] = bindings
            self.fields["source"].set("")
            self.fields["target"].set("")
            self.reload()
            self.status.set("Selected reference roles are available in this house. Choose another pair to add more. Save the scenario CSV to keep these settings.")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.reload()
            self.status.set(str(error))


def open_reference_rules(ctx):
    """The existing entry point now opens the simplified association editor."""
    previous = getattr(ctx, "_guided_reference_rules_window", None)
    if previous is not None and previous.winfo_exists():
        previous.lift()
        previous._guided_reference_editor.reload()
        return previous
    window = tk.Toplevel(ctx.window)
    ctx._guided_reference_rules_window = window
    window.title("Reference device rules")
    window.geometry("1080x660")
    window.minsize(880, 580)
    body = tk.Frame(window, padx=16, pady=14)
    body.pack(fill=tk.BOTH, expand=True)

    def advanced():
        old_editor = open_advanced_reference_rules(ctx)
        old_editor.bind("<Destroy>", lambda event: editor.reload()
                        if event.widget is old_editor and window.winfo_exists() else None, add="+")
        return old_editor

    columns = tk.Frame(body)
    columns.pack(fill=tk.BOTH, expand=True)
    columns.rowconfigure(0, weight=1)
    columns.columnconfigure(0, weight=3, uniform="reference_columns")
    columns.columnconfigure(1, weight=1, uniform="reference_columns")
    connections = tk.LabelFrame(columns, text="Current connections", padx=8, pady=8)
    connections.grid(row=0, column=1, sticky="nsew")
    editor = GuidedReferenceRulesEditor(columns, ctx, advanced=advanced, connections_parent=connections)
    editor.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
    tk.Button(body, text="Close", command=window.destroy).pack(anchor="e", pady=(10, 0))
    window._guided_reference_editor = editor
    window._open_advanced_reference_rules = advanced
    apply_theme_tree(window, "light")
    return window
