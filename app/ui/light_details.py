"""Live light information and independently positioned switch management."""

import tkinter as tk
from tkinter import ttk

from lighting import SMART_LIGHT, ensure_light_switches, with_integrated_light_meters
from room_recognition import point_in_polygon
from app.ui.theme import apply_theme_tree, get_widget_theme_mode


def open_light_details(ctx, light):
    from app.ui.device_inspector import (
        MapSelection, add_switch_to_light, associated_sensor_names, current_devices, current_sensors,
        delete_map_selection, set_light_control, show_device_csv, show_device_graphs,
    )

    windows = getattr(ctx, "_light_details_windows", None)
    if windows is None:
        windows = ctx._light_details_windows = {}
    previous = windows.get(id(light))
    if previous is not None and previous.winfo_exists():
        previous.lift()
        return previous

    window = tk.Toplevel(ctx.window)
    window.title(f"Light: {light.name}")
    window.geometry("740x690")
    window.minsize(540, 560)
    windows[id(light)] = window
    content = tk.Frame(window, padx=14, pady=12)
    content.pack(fill=tk.BOTH, expand=True)
    info = tk.Frame(content)
    info.pack(fill=tk.X)
    info.columnconfigure(1, weight=1)
    values = {}
    for row, label in enumerate(("Name", "Type", "State", "Position", "Rated power", "Current power",
                                 "Consumption range", "Room", "Metering", "Meter readings", "Associated sensors")):
        values[label] = tk.StringVar(master=window)
        tk.Label(info, text=f"{label}:", anchor="w").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=3)
        tk.Label(info, textvariable=values[label], anchor="w", justify=tk.LEFT, wraplength=480).grid(
            row=row, column=1, sticky="w", pady=3)

    controls = tk.Frame(content)
    controls.pack(fill=tk.X, pady=(10, 6))
    tk.Label(controls, text="Light control:").pack(side=tk.LEFT, padx=(0, 12))
    control = ttk.Combobox(controls, values=("Manual", "Automatic"), state="readonly", width=16)
    control.pack(side=tk.LEFT)

    tree = ttk.Treeview(content, columns=("x", "y", "state"), height=5, selectmode="browse")
    tree.heading("#0", text="Switch")
    tree.column("#0", width=320, stretch=True)
    for column, title in (("x", "X"), ("y", "Y"), ("state", "State")):
        tree.heading(column, text=title)
        tree.column(column, width=85, stretch=False)
    tree.pack(fill=tk.BOTH, expand=True, pady=(4, 8))
    tk.Label(content, text="In Select mode, drag a switch independently. Click it in Manual simulation to toggle the light.\n"
             "Automatic lights use room presence; their switches stay saved and become active in Manual control.",
             justify=tk.LEFT, wraplength=680, anchor="w").pack(fill=tk.X)
    buttons = tk.Frame(content)
    buttons.pack(fill=tk.X, pady=(10, 0))

    def add_switch():
        switch = add_switch_to_light(ctx, light)
        refresh()
        tree.selection_set(switch.name)
        tree.see(switch.name)

    def remove_switch():
        selected = set(tree.selection())
        switch = next((switch for switch in ensure_light_switches(light) if switch.name in selected), None)
        if switch is not None:
            delete_map_selection(ctx, MapSelection("light_switch", switch))
            refresh()

    add_button = tk.Button(buttons, text="Add switch", command=add_switch)
    add_button.pack(side=tk.LEFT)
    remove_button = tk.Button(buttons, text="Remove selected switch", command=remove_switch)
    remove_button.pack(side=tk.LEFT, padx=6)
    tk.Button(buttons, text="Close", command=window.destroy).pack(side=tk.RIGHT)
    graph_button = tk.Button(buttons, text="Graphs…", command=lambda: show_device_graphs(ctx, light))
    graph_button.pack(side=tk.RIGHT, padx=6)
    log_button = tk.Button(buttons, text="CSV / log…", command=lambda: show_device_csv(ctx, light))
    log_button.pack(side=tk.RIGHT)

    def update_remove_button(_event=None):
        remove_button.configure(state="normal" if tree.selection() else "disabled")

    def refresh():
        if not any(device is light for device in current_devices(ctx)):
            window.destroy()
            return
        room = next((room for room in getattr(ctx, "rooms", []) if point_in_polygon(light.x, light.y, room.polygon)), None)
        meters = [sensor for sensor in with_integrated_light_meters(current_sensors(ctx), current_devices(ctx))
                  if sensor.type == "Smart Meter" and sensor.associated_device == light.name]
        data = {
            "Name": light.name, "Type": light.type, "State": "ON" if light.state else "OFF",
            "Position": f"({light.x}, {light.y})", "Rated power": f"{light.power:g} W",
            "Current power": f"{light.current_consumption:g} W",
            "Consumption range": f"{light.min_consumption:g} – {light.max_consumption:g} W",
            "Room": f"{room.room_type} ({room.room_id})" if room is not None else "Outside recognized rooms",
            "Metering": "Integrated Smart Meter" if light.type == SMART_LIGHT else "External Smart Meter" if meters else "No Smart Meter",
            "Meter readings": ", ".join(f"{meter.name}: {float(meter.consumption or 0):g} W" for meter in meters) or "None",
            "Associated sensors": ", ".join(associated_sensor_names(ctx, light)) or "None",
        }
        for name, variable in values.items():
            variable.set(data[name])
        if control.get().lower() != light.light_control:
            control.set(light.light_control.title())
        switches = ensure_light_switches(light)
        names = {switch.name for switch in switches}
        for item in tree.get_children():
            if item not in names:
                tree.delete(item)
        for switch in switches:
            state = "Inactive" if light.light_control != "manual" else "ON" if light.state else "OFF"
            row_values = (switch.x, switch.y, state)
            if tree.exists(switch.name):
                tree.item(switch.name, text=switch.name, values=row_values)
            else:
                tree.insert("", tk.END, iid=switch.name, text=switch.name, values=row_values)
        add_button.configure(state="normal" if light.light_control == "manual" else "disabled")
        state = "normal" if meters else "disabled"
        graph_button.configure(state=state)
        log_button.configure(state=state)
        update_remove_button()

    def change_control(_event=None):
        set_light_control(ctx, light, control.get().lower())
        refresh()

    tree.bind("<<TreeviewSelect>>", update_remove_button)
    control.bind("<<ComboboxSelected>>", change_control)
    refresh_job = None

    def poll():
        nonlocal refresh_job
        refresh_job = None
        refresh()
        if window.winfo_exists():
            refresh_job = window.after(500, poll)

    def close(event):
        if event.widget is window:
            if refresh_job is not None:
                window.after_cancel(refresh_job)
            windows.pop(id(light), None)

    window.bind("<Destroy>", close, add="+")
    # Named widget references also make integration checks independent of layout.
    window._light_info_values = values
    window._light_switch_tree = tree
    poll()
    apply_theme_tree(window, get_widget_theme_mode(ctx.window))
    return window
