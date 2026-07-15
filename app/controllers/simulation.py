from __future__ import annotations
import tkinter as tk

from timer import TimerApp
from sim import start_simulation, interaction, update_sensors
from activity import monitor_activities, process_activities, close_current_activity
from log import start_interaction_log_session, stop_interaction_log_session
from app.context import AppContext
from app.logging_setup import setup_logging

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
    if hasattr(ctx, 'activity_label') and ctx.activity_label is not None:
        try:
            ctx.activity_label.config(text="Activity: None")
        except:
            pass


def start_sim(ctx: AppContext):
    """Start manual simulation and wire callbacks."""
    from tkinter import messagebox
    runtime_sources = _resolve_runtime_sources(ctx.load_active)
    from app.ui.rooms import refresh_rooms
    runtime_sources["rooms"] = refresh_rooms(ctx, draw=False)
    s_sensors = runtime_sources["sensors"]
    if not s_sensors:
        messagebox.showwarning("Error", "No sensors found to start the simulation.")
        return

    # Prevent multiple instances
    if hasattr(ctx, 'timer_app_instance') and ctx.timer_app_instance is not None:
        messagebox.showwarning("Warning", "Manual simulation is already running.")
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
        ctx.canvas.unbind("<Button-1>")

    def _bind_canvas_click():
        ctx.canvas.bind(
            "<Button-1>",
            lambda event: interaction(ctx.canvas, timer_app_instance, event, ctx.activity_label, ctx.house_state, runtime_sources),
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
        if hasattr(ctx, "canvas") and ctx.canvas is not None:
            ctx.canvas.unbind("<Button-1>")

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
        ctx.window.after(100, lambda: _cleanup_manual_sim(ctx))

    timer_app_instance = TimerApp(
        ctx.timer_frame,
        start_callback=_on_start,
        pause_callback=_on_pause,
        reset_callback=_on_reset,
    )

    ctx.timer_app_instance = timer_app_instance

    if not hasattr(ctx, 'activity_label') or ctx.activity_label is None:
        ctx.activity_label = tk.Label(
            ctx.activity_frame, text="Activity: None", font=("Helvetica", 16), bg="white", fg="black"
        )
        ctx.activity_label.pack(pady=15, padx=10, fill=tk.BOTH, expand=True)

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

    disable_all_menus(ctx)


def disable_all_menus(ctx: AppContext):
    for label in ["Add points", "Add sensors", "Add devices", "Add walls", "Add doors", "Recognize rooms"]:
        ctx.scenario_menu.entryconfig(label, state="disabled")


def enable_all_menus(ctx: AppContext):
    for label in ["Add points", "Add sensors", "Add devices", "Add walls", "Add doors", "Recognize rooms"]:
        ctx.scenario_menu.entryconfig(label, state="normal")


def exit_app(ctx: AppContext):
    from tkinter import messagebox

    if messagebox.askyesno("Exit", "Are you sure you want to close the application?"):
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

__all__ = ["start_sim", "enable_all_menus", "exit_app"]
