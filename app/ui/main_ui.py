from __future__ import annotations

import tkinter as tk
from tkinter import Menu, messagebox
from pathlib import Path

from device import add_device
from door import add_door_between_points
from point import add_point
from read import draw_points, draw_walls, draw_sensors, draw_devices, draw_doors
from sensor import add_sensor
from wall import add_wall_between_points
from graph import show_graphs
from automatic import launch_automatic_interface

from app.context import AppContext
from app.controllers.simulation import (
    activate_manual_interaction,
    exit_app,
)
from app.preferences import CONFIRMATION_PREFERENCES, save_preferences
from app.io.safe_dialog import ask_open_file
from app.io.scenario import (
    load_scenario_from_file,
    open_scenario,
    save_scenario,
    save_scenario_as,
    delete_scenario,
    export_simulation_csv,
    import_csv,
    import_csv_from_s3,
)
from app.ui.bindings import open_bind_gpio_sensors_ui, open_bind_ip_ui
from app.ui.device_inspector import (
    begin_placement_mode,
    begin_point_segment_mode,
    install_device_inspector,
    record_wall_addition,
)
from app.ui.tool_palette import ToolPalette
from app.ui.theme import apply_theme_tree, get_theme_palette, set_theme_role
from app.controllers.llm_smartmeter import open_llm_smartmeter_ui
from app.ui.rooms import open_rooms_ui
from canvas_zoom import (
    bind_canvas_pan,
    draw_grid_background,
    get_zoom,
    queue_trackpad_zoom,
    reset_zoom,
    zoom_in,
    zoom_out,
)
from utils import (
    refresh_pir_fov,
    set_pir_fov_sources,
    set_pir_fov_transparency,
    set_pir_fov_visibility,
)


def _sensor_store(ctx: AppContext) -> dict:
    return ctx.house_state.sensor_states()


def _activity_log_store(ctx: AppContext) -> dict:
    return ctx.house_state.values.setdefault(
        "activity_log_state",
        {
            "activity_log": [],
            "active_activities": {},
        },
    )


def _current_pir_sources(ctx: AppContext) -> tuple:
    if ctx.load_active:
        from read import read_walls_coordinates

        return ctx.read_sensors, read_walls_coordinates, ctx.read_doors
    from door import doors
    from sensor import sensors
    from wall import walls_coordinates

    return sensors, walls_coordinates, doors


def _refresh_pir_overlay(ctx: AppContext) -> None:
    sensors, walls, doors = _current_pir_sources(ctx)
    set_pir_fov_sources(ctx.canvas, sensors, walls, doors)
    refresh_pir_fov(ctx.canvas)


def _menu_add_point(ctx: AppContext):
    manual_mode = getattr(ctx, "timer_app_instance", None) is not None

    def placement_finished(_created):
        if manual_mode:
            restore_click = getattr(ctx, "_restore_manual_canvas_binding", None)
            if callable(restore_click):
                restore_click()
                return
        activate_select = getattr(ctx, "_activate_canvas_select", None)
        if callable(activate_select):
            activate_select()

    begin_placement_mode(
        ctx,
        "Add points",
        lambda event: add_point(
            ctx.canvas,
            event,
            ctx.load_active,
            on_finished=placement_finished,
        ),
    )


def _menu_add_device(ctx: AppContext):
    from app.ui.rooms import refresh_rooms

    manual_mode = getattr(ctx, "timer_app_instance", None) is not None

    def device_changed():
        rooms = refresh_rooms(ctx, draw=True)
        runtime_sources = getattr(ctx, "_manual_runtime_sources", None)
        if isinstance(runtime_sources, dict):
            runtime_sources["rooms"] = rooms

    def placement_finished(_created):
        if manual_mode:
            restore_click = getattr(ctx, "_restore_manual_canvas_binding", None)
            if callable(restore_click):
                restore_click()
                return
        activate_select = getattr(ctx, "_activate_canvas_select", None)
        if callable(activate_select):
            activate_select()

    begin_placement_mode(
        ctx,
        "Add devices",
        lambda event: add_device(
            ctx.canvas,
            event,
            ctx.load_active,
            on_changed=device_changed,
            on_finished=placement_finished,
        ),
    )


