from __future__ import annotations

import tkinter as tk
from app.context import AppContext
from app.logging_setup import setup_logging
from app.preferences import (
    load_preferences,
    restore_canvas_preferences,
    restore_window_preference,
)
from app.controllers.simulation import exit_app
from app.ui.main_ui import build_home_ui


def apply_startup_preferences(ctx: AppContext) -> None:
    starting_home = ctx.preferences.get("starting_home", "empty")
    if starting_home == "default":
        from app.io.scenario import load_scenario_from_file

        load_scenario_from_file(ctx, ctx.canvas)
    elif starting_home == "custom":
        from app.io.scenario import load_scenario_from_path

        load_scenario_from_path(
            ctx,
            ctx.canvas,
            str(ctx.preferences.get("starting_home_path", "")),
        )
    if ctx.preferences.get("start_manual_mode", False):
        from app.controllers.simulation import start_sim

        start_sim(ctx)

#Reconstruct the interface after returning from the Automatic setup
def rebuild_main_interface(ctx: AppContext):
    win = ctx.window
    win.title("Simulator")
    build_home_ui(ctx)

#Main
def main():
    logger = setup_logging("app")
    logger.info("Starting Simulator application")

    window = tk.Tk()
    window.title("Simulator")

    ctx = AppContext(window=window, preferences=load_preferences())
    restore_window_preference(window, ctx.preferences)
    build_home_ui(ctx)
    apply_startup_preferences(ctx)
    window.after_idle(lambda: restore_canvas_preferences(ctx))
    window.protocol("WM_DELETE_WINDOW", lambda: exit_app(ctx))

    window.mainloop()

if __name__ == "__main__":
    main()
