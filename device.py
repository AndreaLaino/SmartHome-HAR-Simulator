import math
import tkinter as tk
from tkinter import simpledialog, messagebox
from tkinter import ttk
from read import read_devices as device_file
from models import Device, Fixture, FIXTURE_TYPES
from har.catalogue import categories_for, load_catalogue
from lighting import LIGHT_TYPES, NORMAL_LIGHT, SMART_LIGHT, ensure_light_switches
from utils import raise_overlay_labels
from label_layout import create_map_label
from canvas_zoom import event_to_logical, snap_logical_position, to_canvas, to_canvas_length

devices = []

# These are object types only. Their shared categories are defined separately
# in har.catalogue; neither list assigns a recognized activity.
DEVICE_TYPES = (
    "Fridge", "Washing_Machine", "Oven", "Microwave", "Toaster",
    "Coffee_Machine", "Computer", "TV", "PlayStation", "Dishwasher",
    NORMAL_LIGHT, SMART_LIGHT,
)


def device_types_by_category(catalogue, object_types=DEVICE_TYPES):
    """Group creatable types using their shared categories, including every role."""
    groups = {}
    unclassified = []
    for device_type in object_types:
        categories = categories_for({"type": device_type}, "device", catalogue)
        if not categories:
            unclassified.append(device_type)
        for category in categories:
            groups.setdefault(category, []).append(device_type)
    if unclassified:
        label = "Unclassified"
        while label in groups:
            label += " (no categories)"
        groups[label] = unclassified
    return {category: tuple(groups[category]) for category in sorted(groups)}


def get_device_params(device_type):
    """Default nominal watts, applicable only to electrical devices."""
    params = {
        "Fridge": {"power": 150, "min_consumption": 50,  "max_consumption": 150},
        "Washing_Machine":   {"power": 500, "min_consumption": 300, "max_consumption": 500},
        "Oven":       {"power": 2000, "min_consumption": 1500, "max_consumption": 2000},
        "Microwave": {"power": 1000, "min_consumption": 1000, "max_consumption": 1000},
        "Toaster": {"power": 900, "min_consumption": 900, "max_consumption": 900},
        "Coffee_Machine": {"power": 1000, "min_consumption": 100,  "max_consumption": 1300},
        "Computer":    {"power": 250, "min_consumption": 100, "max_consumption": 250},
        "TV": {"power": 100, "min_consumption": 100, "max_consumption": 100},
        "PlayStation": {"power": 200, "min_consumption": 200, "max_consumption": 200},
        "Dishwasher": {"power": 1800, "min_consumption": 1000, "max_consumption": 1600},
        "Lighting": {"power": 10, "min_consumption": 10, "max_consumption": 10},
        NORMAL_LIGHT: {"power": 10, "min_consumption": 10, "max_consumption": 10},
        SMART_LIGHT: {"power": 10, "min_consumption": 10, "max_consumption": 10},
    }
    if device_type in FIXTURE_TYPES:
        return {"power": None, "min_consumption": None, "max_consumption": None}
    return params.get(device_type, {"power": 100, "min_consumption": 50, "max_consumption": 100})