def _menu_add_sensor(ctx: AppContext):
    from app.ui.rooms import refresh_rooms

    manual_mode = getattr(ctx, "timer_app_instance", None) is not None

    def sensor_changed():
        rooms = refresh_rooms(ctx, draw=True)
        runtime_sources = getattr(ctx, "_manual_runtime_sources", None)
        if isinstance(runtime_sources, dict):
            runtime_sources["rooms"] = rooms
        _refresh_pir_overlay(ctx)

    def placement_finished(_created):
        if manual_mode:
            restore_click = getattr(ctx, "_restore_manual_canvas_binding", None)
            if callable(restore_click):
                restore_click()
                return
        activate_select = getattr(ctx, "_activate_canvas_select", None)
        if callable(activate_select):
            activate_select()

    begin_placement_mode(
        ctx,
        "Add sensors",
        lambda event: add_sensor(
            ctx.canvas,
            event,
            ctx.load_active,
            on_changed=sensor_changed,
            on_finished=placement_finished,
        ),
    )


def _menu_add_wall(ctx: AppContext):
    from app.ui.rooms import refresh_rooms

    return_to_manual = getattr(ctx, "_canvas_mode", None) == "manual"

    def geometry_changed():
        rooms = refresh_rooms(ctx, draw=True)
        runtime_sources = getattr(ctx, "_manual_runtime_sources", None)
        if isinstance(runtime_sources, dict):
            runtime_sources["rooms"] = rooms
        _refresh_pir_overlay(ctx)

    def placement_finished(_created):
        if return_to_manual:
            restore_click = getattr(ctx, "_restore_manual_canvas_binding", None)
            if callable(restore_click):
                restore_click()
                return
        activate_select = getattr(ctx, "_activate_canvas_select", None)
        if callable(activate_select):
            activate_select()

    def create_wall(point1, point2):
        wall = add_wall_between_points(
            ctx.canvas,
            point1,
            point2,
            ctx.load_active,
            on_changed=geometry_changed,
        )
        if wall is not None:
            record_wall_addition(ctx, wall)
        return wall

    begin_point_segment_mode(
        ctx,
        "Add walls",
        create_wall,
        on_finished=placement_finished,
        continuous=True,
    )


def _menu_add_door(ctx: AppContext):
    from app.ui.rooms import refresh_rooms

    return_to_manual = getattr(ctx, "_canvas_mode", None) == "manual"

    def geometry_changed():
        rooms = refresh_rooms(ctx, draw=True)
        runtime_sources = getattr(ctx, "_manual_runtime_sources", None)
        if isinstance(runtime_sources, dict):
            runtime_sources["rooms"] = rooms
        _refresh_pir_overlay(ctx)

    def placement_finished(_created):
        if return_to_manual:
            restore_click = getattr(ctx, "_restore_manual_canvas_binding", None)
            if callable(restore_click):
                restore_click()
                return
        activate_select = getattr(ctx, "_activate_canvas_select", None)
        if callable(activate_select):
            activate_select()

    begin_point_segment_mode(
        ctx,
        "Add doors",
        lambda point1, point2: add_door_between_points(
            ctx.canvas,
            point1,
            point2,
            ctx.load_active,
            on_changed=geometry_changed,
        ),
        on_finished=placement_finished,
    )


