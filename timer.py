import tkinter as tk
from datetime import datetime, timedelta


class TimerApp:
    def __init__(
        self,
        parent,
        start_callback=None,
        stop_callback=None,
        pause_callback=None,
        reset_callback=None,
    ):
        self.start_callback = start_callback
        self.stop_callback = stop_callback
        self.pause_callback = pause_callback
        self.reset_callback = reset_callback

        self.on_advance_step = None

        self.timer_frame = tk.Frame(parent, width=400, height=500, bg="lightgrey", bd=2, relief="sunken")
        self.timer_frame.pack(side="right", fill="both", padx=15, pady=15, expand=True)

        self.is_running = False
        self.start_time = None
        self.elapsed_time = timedelta()
        self.simulated_start_time = None
        self.advanced = False
        self.current_date = datetime.today().strftime("%Y-%m-%d")
        self.advance_remaining = 0  # Track remaining seconds to advance
        self.advance_speed = 1
        self.last_update = None

        self.timer_frame.columnconfigure(0, weight=1)

        self.label = tk.Label(
            self.timer_frame,
            text=f"Time: 00:00 \n Date: {self.current_date}",
            font=("Helvetica", 22),
            bg="lightgrey",
        )
        self.label.grid(row=0, column=0, pady=(25, 20), padx=5, sticky="ew")

        # Input for start time
        self.start_hour_label = tk.Label(
            self.timer_frame,
            text="Start Hour (HH:MM):",
            font=("Helvetica", 15),
            bg="lightgrey",
        )
        self.start_hour_label.grid(row=1, column=0, pady=(15, 10), padx=5, sticky="ew")

        self.start_hour_entry = tk.Entry(self.timer_frame, font=("Helvetica", 16), width=15, justify="center")
        self.start_hour_entry.grid(row=2, column=0, pady=(10, 20), padx=20, ipady=10, sticky="ew")

        # Insert current time formatted properly
        self.start_hour_entry.insert(0, datetime.now().strftime("%H:%M"))

        # Start/Stop Button
        self.start_stop_button = tk.Button(self.timer_frame, text="Start", font=("Helvetica", 15, "bold"), command=self.start_stop)
        self.start_stop_button.grid(row=3, column=0, pady=(15, 10), padx=20, sticky="ew", ipady=15)

        # Speed controls
        speed_frame = tk.Frame(self.timer_frame, bg="lightgrey")
        speed_frame.grid(row=4, column=0, pady=(5, 10), padx=20, sticky="ew")
        speed_frame.columnconfigure((0, 1, 2, 3), weight=1)

        tk.Label(speed_frame, text="Speed", font=("Helvetica", 12), bg="lightgrey").grid(row=0, column=0, padx=4)
        self.speed_1x_button = tk.Button(speed_frame, text="1x", font=("Helvetica", 12), command=lambda: self.set_speed(1))
        self.speed_2x_button = tk.Button(speed_frame, text="2x", font=("Helvetica", 12), command=lambda: self.set_speed(2))
        self.speed_5x_button = tk.Button(speed_frame, text="5x", font=("Helvetica", 12), command=lambda: self.set_speed(5))
        self.speed_1x_button.grid(row=0, column=1, padx=4, sticky="ew")
        self.speed_2x_button.grid(row=0, column=2, padx=4, sticky="ew")
        self.speed_5x_button.grid(row=0, column=3, padx=4, sticky="ew")
        self._update_speed_buttons()

        # Advance 15 min Button
        self.advance_button = tk.Button(self.timer_frame, text="Advance 15 min", font=("Helvetica", 15), command=self.advance_time)
        self.advance_button.grid(row=5, column=0, pady=(15, 10), padx=20, sticky="ew", ipady=15)

        # Advance 1 hour Button
        self.advance_hour_button = tk.Button(self.timer_frame, text="Advance 1 hour", font=("Helvetica", 15), command=self.advance_hour)
        self.advance_hour_button.grid(row=6, column=0, pady=(15, 10), padx=20, sticky="ew", ipady=15)

        # Reset Button
        self.reset_button = tk.Button(self.timer_frame, text="Reset", font=("Helvetica", 15), command=self.reset)
        self.reset_button.grid(row=7, column=0, pady=(15, 25), padx=20, sticky="ew", ipady=15)

        self.update_timer()

    def start_stop(self):
        if not self.is_running:
            if self.start_time is None:
                start_hour_str = self.start_hour_entry.get()
                try:
                    today = datetime.today()
                    simulated_start_time = datetime.strptime(start_hour_str, "%H:%M").time()
                    self.simulated_start_time = datetime.combine(today, simulated_start_time)

                    self.start_time = datetime.now()
                    self.elapsed_time = timedelta()
                    self.is_running = True
                    self.start_stop_button.config(text="Stop")
                    self.last_update = datetime.now()
                    if self.start_callback:
                        self.start_callback()
                except ValueError:
                    print("Invalid time format. Use HH:MM.")
            else:
                self.is_running = True
                self.start_stop_button.config(text="Stop")
                self.last_update = datetime.now()
                if self.start_callback:
                    self.start_callback()
        else:
            self.is_running = False
            self.start_stop_button.config(text="Start")
            self.last_update = None
            callback = self.pause_callback or self.stop_callback
            if callback:
                callback()

    def advance_time(self):
        """Advance the simulated time by 15 simulated minutes with smooth batch processing."""
        jump_seconds = 15  # 1 sec = 1 simulated minute
        self._do_advance_instant(jump_seconds)

    def advance_hour(self):
        """Advance the simulated time by 60 simulated minutes with smooth batch processing."""
        jump_seconds = 60  # 1 sec = 1 simulated minute
        self._do_advance_instant(jump_seconds)

    def set_speed(self, speed):
        self.advance_speed = int(speed)
        self._update_speed_buttons()

    def _update_speed_buttons(self):
        active_bg = "#cfe8cf"
        # Use the timer frame background as the inactive button background so it's portable
        try:
            inactive_bg = self.timer_frame.cget("bg")
        except Exception:
            inactive_bg = "lightgrey"
        self.speed_1x_button.config(bg=active_bg if self.advance_speed == 1 else inactive_bg)
        self.speed_2x_button.config(bg=active_bg if self.advance_speed == 2 else inactive_bg)
        self.speed_5x_button.config(bg=active_bg if self.advance_speed == 5 else inactive_bg)

    def _do_advance_batch(self):
        """Process advance steps with short yields to keep UI responsive."""
        if not self.is_running or self.simulated_start_time is None:
            self.advance_remaining = 0
            return

        if self.advance_remaining <= 0:
            self.advanced = True
            self.timer_frame.after(200, self.reset_flag)
            return

        step = min(self.advance_speed, self.advance_remaining)
        self.elapsed_time += timedelta(seconds=step)
        if self.is_running:
            self.last_update = datetime.now()

        if callable(self.on_advance_step):
            self.on_advance_step(step)

        self.advance_remaining -= step
        
        # Update display
        simulated_time = self.get_simulated_time()
        self.label.config(text=f"Time: {simulated_time} \n Date: {self.current_date}")
        
        # Schedule next step with a short delay to keep UI responsive
        self.timer_frame.after(10, self._do_advance_batch)

    def _do_advance_instant(self, jump_seconds):
        """Advance in a single step (skip) to avoid UI lag."""
        if jump_seconds <= 0 or not self.is_running or self.simulated_start_time is None:
            return

        self.advanced = True
        self.advance_remaining = 0

        remaining = int(jump_seconds)
        step_size = 1
        while remaining > 0:
            step = step_size if remaining >= step_size else remaining
            self.elapsed_time += timedelta(seconds=step)
            if self.is_running:
                self.last_update = datetime.now()

            if callable(self.on_advance_step):
                self.on_advance_step(step)

            remaining -= step

        simulated_time = self.get_simulated_time()
        self.label.config(text=f"Time: {simulated_time} \n Date: {self.current_date}")
        self.timer_frame.after(200, self.reset_flag)

    def reset_flag(self):
        self.advanced = False
        self.advance_remaining = 0

    def get_simulated_time(self):
        if self.simulated_start_time is None:
            return "00:00"

        total_seconds = self.elapsed_time.total_seconds()
        simulated_minutes = int(total_seconds)  # 1 sec reale = 1 min simulato
        simulated_time = self.simulated_start_time + timedelta(minutes=simulated_minutes)

        if simulated_time.date() != datetime.strptime(self.current_date, "%Y-%m-%d").date():
            self.current_date = simulated_time.date().strftime("%Y-%m-%d")

        return simulated_time.strftime("%H:%M")

    def reset(self):
        had_session = self.start_time is not None or self.simulated_start_time is not None
        self.is_running = False
        callback = self.reset_callback or self.stop_callback
        if had_session and callback:
            try:
                callback()
            except Exception:
                pass

        self.start_time = None
        self.elapsed_time = timedelta()
        self.simulated_start_time = None
        self.current_date = datetime.today().strftime("%Y-%m-%d")
        self.last_update = None

        self.start_hour_entry.delete(0, tk.END)
        self.start_hour_entry.insert(0, "00:00")

        self.start_stop_button.config(text="Start")
        self.label.config(text=f"Time: 00:00 \n Date: {self.current_date}")
        try:
            from sensor import reset_llm_runtime_state, reset_temperature_runtime_state
            reset_llm_runtime_state()
            reset_temperature_runtime_state()
        except Exception:
            pass

    def update_timer(self):
        if self.is_running:
            now = datetime.now()
            if self.last_update is None:
                self.last_update = now
            else:
                delta = now - self.last_update
                self.elapsed_time += delta * self.advance_speed
                self.last_update = now
            simulated_time = self.get_simulated_time()
            self.label.config(text=f"Time: {simulated_time} \n Date: {self.current_date}")

        self.timer_frame.after(100, self.update_timer)