class DeviceDialog(simpledialog.Dialog):
    def body(self, master):
        tk.Label(master, text="Object name:").grid(row=0)
        tk.Label(master, text="Object kind:").grid(row=1)
        tk.Label(master, text="Category:").grid(row=2)
        tk.Label(master, text="Type:").grid(row=3)
        self.device_name = tk.Entry(master)
        self.device_name.grid(row=0, column=1)
        self.object_kind = ttk.Combobox(master, values=("Electrical device", "Non-electrical object"),
                                        state="readonly", width=28)
        self.object_kind.grid(row=1, column=1)
        self.object_kind.current(0)
        try:
            self.catalogue = load_catalogue()
            self.device_groups = device_types_by_category(self.catalogue)
        except (OSError, ValueError):
            self.catalogue = None
            self.device_groups = {}
            tk.Label(master, text="Categories unavailable; showing all devices.").grid(
                row=11, column=0, columnspan=2, padx=5, pady=5,
            )
        all_devices_label = "All devices"
        while all_devices_label in self.device_groups:
            all_devices_label += " (all types)"
        self.device_category = ttk.Combobox(
            master, values=(all_devices_label, *self.device_groups), state="readonly", width=28,
        )
        self.device_category.grid(row=2, column=1)
        self.device_category.current(0)
        self.device_type = ttk.Combobox(master, values=DEVICE_TYPES, width=28)
        self.device_type.grid(row=3, column=1)
        self.device_type.current(0)
        self.lighting_power_label = tk.Label(master, text="Lighting power (W):")
        self.lighting_power = tk.Entry(master)
        self.lighting_power.insert(0, str(get_device_params("Lighting")["power"]))
        self.lighting_meter_label = tk.Label(master, text="")
        self.lighting_control_label = tk.Label(master, text="Light control:")
        self.lighting_control = ttk.Combobox(master, values=("Manual", "Automatic"), state="readonly")
        self.lighting_control.current(0)
        self.lighting_control_hint = tk.Label(
            master, text="Manual adds an ON/OFF switch. Automatic follows room presence.",
        )
        self.add_sensor = tk.BooleanVar(master, False)
        self.add_sensor_checkbox = ttk.Checkbutton(master, text="Add associated sensor", variable=self.add_sensor,
                                                    command=self.on_add_sensor_changed)
        self.add_sensor_checkbox.grid(row=8, column=0, columnspan=2, sticky="w")
        self.sensor_type = ttk.Combobox(master, values=(), state="readonly", width=28)
        self.sensor_type_label = tk.Label(master, text="Sensor type:")
        self.sensor_hint = tk.Label(master, text="Placed nearby automatically; move it independently afterwards.")
        self.object_kind.bind("<<ComboboxSelected>>", self.on_object_kind_selected)
        self.device_category.bind("<<ComboboxSelected>>", self.on_category_selected)
        self.device_type.bind("<<ComboboxSelected>>", self.on_device_type_selected)
        self.on_device_type_selected()
        return self.device_name

    def on_object_kind_selected(self, _event=None):
        object_types = FIXTURE_TYPES if self.object_kind.get() == "Non-electrical object" else DEVICE_TYPES
        self.device_groups = device_types_by_category(self.catalogue, object_types) if self.catalogue else {}
        all_label = "All objects" if object_types == FIXTURE_TYPES else "All devices"
        while all_label in self.device_groups:
            all_label += " (all types)"
        self.device_category.configure(values=(all_label, *self.device_groups))
        self.device_category.current(0)
        self.on_category_selected()

    def on_add_sensor_changed(self):
        for widget in (self.sensor_type, self.sensor_type_label, self.sensor_hint):
            widget.grid_remove()
        if self.add_sensor.get():
            self.sensor_type_label.grid(row=9, column=0)
            self.sensor_type.grid(row=9, column=1)
            self.sensor_hint.grid(row=10, column=0, columnspan=2, padx=5, pady=5)

    def on_category_selected(self, _event=None):
        category = self.device_category.get()
        defaults = FIXTURE_TYPES if self.object_kind.get() == "Non-electrical object" else DEVICE_TYPES
        choices = self.device_groups.get(category, defaults)
        self.device_type.configure(values=choices)
        if self.device_type.get() not in choices:
            self.device_type.set(choices[0])
        self.on_device_type_selected()

    def on_device_type_selected(self, _event=None):
        if self.device_type.get() in LIGHT_TYPES:
            self.lighting_power_label.grid(row=4, column=0)
            self.lighting_power.grid(row=4, column=1)
            self.lighting_meter_label.configure(text=(
                "Smart Meter integrated; included in room power automatically."
                if self.device_type.get() == SMART_LIGHT
                else "Add a separate Smart Meter to include this light in room power."
            ))
            self.lighting_control_label.grid(row=5, column=0)
            self.lighting_control.grid(row=5, column=1)
            self.lighting_control_hint.grid(row=6, column=0, columnspan=2, padx=5, pady=5)
            self.lighting_meter_label.grid(row=7, column=0, columnspan=2, padx=5, pady=5)
        else:
            self.lighting_power_label.grid_remove()
            self.lighting_power.grid_remove()
            self.lighting_meter_label.grid_remove()
            self.lighting_control_label.grid_remove()
            self.lighting_control.grid_remove()
            self.lighting_control_hint.grid_remove()
        passive = self.object_kind.get() == "Non-electrical object" or self.device_type.get() in FIXTURE_TYPES
        choices = ("Weight", "Temperature", "PIR") if passive else ("Smart Meter", "Weight", "Temperature", "PIR")
        self.sensor_type.configure(values=choices)
        if self.sensor_type.get() not in choices:
            self.sensor_type.current(0)
        integrated = self.device_type.get() == SMART_LIGHT
        self.add_sensor_checkbox.configure(state="disabled" if integrated else "normal")
        if integrated:
            self.add_sensor.set(False)
        self.on_add_sensor_changed()

    def validate(self):
        name = self.device_name.get().strip()
        if not name:
            messagebox.showwarning("Input not valid", "Device name cannot be empty.")
            return False
        if self.object_kind.get() == "Non-electrical object" and self.device_type.get() not in FIXTURE_TYPES:
            messagebox.showwarning("Input not valid", "Select an object type from the list.")
            return False
        # avoid duplicates by considering both runtimes and file uploads
        for d in devices:
            if name == d.name:
                messagebox.showwarning("Input not valid", "Device name already present.")
                return False
        for d in device_file:
            if name == d.name:
                messagebox.showwarning("Input not valid", "Device name already present.")
                return False
        if self.device_type.get() in LIGHT_TYPES:
            try:
                power = float(self.lighting_power.get())
                if not math.isfinite(power) or power <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("Input not valid", "Lighting power must be a positive number in watts.")
                return False
        return True

    def apply(self):
        name = self.device_name.get().strip()
        type = self.device_type.get()
        params = get_device_params(type)
        power = params["power"]
        min_consumption = params["min_consumption"]
        max_consumption = params["max_consumption"]
        if type in LIGHT_TYPES:
            power = min_consumption = max_consumption = float(self.lighting_power.get())
        control = self.lighting_control.get().lower() if type in LIGHT_TYPES else "manual"
        sensor_type = self.sensor_type.get() if self.add_sensor.get() else None
        if type in FIXTURE_TYPES and sensor_type == "Smart Meter":
            sensor_type = "Weight"
        self.result = (name, type, power, min_consumption, max_consumption, control, sensor_type)



