"""Live HAR columns, retaining the primary Label used by simulation callbacks."""

import tkinter as tk


class ActivityComparisonLabel(tk.Label):
    def __init__(self, parent):
        self.panel = parent
        self.columns = tk.Frame(parent)
        self.columns.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        self.columns.rowconfigure(1, weight=1)
        for column in (0, 1):
            self.columns.columnconfigure(column, weight=1, uniform="har")
        self.titles = [tk.Label(self.columns, text=name, font=("Helvetica", 10, "bold"), anchor="w")
                       for name in ("Random Forest", "Gradient Boosting")]
        for column, title in enumerate(self.titles):
            title.grid(row=0, column=column, sticky="ew", pady=(0, 5))
        super().__init__(self.columns, text="Activity: None", font=("Helvetica", 11),
                         wraplength=180, justify=tk.LEFT, anchor="nw")
        self.secondary = tk.Label(self.columns, text="Activity: None", font=("Helvetica", 11),
                                  wraplength=180, justify=tk.LEFT, anchor="nw")
        self.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.secondary.grid(row=1, column=1, sticky="nsew", padx=(5, 0))
        self._fit_job = None
        self.bind("<Configure>", self._resize)
        self.secondary.bind("<Configure>", self._resize)

    def _resize(self, event):
        width = max(1, event.width - 12)
        if int(event.widget.cget("wraplength")) != width:
            event.widget.configure(wraplength=width)
        self._schedule_fit()

    def _schedule_fit(self):
        if self._fit_job is None:
            self._fit_job = self.after_idle(self._fit_height)

    def _fit_height(self):
        self._fit_job = None
        height = max(self.winfo_reqheight(), self.secondary.winfo_reqheight()) + self.titles[0].winfo_reqheight() + 5
        self.panel.configure(height=max(150, height + 8))

    def show_prediction(self, result):
        from activity import _learned_activity_text

        predictions = result.get("comparisons")
        if predictions:
            left = _learned_activity_text(predictions["random_forest"])
            right = _learned_activity_text(predictions["gradient_boosting"])
        elif result.get("status") in {"no_model", "error"}:
            left = right = _learned_activity_text(result)
        else:
            missing = ("No prediction for this algorithm.\n"
                       "Activate the RF + Gradient Boosting comparison in Train & models.")
            prediction = _learned_activity_text(result)
            if result.get("algorithm") == "gradient_boosting":
                left, right = missing, prediction
            else:
                left, right = prediction, missing
        self.configure(text=left)
        self.secondary.configure(text=right)
        self._schedule_fit()

    def reset_prediction(self):
        self.configure(text="Activity: None")
        self.secondary.configure(text="Activity: None")
        self._schedule_fit()
