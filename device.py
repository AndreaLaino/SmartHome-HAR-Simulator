import tkinter as tk
from tkinter import simpledialog, messagebox
from tkinter import ttk
from read import read_devices as device_file
from models import Device
from utils import raise_overlay_labels
from label_layout import create_map_label
from canvas_zoom import event_to_logical, to_canvas, to_canvas_length

devices = []


def get_device_params(device_type):
    params = {
        "Fridge": {"power": 150, "min_consumption": 50,  "max_consumption": 150},
        "Washing_Machine":   {"power": 500, "min_consumption": 300, "max_consumption": 500},
        "Oven":       {"power": 2000, "min_consumption": 1500, "max_consumption": 2000},
        "Coffee_Machine": {"power": 1000, "min_consumption": 100,  "max_consumption": 1300},
        "Computer":    {"power": 250, "min_consumption": 100, "max_consumption": 250},
        "Dishwasher": {"power": 1800, "min_consumption": 1000, "max_consumption": 1600}
    }
    return params.get(device_type, {"power": 100, "min_consumption": 50, "max_consumption": 100})



class DeviceDialog(simpledialog.Dialog):
    def body(self, master):
        tk.Label(master, text="Device name:").grid(row=0)
        tk.Label(master, text="Device type:").grid(row=1)
        self.device_name = tk.Entry(master)
        self.device_name.grid(row=0, column=1)
        self.device_type = ttk.Combobox(master, values=[
            "Fridge", "Washing_Machine", "Oven", "Coffee_Machine", "Computer", "Dishwasher"])
        self.device_type.grid(row=1, column=1)
        self.device_type.current(0)
        return self.device_name

    def validate(self):
        name = self.device_name.get().strip()
        if not name:
            messagebox.showwarning("Input not valid", "Device name cannot be empty.")
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
        return True

    def apply(self):
        name = self.device_name.get()
        type = self.device_type.get()
        params = get_device_params(type)
        power = params["power"]
        min_consumption = params["min_consumption"]
        max_consumption = params["max_consumption"]
        self.result = (name, type, power, min_consumption, max_consumption)



def add_device(canvas, event, load_active, on_changed=None):
    logical_x, logical_y = event_to_logical(canvas, event)
    x = int(logical_x)
    y = int(logical_y)
    dialog = DeviceDialog(canvas.master, "Add device")
    if dialog.result:
        name, type, power, min_consumption, max_consumption = dialog.result
        device = Device(
            name=name, 
            x=x, 
            y=y, 
            type=type, 
            power=power, 
            state=0,  # OFF
            min_consumption=min_consumption, 
            max_consumption=max_consumption, 
            current_consumption=0.0,
            consumption_direction=1
        )

        if load_active:
            device_file.append(device)
        else:
            devices.append(device)

        draw_device(canvas, device)
        if callable(on_changed):
            on_changed()



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
        text=f"{name} ({type})",
        fill=color,
        tags=(name, 'device', 'device_label'),
        kind="device",
        font=("Helvetica", 9),
        hover_target=marker_id,
    )
    raise_overlay_labels(canvas)