def add_device(canvas, event, load_active, on_changed=None, on_finished=None, *, rooms=(), on_created=None):
    logical_x, logical_y = event_to_logical(canvas, event)
    x, y = snap_logical_position(canvas, logical_x, logical_y)
    created = False
    try:
        dialog = DeviceDialog(canvas.master, "Add device / non-electrical object")
        if dialog.result:
            name, type, power, min_consumption, max_consumption, *control = dialog.result
            device = Fixture(name, x, y, type) if type in FIXTURE_TYPES else Device(
                name=name,
                x=x,
                y=y,
                type=type,
                power=power,
                state=0,  # OFF
                min_consumption=min_consumption,
                max_consumption=max_consumption,
                current_consumption=0.0,
                consumption_direction=1,
                light_control=control[0] if control else "manual",
            )

            from sensor import sensors, sensors_file, create_associated_sensor
            sensor_source = sensors_file if load_active else sensors
            associated_sensor = None
            if len(control) > 1 and control[1]:
                try:
                    object_source = device_file if load_active else devices
                    associated_sensor = create_associated_sensor(device, control[1], sensor_source, rooms,
                                                                 object_names=[obj.name for obj in object_source])
                except ValueError as error:
                    messagebox.showwarning("Cannot add sensor", str(error))
                    return False

            if load_active:
                device_file.append(device)
            else:
                devices.append(device)

            draw_device(canvas, device)
            if associated_sensor is not None:
                from utils import draw_sensor
                sensor_source.append(associated_sensor)
                draw_sensor(canvas, associated_sensor)
            created = True
            if callable(on_created):
                on_created(device, associated_sensor)
            if callable(on_changed):
                on_changed()
    finally:
        if callable(on_finished):
            on_finished(created)
    return created



def draw_device(canvas, device):
    name, x, y, type, state = device.name, device.x, device.y, device.type, device.state
    color = "red" if state == 0 else "green"
    canvas_x, canvas_y = to_canvas(canvas, x, y)
    radius = to_canvas_length(canvas, 5)
    marker_id = canvas.create_oval(
        canvas_x - radius,
        canvas_y - radius,
        canvas_x + radius,
        canvas_y + radius,
        fill=color,
        tags=(name, 'device'),
    )
    create_map_label(
        canvas,
        x,
        y,
        text=f"{name} ({type})" + (f" · {device.light_control.title()}" if type in LIGHT_TYPES else ""),
        fill=color,
        tags=(name, 'device', 'device_label'),
        kind="device",
        font=("Helvetica", 9),
        hover_target=marker_id,
    )
    for switch in ensure_light_switches(device) if type in LIGHT_TYPES and device.light_control == "manual" else []:
        switch_x, switch_y = to_canvas(canvas, switch.x, switch.y)
        switch_radius = to_canvas_length(canvas, 7)
        canvas.create_rectangle(
            switch_x - switch_radius, switch_y - switch_radius,
            switch_x + switch_radius, switch_y + switch_radius,
            fill=color, outline="#333333", width=2,
            tags=(name, "device", "light_switch", f"light_switch:{name}", f"light_switch_id:{switch.name}"),
        )
        create_map_label(
            canvas, switch.x, switch.y,
            text=f"Switch: {'ON' if state else 'OFF'}", fill=color,
            tags=(name, "device", "light_switch", f"light_switch:{name}",
                  f"light_switch_id:{switch.name}", f"light_switch_label:{name}"),
            font=("Helvetica", 9),
        )
    raise_overlay_labels(canvas)
