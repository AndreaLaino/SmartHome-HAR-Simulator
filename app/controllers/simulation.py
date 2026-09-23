from __future__ import annotations
import tkinter as tk

from timer import TimerApp
from sim import start_simulation, interaction, update_sensors
from activity import monitor_activities, process_activities, close_current_activity
from log import start_interaction_log_session, stop_interaction_log_session
from app.context import AppContext
from app.logging_setup import setup_logging
from canvas_zoom import set_canvas_cursor

logger = setup_logging("controllers.simulation")


def _resolve_runtime_sources(load_active: bool) -> dict:
    from read import coordinates, read_sensors, read_walls_coordinates, read_devices, read_doors
    from point import points
    from sensor import sensors
    from wall import walls_coordinates
    from device import devices
    from door import doors

    if load_active:
        return {
            "points": coordinates,
            "sensors": read_sensors,
            "walls": read_walls_coordinates,
            "devices": read_devices,
            "doors": read_doors,
        }

    return {
        "points": points,
        "sensors": sensors,
        "walls": walls_coordinates,
        "devices": devices,
        "doors": doors,
    }


def _cleanup_manual_sim(ctx: AppContext):
    """Cleanup function called after stopping manual simulation."""
    setattr(ctx, 'timer_app_instance', None)
    setattr(ctx, '_manual_runtime_sources', None)
    setattr(ctx, '_restore_manual_canvas_binding', None)
    if getattr(ctx, "simulation_menu", None) is not None:
        ctx.simulation_menu.entryconfig("Manual", state="normal")
    if hasattr(ctx, 'activity_label') and ctx.activity_label is not None:
        try:
            ctx.activity_label.config(text="Activity: None")
        except:
            pass


def start_sim(ctx: AppContext):
    """Start manual simulation and wire callbacks."""
    from tkinter import messagebox
    # Prevent multiple instances before changing the active canvas tool.
    if getattr(ctx, "timer_app_instance", None) is not None:
        messagebox.showwarning("Warning", "Manual simulation is already running.")
        return

    cancel_canvas_action = getattr(ctx, "_cancel_canvas_action", None)
    if callable(cancel_canvas_action):
        cancel_canvas_action()
    runtime_sources = _resolve_runtime_sources(ctx.load_active)
    from app.ui.rooms import refresh_rooms
    runtime_sources["rooms"] = refresh_rooms(ctx, draw=False)
    s_sensors = runtime_sources["sensors"]
    if not s_sensors:
        messagebox.showwarning("Error", "No sensors found to start the simulation.")
        return

    try:
        ctx.house_state.clear_runtime_state()
    except Exception:
        pass
    sensor_states_store = ctx.house_state.sensor_states()

    # Clean up previous widgets if they exist
    if hasattr(ctx, 'activity_label') and ctx.activity_label is not None:
        try:
            ctx.activity_label.config(text="Activity: None")
        except:
            pass

    # Clean up all children of timer_frame from previous sessions
    if hasattr(ctx, 'timer_frame'):
        for widget in ctx.timer_frame.winfo_children():
            widget.destroy()

    ctx.simulation_menu.entryconfig("Manual", state="disabled")
    if hasattr(ctx, 'canvas') and ctx.canvas is not None:
        ctx.canvas.unbind("<Button-3>")
        set_canvas_cursor(ctx.canvas, "arrow")
    set_canvas_mode = getattr(ctx, "_set_canvas_mode", None)
    activate_manual_canvas = getattr(ctx, "_activate_canvas_manual", None)
    if callable(activate_manual_canvas):
        activate_manual_canvas("Manual: press Start · left-click to interact")
    elif callable(set_canvas_mode):
        ctx.canvas.unbind("<Button-1>")
        set_canvas_mode(
            "manual",
            "Manual: press Start · left-click to interact",
        )

    def _bind_canvas_click():
        if callable(activate_manual_canvas):
            activate_manual_canvas("Manual: left-click to move / interact")
        ctx.canvas.bind(
            "<Button-1>",
            lambda event: interaction(ctx.canvas, timer_app_instance, event, ctx.activity_label, ctx.house_state, runtime_sources),
        )
        set_canvas_cursor(ctx.canvas, "arrow")
        if callable(set_canvas_mode):
            set_canvas_mode(
                "manual",
                "Manual: left-click to move / interact",
            )

    # Ensure the name exists in the closure for lambdas below
    timer_app_instance = None

    runtime_initialized = False

    def _on_start():
        nonlocal runtime_initialized
        try:
            from sensor import reset_llm_runtime_state, reset_temperature_runtime_state, set_llm_smartmeter_mode
            if not runtime_initialized:
                reset_llm_runtime_state()
                reset_temperature_runtime_state()
            set_llm_smartmeter_mode(getattr(ctx, "smartmeter_mode", "simulation"))
        except Exception as e:
            logger.warning("Unable to set Smart Meter mode: %s", e)
        runtime_initialized = True
        _bind_canvas_click()
        start_simulation(ctx.canvas, timer_app_instance, ctx.activity_label, ctx.house_state, runtime_sources)
        monitor_activities(ctx.canvas, ctx.activity_label, timer_app_instance, sensor_states_store, ctx.house_state, runtime_sources)
        interaction_state = ctx.house_state.interaction_log_state()
        if interaction_state.get("interaction_file") is None:
            start_interaction_log_session(
                ctx.house_state,
                timer_app_instance.get_simulated_time(),
            )

    def _on_pause():
        # Do not erase Select/placement bindings if the timer is paused while
        # another canvas tool is active.
        if getattr(ctx, "_canvas_mode", "manual") == "manual":
            if hasattr(ctx, "canvas") and ctx.canvas is not None:
                ctx.canvas.unbind("<Button-1>")
            if callable(set_canvas_mode):
                set_canvas_mode("manual", "Manual paused · press Start")

    def _on_reset():
        close_current_activity(
            timer_app_instance,
            ctx.activity_label,
            ctx.house_state,
        )
        stop_interaction_log_session(ctx.house_state)
        if hasattr(ctx, "canvas") and ctx.canvas is not None:
            ctx.canvas.unbind("<Button-1>")
        enable_all_menus(ctx)
        activate_select = getattr(ctx, "_activate_canvas_select", None)
        if callable(activate_select):
            activate_select()
        ctx.window.after(100, lambda: _cleanup_manual_sim(ctx))

    def _restore_canvas_binding():
        enable_all_menus(ctx)
        activate_manual_canvas = getattr(ctx, "_activate_canvas_manual", None)
        if timer_app_instance is not None and timer_app_instance.is_running:
            if callable(activate_manual_canvas):
                activate_manual_canvas("Manual: left-click to move / interact")
            _bind_canvas_click()
        else:
            if callable(activate_manual_canvas):
                activate_manual_canvas("Manual paused · press Start")
            else:
                ctx.canvas.unbind("<Button-1>")
                ctx.canvas.unbind("<Button-3>")
            set_canvas_cursor(ctx.canvas, "arrow")
            if callable(set_canvas_mode):
                set_canvas_mode("manual", "Manual paused · press Start")

    timer_app_instance = TimerApp(
        ctx.timer_frame,
        start_callback=_on_start,
        pause_callback=_on_pause,
        reset_callback=_on_reset,
        start_time_mode=ctx.preferences.get("start_time", "computer"),
    )

    ctx.timer_app_instance = timer_app_instance
    ctx._manual_runtime_sources = runtime_sources
    ctx._restore_manual_canvas_binding = _restore_canvas_binding

    if not hasattr(ctx, 'activity_label') or ctx.activity_label is None:
        ctx.activity_label = tk.Label(
            ctx.activity_frame, text="Activity: None", font=("Helvetica", 16), bg="white", fg="black"
        )
        ctx.activity_label.pack(pady=15, padx=10, fill=tk.BOTH, expand=True)

    apply_theme = getattr(ctx, "_apply_theme", None)
    if callable(apply_theme):
        apply_theme()

    def _on_advance_step(delta_seconds):
        update_sensors(
            ctx.canvas,
            timer_app_instance,
            ctx.activity_label,
            ctx.house_state,
            runtime_sources,
            schedule_next=False,
            force=True,
            delta_override=delta_seconds,
            fast=True,
        )
        process_activities(
            ctx.activity_label,
            timer_app_instance,
            sensor_states_store,
            ctx.house_state,
            runtime_sources,
        )

    timer_app_instance.on_advance_step = _on_advance_step

    # Scenario editing remains available in Manual mode. Placement commands
    # temporarily replace the simulation click binding and restore it when done.
    enable_all_menus(ctx)