def build_home_ui(ctx: AppContext):
    # If already built, just show it
    if getattr(ctx, "home_frame", None) is not None:
        ctx.home_frame.pack(fill=tk.BOTH, expand=True)
        return

    home_frame = set_theme_role(tk.Frame(ctx.window), "background")
    home_frame.pack(fill=tk.BOTH, expand=True)
    ctx.home_frame = home_frame

    # Left: canvas with scrollbars
    image_frame = set_theme_role(
        tk.Frame(home_frame, width=900, height=900),
        "background",
    )
    image_frame.pack(side=tk.LEFT, padx=10, pady=10, fill=tk.BOTH, expand=True)

    zoom_bar = set_theme_role(tk.Frame(image_frame), "surface")
    zoom_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
    zoom_text = tk.StringVar(value="100%")
    canvas_mode_text = tk.StringVar(value="Mode: Select / inspect")

    tk.Label(
        zoom_bar,
        textvariable=canvas_mode_text,
        anchor="w",
        font=("Segoe UI", 9, "bold"),
    ).pack(
        side=tk.LEFT,
        padx=(0, 12),
    )
    help_label = tk.Label(
        zoom_bar,
        text="Select: left move · right menu/box-select   ·   Manual: left interact · Middle: pan",
        fg="#555555",
    )
    set_theme_role(help_label, "muted")
    help_label.pack(
        side=tk.RIGHT,
    )

    h_scroll = tk.Scrollbar(image_frame, orient=tk.HORIZONTAL)
    h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
    v_scroll = tk.Scrollbar(image_frame, orient=tk.VERTICAL)
    v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    tool_palette = ToolPalette(image_frame)
    tool_palette.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
    ctx._tool_palette = tool_palette
    tool_palette.add_tool(
        "select",
        "select",
        "Select / edit\nLeft-click or drag; drag segment ends to resize.\nRight-click for actions; right-drag to select multiple objects.",
        lambda: getattr(ctx, "_activate_canvas_select", lambda: None)(),
    )
    tool_palette.add_tool(
        "manual",
        "manual",
        "Manual interaction\nPress Start, then left-click the home to move and interact.",
        lambda: activate_manual_interaction(ctx),
    )
    tool_palette.add_separator()
    tool_palette.add_tool(
        "point",
        "point",
        "Add point\nChoose this tool, then left-click the grid.",
        lambda: _menu_add_point(ctx),
    )
    tool_palette.add_tool(
        "device",
        "device",
        "Add device\nChoose this tool, then left-click its position.",
        lambda: _menu_add_device(ctx),
    )
    tool_palette.add_tool(
        "sensor",
        "sensor",
        "Add sensor\nChoose this tool, then left-click its position.",
        lambda: _menu_add_sensor(ctx),
    )
    tool_palette.add_separator()
    tool_palette.add_tool(
        "wall",
        "wall",
        "Add wall\nClick two existing points; the wall stays attached to them.",
        lambda: _menu_add_wall(ctx),
    )
    tool_palette.add_tool(
        "door",
        "door",
        "Add door\nClick two existing points; the door stays attached to them.",
        lambda: _menu_add_door(ctx),
    )

    canvas = tk.Canvas(
        image_frame,
        width=900,
        height=900,
        xscrollcommand=h_scroll.set,
        yscrollcommand=v_scroll.set,
    )
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    h_scroll.config(command=canvas.xview)
    v_scroll.config(command=canvas.yview)
    ctx.canvas = canvas
    canvas._zoom_factor = 1.0
    canvas._zoom_text = zoom_text
    canvas._snap_to_grid = bool(ctx.preferences.get("snap_to_grid", True))
    canvas._show_grid = bool(ctx.preferences.get("show_grid", True))
    canvas._show_room_types = bool(
        ctx.preferences.get("show_room_types", True)
    )
    set_theme_role(canvas, "canvas")

    def apply_zoom(action, event=None):
        if action == "in":
            zoom_in(canvas, screen_x=getattr(event, "x", None), screen_y=getattr(event, "y", None))
        elif action == "out":
            zoom_out(canvas, screen_x=getattr(event, "x", None), screen_y=getattr(event, "y", None))
        else:
            reset_zoom(canvas)
        zoom_text.set(f"{round(get_zoom(canvas) * 100):.0f}%")
        return "break"

    def wheel_zoom(event):
        if event.delta == 0:
            return "break"
        queue_trackpad_zoom(
            canvas,
            1 if event.delta > 0 else -1,
            screen_x=event.x,
            screen_y=event.y,
            on_update=lambda zoom: zoom_text.set(f"{round(zoom * 100):.0f}%"),
        )
        return "break"

    canvas.bind("<MouseWheel>", wheel_zoom)
    canvas.bind("<Button-4>", lambda event: wheel_zoom(type("Wheel", (), {"delta": 1, "x": event.x, "y": event.y})()))
    canvas.bind("<Button-5>", lambda event: wheel_zoom(type("Wheel", (), {"delta": -1, "x": event.x, "y": event.y})()))
    bind_canvas_pan(canvas)

    draw_grid_background(canvas)
    pir_sensors, pir_walls, pir_doors = _current_pir_sources(ctx)
    canvas._pir_fov_transparency = int(
        ctx.preferences.get("pir_fov_transparency", 50)
    )
    set_pir_fov_visibility(
        canvas,
        pir_sensors,
        bool(ctx.preferences.get("show_pir_fov", False)),
        pir_walls,
        pir_doors,
    )

    # Right: timer and activity panel (vertical layout)
    right_container = set_theme_role(
        tk.Frame(home_frame, bg="lightgrey", width=420),
        "panel",
    )
    right_container.pack(side=tk.RIGHT, fill=tk.Y, expand=False)
    right_container.pack_propagate(False)

    mode_frame = tk.LabelFrame(
        right_container,
        text="Smart Meter mode",
        bg="lightgrey",
        fg="black",
        font=("Helvetica", 11, "bold"),
        padx=10,
        pady=8,
        bd=1,
        relief=tk.GROOVE,
    )
    set_theme_role(mode_frame, "panel")
    mode_frame.pack(side=tk.TOP, fill=tk.X, padx=16, pady=(10, 4))
    if not getattr(ctx, "smartmeter_mode", None):
        ctx.smartmeter_mode = "simulation"
    smartmeter_mode_var = tk.StringVar(value=ctx.smartmeter_mode)

    def _set_smartmeter_mode():
        ctx.smartmeter_mode = smartmeter_mode_var.get()
        try:
            from sensor import set_llm_smartmeter_mode
            set_llm_smartmeter_mode(ctx.smartmeter_mode)
        except Exception:
            pass

    tk.Radiobutton(
        mode_frame,
        text="Complete simulation",
        value="simulation",
        variable=smartmeter_mode_var,
        command=_set_smartmeter_mode,
        bg="lightgrey",
        fg="black",
        activebackground="lightgrey",
        activeforeground="black",
        selectcolor="white",
        font=("Helvetica", 10),
        anchor="w",
    ).pack(anchor="w", pady=1)
    tk.Radiobutton(
        mode_frame,
        text="Real-time DT prediction",
        value="realtime_dt",
        variable=smartmeter_mode_var,
        command=_set_smartmeter_mode,
        bg="lightgrey",
        fg="black",
        activebackground="lightgrey",
        activeforeground="black",
        selectcolor="white",
        font=("Helvetica", 10),
        anchor="w",
    ).pack(anchor="w", pady=1)

    # Timer at the top - takes most space
    timer_container = set_theme_role(
        tk.Frame(right_container, bg="lightgrey", width=400, height=650),
        "panel",
    )
    timer_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
    timer_container.pack_propagate(False)
    timer_frame = set_theme_role(
        tk.Frame(timer_container, bg="lightgrey", width=400),
        "panel",
    )
    timer_frame.pack(fill=tk.BOTH, expand=True)
    ctx.timer_frame = timer_frame

    # Activity at the bottom - fixed smaller space
    activity_container = set_theme_role(
        tk.Frame(right_container, bg="lightgrey", width=400, height=150),
        "panel",
    )
    activity_container.pack(side=tk.BOTTOM, fill=tk.X, expand=False, padx=10, pady=10)
    activity_container.pack_propagate(False)
    ctx.activity_frame = activity_container

    menu_bar = Menu(ctx.window)
    ctx.window.config(menu=menu_bar)

    # File menu
    file_menu = Menu(menu_bar, tearoff=0)
    ctx.file_menu = file_menu
    menu_bar.add_cascade(label="File", menu=file_menu)
    file_menu.add_command(label="New", command=lambda: delete_scenario(ctx, ctx.canvas))
    file_menu.add_command(label="Open file", command=lambda: open_scenario(ctx, ctx.canvas))
    file_menu.add_command(label="Load default (saved.csv)", command=lambda: load_scenario_from_file(ctx, ctx.canvas))
    file_menu.add_separator()
    file_menu.add_command(label="Save", command=lambda: save_scenario(ctx))
    file_menu.add_command(label="Save As", command=lambda: save_scenario_as(ctx))
    file_menu.add_separator()
    file_menu.add_command(label="Exit", command=lambda: exit_app(ctx))

    # Scenario menu
    scenario_menu = Menu(menu_bar, tearoff=0)
    menu_bar.add_cascade(label="Scenario", menu=scenario_menu)
    ctx.scenario_menu = scenario_menu

    scenario_menu.add_command(label="Add points", command=lambda: _menu_add_point(ctx))
    scenario_menu.add_command(label="Add devices", command=lambda: _menu_add_device(ctx))
    scenario_menu.add_command(label="Add walls", command=lambda: _menu_add_wall(ctx))
    scenario_menu.add_command(label="Add doors", command=lambda: _menu_add_door(ctx))
    scenario_menu.add_command(label="Add sensors", command=lambda: _menu_add_sensor(ctx))
    scenario_menu.add_separator()
    scenario_menu.add_command(label="Recognize rooms", command=lambda: open_rooms_ui(ctx))

    # Settings menu (personal preferences, independent from scenario files)
    settings_menu = Menu(menu_bar, tearoff=0)
    menu_bar.add_cascade(label="Settings", menu=settings_menu)

    def save_setting(key, value):
        ctx.preferences[key] = value
        try:
            save_preferences(ctx.preferences)
        except OSError as error:
            messagebox.showerror(
                "Settings",
                f"The preference was applied but could not be saved:\n{error}",
            )

    def apply_light_style() -> None:
        current_palette = get_theme_palette(None)
        apply_theme_tree(ctx.window, "light")
        for themed_menu in getattr(ctx, "_theme_menus", ()):
            apply_theme_tree(themed_menu, "light")
        canvas._grid_background = current_palette["canvas"]
        canvas._grid_line_color = current_palette["grid"]
        redraw_grid = getattr(canvas, "_redraw_zoom_background", None)
        if callable(redraw_grid):
            redraw_grid()

    ctx._apply_theme = apply_light_style

    start_time_menu = Menu(settings_menu, tearoff=0)
    settings_menu.add_cascade(label="Start time", menu=start_time_menu)
    start_time_mode = tk.StringVar(
        value=str(ctx.preferences.get("start_time", "computer"))
    )
    start_time_menu.add_radiobutton(
        label="00:00",
        variable=start_time_mode,
        value="midnight",
        command=lambda: save_setting("start_time", start_time_mode.get()),
    )
    start_time_menu.add_radiobutton(
        label="Computer time",
        variable=start_time_mode,
        value="computer",
        command=lambda: save_setting("start_time", start_time_mode.get()),
    )

    starting_home_menu = Menu(settings_menu, tearoff=0)
    settings_menu.add_cascade(
        label="Starting home (next launch)",
        menu=starting_home_menu,
    )
    starting_home = tk.StringVar(
        value=str(ctx.preferences.get("starting_home", "empty"))
    )

    def select_starting_home_csv():
        filename = ask_open_file(
            title="Select the home CSV to load at startup",
            filetypes=[("Scenario CSV", "*.csv"), ("All files", "*.*")],
        )
        if not filename:
            return
        ctx.preferences["starting_home_path"] = str(Path(filename).resolve())
        starting_home.set("custom")
        save_setting("starting_home", "custom")
        rebuild_starting_home_menu()

    def rebuild_starting_home_menu():
        starting_home_menu.delete(0, tk.END)
        starting_home_menu.add_radiobutton(
            label="No home",
            variable=starting_home,
            value="empty",
            command=lambda: save_setting("starting_home", "empty"),
        )
        starting_home_menu.add_radiobutton(
            label="Default home (saved.csv)",
            variable=starting_home,
            value="default",
            command=lambda: save_setting("starting_home", "default"),
        )
        custom_path = str(ctx.preferences.get("starting_home_path", "")).strip()
        if custom_path:
            starting_home_menu.add_radiobutton(
                label=f"Custom home ({Path(custom_path).name})",
                variable=starting_home,
                value="custom",
                command=lambda: save_setting("starting_home", "custom"),
            )
        starting_home_menu.add_separator()
        starting_home_menu.add_command(
            label="Select CSV file...",
            command=select_starting_home_csv,
        )

    rebuild_starting_home_menu()

    start_manual_mode = tk.BooleanVar(
        value=bool(ctx.preferences.get("start_manual_mode", False))
    )
    settings_menu.add_checkbutton(
        label="Start in manual mode (next launch)",
        variable=start_manual_mode,
        command=lambda: save_setting(
            "start_manual_mode",
            bool(start_manual_mode.get()),
        ),
    )

    snap_to_grid = tk.BooleanVar(
        value=bool(ctx.preferences.get("snap_to_grid", True))
    )

    def toggle_grid_snap() -> None:
        enabled = bool(snap_to_grid.get())
        ctx.canvas._snap_to_grid = enabled
        save_setting("snap_to_grid", enabled)

    settings_menu.add_checkbutton(
        label="Grid mode (snap all objects)",
        variable=snap_to_grid,
        command=toggle_grid_snap,
    )

    show_grid = tk.BooleanVar(
        value=bool(ctx.preferences.get("show_grid", True))
    )

    def toggle_grid_visibility() -> None:
        visible = bool(show_grid.get())
        ctx.canvas._show_grid = visible
        redraw_grid = getattr(ctx.canvas, "_redraw_zoom_background", None)
        if callable(redraw_grid):
            redraw_grid()
        save_setting("show_grid", visible)

    settings_menu.add_checkbutton(
        label="Show grid",
        variable=show_grid,
        command=toggle_grid_visibility,
    )

    show_room_types = tk.BooleanVar(
        value=bool(ctx.preferences.get("show_room_types", True))
    )

    def toggle_room_types() -> None:
        from app.ui.rooms import refresh_rooms

        visible = bool(show_room_types.get())
        ctx.canvas._show_room_types = visible
        refresh_rooms(ctx, draw=True)
        save_setting("show_room_types", visible)

    settings_menu.add_checkbutton(
        label="Show room types",
        variable=show_room_types,
        command=toggle_room_types,
    )

    confirmations_menu = Menu(settings_menu, tearoff=0)
    settings_menu.add_cascade(
        label="Confirmations",
        menu=confirmations_menu,
    )
    confirmation_vars = {
        key: tk.BooleanVar(value=bool(ctx.preferences.get(key, True)))
        for key in CONFIRMATION_PREFERENCES
    }
    ctx._confirmation_vars = confirmation_vars
    for preference_key, label in CONFIRMATION_PREFERENCES.items():
        preference_var = confirmation_vars[preference_key]
        confirmations_menu.add_checkbutton(
            label=label,
            variable=preference_var,
            command=lambda key=preference_key, var=preference_var: save_setting(
                key,
                bool(var.get()),
            ),
        )

    def set_all_confirmations(enabled: bool) -> None:
        for preference_key, preference_var in confirmation_vars.items():
            preference_var.set(enabled)
            ctx.preferences[preference_key] = enabled
        try:
            save_preferences(ctx.preferences)
        except OSError as error:
            messagebox.showerror(
                "Settings",
                f"The preferences were applied but could not be saved:\n{error}",
            )

    confirmations_menu.add_separator()
    confirmations_menu.add_command(
        label="Enable all",
        command=lambda: set_all_confirmations(True),
    )
    confirmations_menu.add_command(
        label="Disable all",
        command=lambda: set_all_confirmations(False),
    )

    settings_menu.add_separator()
    show_pir_fov = tk.BooleanVar(
        value=bool(ctx.preferences.get("show_pir_fov", False))
    )

    def toggle_pir_fov():
        visible = bool(show_pir_fov.get())
        sensors, walls, doors = _current_pir_sources(ctx)
        set_pir_fov_visibility(ctx.canvas, sensors, visible, walls, doors)
        save_setting("show_pir_fov", visible)

    settings_menu.add_checkbutton(
        label="Show PIR fields of view",
        variable=show_pir_fov,
        command=toggle_pir_fov,
    )

    def open_pir_transparency_slider():
        dialog = tk.Toplevel(ctx.window)
        dialog.title("PIR transparency")
        dialog.resizable(False, False)
        dialog.transient(ctx.window)

        current = int(ctx.preferences.get("pir_fov_transparency", 50))
        transparency = tk.IntVar(value=current)
        value_label = tk.Label(dialog, text=f"{current}% transparent")
        value_label.pack(padx=20, pady=(16, 4))

        def preview(value):
            selected = set_pir_fov_transparency(ctx.canvas, int(float(value)))
            transparency.set(selected)
            value_label.config(text=f"{selected}% transparent")

        slider = tk.Scale(
            dialog,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            length=320,
            resolution=1,
            variable=transparency,
            command=preview,
            showvalue=False,
        )
        slider.pack(padx=20, pady=4)
        tk.Label(dialog, text="0% = opaque     100% = invisible").pack(
            padx=20,
            pady=(0, 12),
        )

        def save_and_close():
            selected = set_pir_fov_transparency(
                ctx.canvas,
                transparency.get(),
            )
            save_setting("pir_fov_transparency", selected)
            dialog.destroy()

        tk.Button(dialog, text="Save", command=save_and_close).pack(
            pady=(0, 16),
        )
        dialog.protocol("WM_DELETE_WINDOW", save_and_close)
        slider.focus_set()
        apply_theme_tree(dialog, ctx.preferences)

    settings_menu.add_command(
        label="PIR transparency...",
        command=open_pir_transparency_slider,
    )

    # Simulation menu
    sim_menu = Menu(menu_bar, tearoff=0)
    ctx.simulation_menu = sim_menu
    menu_bar.add_cascade(label="Simulation", menu=sim_menu)
    sim_menu.add_command(label="Automatic", command=lambda: launch_automatic_interface(ctx))
    sim_menu.add_command(
        label="Manual",
        command=lambda: activate_manual_interaction(ctx),
    )
    sim_menu.add_command(label="LLM Smart Meter", command=lambda: open_llm_smartmeter_ui(ctx))
    sim_menu.add_separator()
    sim_menu.add_command(label="Generate log", command=lambda: __import__("log").show_log(ctx.canvas, _sensor_store(ctx), ctx.load_active, _activity_log_store(ctx)))
    sim_menu.add_command(label="Activity Log", command=lambda: __import__("log").show_activity_log(_activity_log_store(ctx)))
    sim_menu.add_command(label="Generate graphs", command=lambda: __import__("graph").show_graphs(ctx.canvas, _sensor_store(ctx)))
    sim_menu.add_separator()
    sim_menu.add_command(
        label="Import sensor CSV from S3",
        command=lambda: import_csv_from_s3(ctx.window, ctx),
    )
    sim_menu.add_command(label="Import sensor CSV (local)", command=lambda: import_csv(ctx.window))
    sim_menu.add_command(label="Export simulations (CSV)", command=lambda: export_simulation_csv())
    sim_menu.add_separator()
    sim_menu.add_command(label="Bind by IP", command=lambda: open_bind_ip_ui(ctx.window, _sensor_store(ctx)))
    sim_menu.add_command(label="Bind by GPIO", command=lambda: open_bind_gpio_sensors_ui(ctx.window, _sensor_store(ctx)))

    ctx._theme_menus = [
        menu_bar,
        file_menu,
        scenario_menu,
        settings_menu,
        start_time_menu,
        starting_home_menu,
        confirmations_menu,
        sim_menu,
    ]
    apply_light_style()

    install_device_inspector(ctx, canvas_mode_text)



