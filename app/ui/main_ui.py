from __future__ import annotations

import tkinter as tk
from tkinter import Menu
from PIL import ImageTk, Image

from device import add_device
from door import draw_line_door
from point import add_point
from read import draw_points, draw_walls, draw_sensors, draw_devices, draw_doors
from sensor import add_sensor
from wall import draw_line_window
from graph import show_graphs
from automatic import launch_automatic_interface

from app.context import AppContext
from app.controllers.simulation import start_sim, enable_all_menus, exit_app
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
from app.controllers.llm_smartmeter import open_llm_smartmeter_ui
from app.ui.rooms import open_rooms_ui
from canvas_zoom import bind_canvas_pan, get_zoom, queue_trackpad_zoom, reset_zoom, zoom_in, zoom_out


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


def _load_image(canvas_obj: tk.Canvas, file_path: str):
    image = Image.open(file_path)
    max_size = 1200
    width, height = image.size
    scale = min(max_size / width, max_size / height)

    if scale < 1:
        resized_size = (int(width * scale), int(height * scale))
        image = image.resize(resized_size, Image.Resampling.LANCZOS)

    canvas_obj._background_source = image.copy()
    canvas_obj._background_cache = {}
    background_id = canvas_obj.create_image(0, 0, anchor=tk.NW, tags="background_image")

    def redraw_background(zoom):
        source = canvas_obj._background_source
        cache_key = round(float(zoom), 3)
        photo = canvas_obj._background_cache.get(cache_key)
        if photo is None:
            target_size = (
                max(1, round(source.width * zoom)),
                max(1, round(source.height * zoom)),
            )
            rendered = source.resize(target_size, Image.Resampling.NEAREST)
            photo = ImageTk.PhotoImage(rendered)
            canvas_obj._background_cache[cache_key] = photo
            while len(canvas_obj._background_cache) > 3:
                oldest_key = next(iter(canvas_obj._background_cache))
                if oldest_key == cache_key:
                    break
                canvas_obj._background_cache.pop(oldest_key)
        canvas_obj.itemconfigure(background_id, image=photo)
        canvas_obj.image = photo
        canvas_obj.tag_lower(background_id)

    canvas_obj._redraw_zoom_background = redraw_background
    redraw_background(1.0)
    canvas_obj.config(scrollregion=canvas_obj.bbox(tk.ALL))


def _menu_add_point(ctx: AppContext):
    ctx.canvas.bind("<Button-1>", lambda event: add_point(ctx.canvas, event, ctx.load_active))
    ctx.scenario_menu.entryconfig("Add points", state="disabled")
    ctx.scenario_menu.entryconfig("Add sensors", state="normal")
    ctx.scenario_menu.entryconfig("Add devices", state="normal")


def _menu_add_device(ctx: AppContext):
    from app.ui.rooms import refresh_rooms

    ctx.canvas.bind(
        "<Button-1>",
        lambda event: add_device(
            ctx.canvas,
            event,
            ctx.load_active,
            on_changed=lambda: refresh_rooms(ctx, draw=True),
        ),
    )
    ctx.scenario_menu.entryconfig("Add devices", state="disabled")
    ctx.scenario_menu.entryconfig("Add sensors", state="normal")
    ctx.scenario_menu.entryconfig("Add points", state="normal")


def _menu_add_sensor(ctx: AppContext):
    from app.ui.rooms import refresh_rooms

    ctx.canvas.bind(
        "<Button-1>",
        lambda event: add_sensor(
            ctx.canvas,
            event,
            ctx.load_active,
            on_changed=lambda: refresh_rooms(ctx, draw=True),
        ),
    )
    ctx.scenario_menu.entryconfig("Add sensors", state="disabled")
    ctx.scenario_menu.entryconfig("Add devices", state="normal")
    ctx.scenario_menu.entryconfig("Add points", state="normal")


def _menu_add_wall(ctx: AppContext):
    draw_line_window(ctx.canvas, ctx.window, ctx.load_active)
    enable_all_menus(ctx)


def _menu_add_door(ctx: AppContext):
    draw_line_door(ctx.canvas, ctx.window, ctx.load_active)
    enable_all_menus(ctx)


def build_home_ui(ctx: AppContext):
    # If already built, just show it
    if getattr(ctx, "home_frame", None) is not None:
        ctx.home_frame.pack(fill=tk.BOTH, expand=True)
        return

    home_frame = tk.Frame(ctx.window)
    home_frame.pack(fill=tk.BOTH, expand=True)
    ctx.home_frame = home_frame

    # Left: canvas with scrollbars
    image_frame = tk.Frame(home_frame, width=900, height=900)
    image_frame.pack(side=tk.LEFT, padx=10, pady=10, fill=tk.BOTH, expand=True)

    zoom_bar = tk.Frame(image_frame)
    zoom_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
    zoom_text = tk.StringVar(value="100%")

    h_scroll = tk.Scrollbar(image_frame, orient=tk.HORIZONTAL)
    h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
    v_scroll = tk.Scrollbar(image_frame, orient=tk.VERTICAL)
    v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

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

    _load_image(canvas, "images/grid_25.PNG")

    # Right: timer and activity panel (vertical layout)
    right_container = tk.Frame(home_frame, bg="lightgrey", width=420)
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
    timer_container = tk.Frame(right_container, bg="lightgrey", width=400, height=650)
    timer_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(4, 10))
    timer_container.pack_propagate(False)
    timer_frame = tk.Frame(timer_container, bg="lightgrey", width=400)
    timer_frame.pack(fill=tk.BOTH, expand=True)
    ctx.timer_frame = timer_frame

    # Activity at the bottom - fixed smaller space
    activity_container = tk.Frame(right_container, bg="lightgrey", width=400, height=150)
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

    # Simulation menu
    sim_menu = Menu(menu_bar, tearoff=0)
    ctx.simulation_menu = sim_menu
    menu_bar.add_cascade(label="Simulation", menu=sim_menu)
    sim_menu.add_command(label="Automatic", command=lambda: launch_automatic_interface(ctx))
    sim_menu.add_command(label="Manual", command=lambda: start_sim(ctx))
    sim_menu.add_command(label="LLM Smart Meter", command=lambda: open_llm_smartmeter_ui(ctx))
    sim_menu.add_separator()
    sim_menu.add_command(label="Generate log", command=lambda: __import__("log").show_log(ctx.canvas, _sensor_store(ctx), ctx.load_active, _activity_log_store(ctx)))
    sim_menu.add_command(label="Activity Log", command=lambda: __import__("log").show_activity_log(_activity_log_store(ctx)))
    sim_menu.add_command(label="Generate graphs", command=lambda: __import__("graph").show_graphs(ctx.canvas, _sensor_store(ctx)))
    sim_menu.add_separator()
    sim_menu.add_command(label="Import sensor CSV from S3", command=lambda: import_csv_from_s3(ctx.window))
    sim_menu.add_command(label="Import sensor CSV (local)", command=lambda: import_csv(ctx.window))
    sim_menu.add_command(label="Export simulations (CSV)", command=lambda: export_simulation_csv())
    sim_menu.add_separator()
    sim_menu.add_command(label="Bind by IP", command=lambda: open_bind_ip_ui(ctx.window, _sensor_store(ctx)))
    sim_menu.add_command(label="Bind by GPIO", command=lambda: open_bind_gpio_sensors_ui(ctx.window, _sensor_store(ctx)))