def activate_manual_interaction(ctx: AppContext):
    """Enter Manual canvas mode, leaving timer control to the Start button."""
    timer_app_instance = getattr(ctx, "timer_app_instance", None)
    if timer_app_instance is None:
        start_sim(ctx)
        return

    restore_binding = getattr(ctx, "_restore_manual_canvas_binding", None)
    if callable(restore_binding):
        restore_binding()


def enable_all_menus(ctx: AppContext):
    for label in ["Add points", "Add sensors", "Add devices", "Add walls", "Add doors", "Recognize rooms"]:
        ctx.scenario_menu.entryconfig(label, state="normal")


def exit_app(ctx: AppContext):
    from app.confirmations import ask_confirmation

    if ask_confirmation(
        ctx,
        "confirm_exit_app",
        "Exit",
        "Are you sure you want to close the application?",
    ):
        try:
            from app.preferences import capture_view_preferences, save_preferences

            capture_view_preferences(ctx)
            save_preferences(ctx.preferences)
        except Exception as e:
            logger.warning("Saving view preferences failed: %s", e)
        try:
            if ctx.smart_logger is not None:
                ctx.smart_logger.stop()
                logger.info("[SmartMeter] logging stopped")
        except Exception as e:
            logger.warning("Stopping SmartMeterLogger failed: %s", e)
        for module_name, label in (
            ("app.hardware.smartmeter", "SmartMeter"),
            ("app.hardware.real_sensors", "GPIO/DHT sensor"),
        ):
            try:
                module = __import__(module_name, fromlist=["stop_all"])
                stop_all = getattr(module, "stop_all", None)
                if callable(stop_all):
                    stop_all()
                    logger.info("[%s] loggers stopped", label)
            except Exception as e:
                logger.warning("Stopping %s loggers failed: %s", label, e)
        ctx.window.quit()

__all__ = [
    "start_sim",
    "activate_manual_interaction",
    "enable_all_menus",
    "exit_app",
]
