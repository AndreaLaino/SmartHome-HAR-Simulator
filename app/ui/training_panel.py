"""Map-side controls for recording human-labelled activity examples."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.ui.theme import apply_theme_tree
from har import runtime, storage


class TrainingPanel:
    def __init__(self, ctx, parent, before):
        self.ctx = ctx
        self.before = before
        self.visible = False
        self._poll_job = None
        self._house_id = ctx.har_config.get("house_id")
        self._timer = None
        self._session_id = None
        self._columns = None
        self.activity_vars = {}
        self.activity_buttons = {}
        self.frame = ttk.Frame(parent, padding=8)
        self.frame.columnconfigure(0, weight=1)
        self.frame.bind("<Destroy>", self._destroyed, add="+")
        controls = ttk.Frame(self.frame)
        controls.grid(row=0, column=0, sticky="ew")
        self.record_button = ttk.Button(controls, text="Record", command=lambda: self._safe(self.record))
        self.record_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="Stop & save", command=lambda: self._safe(self.stop))
        self.stop_button.pack(side="left", padx=6)
        self.models_button = ttk.Button(controls, text="Train & models…", command=lambda: self._safe(self.open_models))
        self.models_button.pack(side="right")
        self.add_button = ttk.Button(controls, text="Add activity", command=self.show_add_activity)
        self.add_button.pack(side="right", padx=6)

        options = ttk.Frame(self.frame)
        options.grid(row=1, column=0, sticky="ew", pady=6)
        ttk.Label(options, text="Data use:").pack(side="left")
        self.partition = tk.StringVar(value="Source (training)")
        self.partition_combo = ttk.Combobox(
            options, textvariable=self.partition, values=("Source (training)", "Target (test)"),
            state="readonly", width=18,
        )
        self.partition_combo.pack(side="left", padx=6)
        self.house_text = tk.StringVar()
        ttk.Label(options, textvariable=self.house_text).pack(side="left", padx=6)

        list_frame = ttk.Frame(self.frame)
        list_frame.grid(row=2, column=0, sticky="ew")
        list_frame.columnconfigure(0, weight=1)
        self.activity_canvas = tk.Canvas(list_frame, height=90, highlightthickness=0)
        self.activity_canvas.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(list_frame, command=self.activity_canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.activity_canvas.configure(yscrollcommand=scroll.set)
        self.activity_grid = ttk.Frame(self.activity_canvas)
        self._grid_item = self.activity_canvas.create_window(0, 0, anchor="nw", window=self.activity_grid)
        self.activity_grid.bind("<Configure>", lambda _event: self.activity_canvas.configure(
            scrollregion=self.activity_canvas.bbox("all")))
        self.activity_canvas.bind("<Configure>", self._resize)
        self.no_activity = tk.BooleanVar(value=False)
        self.no_activity_button = tk.Checkbutton(
            self.activity_grid, text="No activity (idle)", variable=self.no_activity,
            indicatoron=False, width=1, padx=8, pady=5,
            command=lambda: self._safe(self._choose_no_activity),
        )

        self.add_row = ttk.Frame(self.frame)
        self.add_row.columnconfigure(0, weight=1)
        self.new_activity = tk.StringVar()
        self.name_entry = ttk.Entry(self.add_row, textvariable=self.new_activity)
        self.name_entry.grid(row=0, column=0, sticky="ew")
        self.name_entry.bind("<Return>", lambda _event: self._safe(self.add_activity))
        self.save_activity_button = ttk.Button(self.add_row, text="Add", command=lambda: self._safe(self.add_activity))
        self.save_activity_button.grid(row=0, column=1, padx=6)
        ttk.Button(self.add_row, text="Cancel", command=self.add_row.grid_remove).grid(row=0, column=2)
        self.status = tk.StringVar(value="Select the true activities, then Record. No selection leaves observations unlabelled.")
        self.status_label = ttk.Label(self.frame, textvariable=self.status, wraplength=650)
        self.status_label.grid(row=4, column=0, sticky="ew", pady=(5, 0))
        self.recording_text = tk.StringVar()
        self.recording_label = ttk.Label(self.frame, textvariable=self.recording_text, wraplength=650)
        self.recording_label.grid(row=5, column=0, sticky="ew", pady=(3, 0))
        self.stop_button.configure(state="disabled")

    def _safe(self, callback):
        try:
            return callback()
        except Exception as error:
            self.status.set(str(error))
        finally:
            self.refresh()

    def toggle(self):
        if self.visible:
            self.frame.pack_forget()
            self.visible = False
            self._cancel_poll()
            return
        self.frame.pack(side=tk.BOTTOM, fill=tk.X, before=self.before, pady=(5, 0))
        self.visible = True
        self._safe(self.load_activities)
        apply_theme_tree(self.frame, "light")
        self._poll_job = self.frame.after(300, self._poll)

    def load_activities(self):
        names = [
            "cooking", "eating", "relaxing", "sleeping", "working", "cleaning",
            "laundry", "washing dishes", "using the bathroom", "showering",
            "leaving home", "returning home", *storage.load_activity_names(),
        ]
        for session in storage.list_sessions():
            for frame in session["frames"]:
                names.extend(frame.get("labels") or [])
        for model in storage.list_models():
            names.extend(model.get("labels", []))
        self._ensure_activities(names)

    def _ensure_activities(self, names):
        added = False
        for name in dict.fromkeys(names):
            if name in self.activity_vars:
                continue
            variable = tk.BooleanVar(value=False)
            self.activity_vars[name] = variable
            self.activity_buttons[name] = tk.Checkbutton(
                self.activity_grid, text=name, variable=variable, indicatoron=False,
                width=1, padx=8, pady=5,
                command=lambda: self._safe(self._choose_activity),
            )
            added = True
        if added:
            apply_theme_tree(self.activity_grid, "light")
            self._layout_buttons()

    def _resize(self, event):
        self.activity_canvas.itemconfigure(self._grid_item, width=event.width)
        self.status_label.configure(wraplength=max(100, event.width))
        self.recording_label.configure(wraplength=max(100, event.width))
        self._layout_buttons()

    def _layout_buttons(self):
        width = max(170, self.activity_canvas.winfo_width())
        columns = max(1, min(4, width // 170))
        for column in range(max(columns, self._columns or 0)):
            self.activity_grid.columnconfigure(
                column, weight=int(column < columns), uniform="activity" if column < columns else "",
            )
        self._columns = columns
        for index, button in enumerate([self.no_activity_button, *self.activity_buttons.values()]):
            button.configure(wraplength=max(80, width // columns - 30))
            button.grid(row=index // columns, column=index % columns, sticky="ew", padx=3, pady=3)

    def selected_labels(self):
        if self.no_activity.get():
            return []
        return sorted(name for name, variable in self.activity_vars.items() if variable.get()) or None

    def _set_selection(self, labels):
        self._ensure_activities(labels or [])
        self.no_activity.set(labels == [])
        for name, variable in self.activity_vars.items():
            variable.set(labels is not None and name in labels)

    def _choose_activity(self):
        self.no_activity.set(False)
        self._apply_selection()

    def _choose_no_activity(self):
        if self.no_activity.get():
            for variable in self.activity_vars.values():
                variable.set(False)
        self._apply_selection()

    def _apply_selection(self):
        labels = self.selected_labels()
        if runtime.recording_status(self.ctx)["recording"]:
            runtime.set_recording_labels(self.ctx, labels)
        description = "unlabelled context" if labels is None else (", ".join(labels) or "no activity")
        self.status.set(f"True activities: {description}. These labels apply to observations from now on.")

    def record(self):
        from app.controllers.simulation import activate_manual_interaction

        self.refresh()
        if getattr(self.ctx, "_manual_reset_pending", False):
            raise ValueError("Reset is finishing. Press Record again in a moment.")
        if runtime.recording_status(self.ctx)["recording"]:
            raise ValueError("A recording is already running. Stop and save it first.")
        # Manual startup clears runtime state, so it must precede recording.
        activate_manual_interaction(self.ctx)
        timer = getattr(self.ctx, "timer_app_instance", None)
        if timer is None:
            raise ValueError("Add a device or sensor before recording.")
        labels = self.selected_labels()
        self._timer = timer
        partition = "target" if self.partition.get() == "Target (test)" else "source"
        runtime.start_recording(self.ctx, partition=partition)
        runtime.set_recording_labels(self.ctx, labels)
        if not timer.is_running:
            timer.start_stop()
        if not timer.is_running:
            raise ValueError("Recording is armed, but the clock did not start. Check Start hour (HH:MM), then press Start.")
        self.status.set("Recording observations and true activities. Select activities as they change.")

    def stop(self):
        path = runtime.stop_recording(self.ctx)
        self._session_id = None
        self.status.set(f"Saved {path.name}. Reset the simulation before recording an independent session.")
        editor = getattr(self.ctx, "_learned_har_window", None)
        if editor is not None:
            editor.refresh_data()

    def show_add_activity(self):
        self.add_row.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.name_entry.focus_set()

    def add_activity(self):
        name = self.new_activity.get().strip()
        names = storage.load_activity_names()
        storage.save_activity_names([*names, name])
        self._ensure_activities([name])
        self.new_activity.set("")
        self.add_row.grid_remove()
        self.status.set(f"Added {name}. Record labelled examples, then train a model to recognize it.")

    def open_models(self):
        from app.ui.learned_har import open_learned_har

        editor = open_learned_har(self.ctx)
        editor.tabs.select(editor.train_tab)

    def refresh(self):
        house_id = self.ctx.har_config.get("house_id")
        timer = getattr(self.ctx, "timer_app_instance", None)
        if house_id != self._house_id:
            self._house_id = house_id
            self.partition.set("Source (training)")
            self._set_selection(None)
            self.status.set("Select the true activities, then Record. No selection leaves observations unlabelled.")
        elif self._timer is not None and timer is not self._timer:
            self._set_selection(None)
        self._timer = timer
        self.house_text.set("House: " + self.ctx.har_config.get("house_label", "House"))
        state = runtime.recording_status(self.ctx)
        recording = state["recording"]
        if self._session_id is not None and not recording:
            self.status.set("Recording saved. Select the true activities before the next recording.")
        self._session_id = state["session_id"]
        if recording:
            self._set_selection(state["labels"])
            self.partition.set("Target (test)" if state["partition"] == "target" else "Source (training)")
        pending_reset = getattr(self.ctx, "_manual_reset_pending", False)
        self.record_button.configure(state="disabled" if recording or pending_reset else "normal")
        self.stop_button.configure(state="normal" if recording else "disabled")
        self.partition_combo.configure(state="disabled" if recording else "readonly")
        labels = state["labels"] if recording else self.selected_labels()
        description = "unlabelled context" if labels is None else (", ".join(labels) or "no activity")
        self.recording_text.set(f"{'Recording' if recording else 'Not recording'} · Frames: {state['frames']} · True activities: {description}")

    def _poll(self):
        self._poll_job = None
        self.refresh()
        self._poll_job = self.frame.after(300, self._poll)

    def _cancel_poll(self):
        if self._poll_job is not None:
            self.frame.after_cancel(self._poll_job)
            self._poll_job = None

    def _destroyed(self, event):
        if event.widget is self.frame:
            self._cancel_poll()
            if getattr(self.ctx, "_training_panel", None) is self:
                self.ctx._training_panel = None


def toggle_training_panel(ctx, parent, before):
    panel = getattr(ctx, "_training_panel", None)
    if panel is None:
        panel = TrainingPanel(ctx, parent, before)
        ctx._training_panel = panel
    panel.toggle()
    return panel
