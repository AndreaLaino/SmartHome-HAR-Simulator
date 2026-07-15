import tkinter as tk
from tkinter import messagebox, ttk
from app.io.safe_dialog import ask_save_file
import csv
import json
import os
from datetime import datetime
from read import read_sensors
from sensor import sensors
from app.save_paths import (
    ensure_saves_dir,
    get_or_create_current_save_session,
    get_session_subdir,
)


def _activity_log_state(log_state: dict | None) -> dict:
    if log_state is None:
        return {
            "activity_log": [],
            "active_activities": {},
        }
    log_state.setdefault("activity_log", [])
    log_state.setdefault("active_activities", {})
    return log_state


def _interaction_log_state(house_state_or_state):
    if house_state_or_state is None:
        return {
            "interaction_session_dir": None,
            "interaction_file_path": None,
            "interaction_file": None,
        }

    if hasattr(house_state_or_state, "interaction_log_state"):
        state = house_state_or_state.interaction_log_state()
        state.setdefault("interaction_session_dir", None)
        state.setdefault("interaction_file_path", None)
        state.setdefault("interaction_file", None)
        return state

    house_state_or_state.setdefault("interaction_session_dir", None)
    house_state_or_state.setdefault("interaction_file_path", None)
    house_state_or_state.setdefault("interaction_file", None)
    return house_state_or_state


def log_activity_start(name, start_time, log_state):
    state = _activity_log_state(log_state)
    if name not in state["active_activities"]:
        state["active_activities"][name] = start_time
        print(f"[LOG] Start Activity: {name} at {start_time}")

def _append_or_merge_activity_entry(state: dict, name, start_time, end_time):
    for entry in reversed(state["activity_log"]):
        if entry["activity"] != name:
            continue
        if entry["end"] == start_time:
            entry["end"] = end_time
            return False
        break

    state["activity_log"].append({
        "activity": name,
        "start": start_time,
        "end": end_time,
    })
    return True

def _compact_activity_log(entries):
    compacted = []
    for entry in entries:
        merged = False
        for existing in reversed(compacted):
            if existing["activity"] != entry["activity"]:
                continue
            if existing["end"] == entry["start"]:
                existing["end"] = entry["end"]
                merged = True
            break
        if merged:
            continue
        compacted.append(dict(entry))
    return compacted

def log_activity_end(name, end_time, log_state):
    state = _activity_log_state(log_state)
    if name in state["active_activities"]:
        start_time = state["active_activities"].pop(name)
        _append_or_merge_activity_entry(state, name, start_time, end_time)
        print(f"[LOG] End activity: {name} at {end_time}")
    else:
        print(f"[WARNING] End of activity received for '{name}' but was not active.")

def log_end_of_simulation(end_time, log_state):
    state = _activity_log_state(log_state)
    # Close all still active tasks with the simulation end time
    for name, start in list(state["active_activities"].items()):
        _append_or_merge_activity_entry(state, name, start, end_time)
        print(f"[LOG] Force close activity: {name} at {end_time}")
    state["active_activities"].clear()

def save_activity_log(filename="activity_log.csv", log_state=None):
    state = _activity_log_state(log_state)
    try:
        with open(filename, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["activity", "start", "end"])
            for entry in _compact_activity_log(state["activity_log"]):
                writer.writerow([entry["activity"], entry["start"], entry["end"]])
        print(f"[LOG] Activity log saved in '{filename}'")
    except Exception as e:
        print(f"[ERROR] Saving failed: {e}")

def show_activity_log(log_state):
    state = _activity_log_state(log_state)
    log_window = tk.Toplevel()
    log_window.title("Activity log")

    tk.Label(log_window, text="Log of activities detected during the simulation").pack(pady=10)

    text_box = tk.Text(log_window, height=20, width=80)
    text_box.pack(pady=10)

    compacted_log = _compact_activity_log(state["activity_log"])

    if not compacted_log:
        text_box.insert(tk.END, "No activity recorded.\n")
    else:
        # print activity list in vertical column format
        for i, entry in enumerate(compacted_log, 1):
            text_box.insert(tk.END, f"Activity {i}:\n")
            text_box.insert(tk.END, f"  Name:  {entry['activity']}\n")
            text_box.insert(tk.END, f"  Start: {entry['start']}\n")
            text_box.insert(tk.END, f"  End:   {entry['end']}\n")
            text_box.insert(tk.END, "-" * 60 + "\n")

    def save():
        session_dir = get_or_create_current_save_session(suffix="logs")
        activity_dir = get_session_subdir("activities", session_dir)
        file_path = activity_dir / "activity_log.csv"
        save_activity_log(str(file_path), state)
        messagebox.showinfo("Success", f"Activity log saved in:\n{file_path}")

    tk.Button(log_window, text="Save activity log", command=save).pack(pady=10)


# sensor log

def show_log(canvas, sensor_states, load_active, log_state):
    state = _activity_log_state(log_state)
    def _align_len(lst, target_len, fill=None):
        #Makes 'lst' long 'target_len' by filling with 'fill' or cutting
        if lst is None:
            return [fill] * target_len
        out = list(lst)
        if len(out) < target_len:
            out.extend([fill] * (target_len - len(out)))
        elif len(out) > target_len:
            out = out[:target_len]
        return out

    def _sensor_metadata(sensor_name):
        # Build a short (type, subject, name) descriptor for the selected sensor.
        sensors_src = read_sensors if load_active else sensors
        for s in sensors_src:
            if s.name == sensor_name:
                return s.type, s.x, s.y
        return "UNKNOWN", 0, 0

    # Create a new tab with a table for the selected sensor and populate it from CSV.
    def build_tab_for_sensor(parent_notebook, sensore_name, sensor_data):
        frame = ttk.Frame(parent_notebook)
        parent_notebook.add(frame, text=sensore_name)

        # Header
        head = ttk.Frame(frame)
        head.pack(fill="x", padx=10, pady=(12, 6))
        ttk.Label(head, text=f"Sensor log: {sensore_name}", font=("Helvetica", 12, "bold")).pack(side="left")

        # Text area
        text_box = tk.Text(frame, height=22, width=100)
        text_box.pack(padx=10, pady=(0, 8), fill="both", expand=True)

        # Data
        time_list = sensor_data.get('time', []) or []
        state_list = sensor_data.get('state', []) or []
        sensor_type, x, y = _sensor_metadata(sensore_name)

        if not time_list:
            text_box.insert(tk.END, "No data available for this sensor.\n")
        else:
            if str(sensor_type).lower() == "smart meter":
                consumption_list = _align_len(sensor_data.get('consumption'), len(time_list), fill=None)
                text_box.insert(tk.END, "time\tstate\tconsumption\n")
                for t, s, c in zip(time_list, state_list, consumption_list):
                    text_box.insert(tk.END, f"{t}\t{s}\t{c}\n")
            else:
                text_box.insert(tk.END, "time\tstate\n")
                for t, s in zip(time_list, state_list):
                    text_box.insert(tk.END, f"{t}\t{s}\n")

        def save_log_tab():
            if str(sensor_type).lower() == "smart meter":
                consumption_list = _align_len(sensor_data.get('consumption'), len(time_list), fill=None)
            else:
                consumption_list = [None] * len(time_list)

            default_name = f"{sensore_name}_log.csv".replace(" ", "_")
            saves_dir = ensure_saves_dir()
            out_path = ask_save_file(
                title=f"Save log for {sensore_name}",
                initialdir=str(saves_dir),
                filetypes=[("CSV", "*.csv")]
            )
            if not out_path:
                return

            try:
                with open(out_path, mode="w", newline="", encoding="utf-8") as file:
                    writer = csv.writer(file)
                    writer.writerow(["time", "state", "consumption", "type", "x", "y"])
                    for t, s, c in zip(time_list, state_list, consumption_list):
                        writer.writerow([t, s, c, sensor_type, x, y])
                messagebox.showinfo("Success", f"Log saved in:\n{out_path}")
            except Exception as e:
                messagebox.showerror("Error", f"Impossible to save the file:\n{e}")

        # Action bar
        action_bar = ttk.Frame(frame)
        action_bar.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(action_bar, text="Save log", command=save_log_tab).pack(side="right")

        return frame

    def on_toggle_select_all():
        new_val = not all(var.get() for var in sensor_selection.values())
        for var in sensor_selection.values():
            var.set(new_val)

    def save_selected_logs():
        # save with default name in saves session)
        selected = [s for s, var in sensor_selection.items() if var.get()]
        if not selected:
            messagebox.showwarning("Warning", "Select at least one sensor.")
            return

        session_dir = get_or_create_current_save_session(suffix="logs")
        sensors_dir = get_session_subdir("sensors", session_dir)
        for sensor_name in selected:
            if sensor_name in sensor_states:
                sensor_data = sensor_states[sensor_name]
                time_list = sensor_data['time']
                state_list = sensor_data['state']

                sensor_type, x, y = _sensor_metadata(sensor_name)
                if str(sensor_type).lower() == "smart meter":
                    consumption_list = _align_len(sensor_data.get('consumption'), len(time_list), fill=None)
                else:
                    consumption_list = [None] * len(time_list)

                filename = f"{sensor_name}_log.csv".replace(" ", "_")
                file_path = sensors_dir / filename
                try:
                    with open(file_path, mode="w", newline="", encoding="utf-8") as file:
                        writer = csv.writer(file)
                        writer.writerow(["time", "state", "consumption", "type", "x", "y"])
                        for t, s, c in zip(time_list, state_list, consumption_list):
                            writer.writerow([t, s, c, sensor_type, x, y])
                except Exception as e:
                    messagebox.showerror("Error", f"Impossible to save '{filename}': {e}")
        messagebox.showinfo("Success", f"Log saved in:\n{session_dir}")

    def open_detail_window():
        selected = [s for s, var in sensor_selection.items() if var.get()]
        if not selected and not activity_var.get():
            messagebox.showwarning("Warning", "Select at least one sensor or include the Activity Log.")
            return

        # window with tabs
        win = tk.Toplevel()
        win.title("Sensor Log (multi-tab)")
        win.geometry("980x640")

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True)

        # Create a tab for each sensor
        for sensor_name in selected:
            if sensor_name not in sensor_states:
                continue
            sensor_data = sensor_states[sensor_name]
            build_tab_for_sensor(nb, sensor_name, sensor_data)

        # Activity tab
        if activity_var.get():
            act_frame = ttk.Frame(nb)
            nb.add(act_frame, text="Activity")

            ttk.Label(
                act_frame,
                text="Log of activities detected during the simulation",
                font=("Helvetica", 12, "bold")
            ).pack(pady=(12, 6), anchor="w", padx=10)

            text_box = tk.Text(act_frame, height=22, width=100)
            text_box.pack(padx=10, pady=(0, 8), fill="both", expand=True)

            if not state["activity_log"]:
                text_box.insert(tk.END, "No activity recorded.\n")
            else:
                text_box.insert(tk.END, "Activity\tStart\tEnd\n")
                for entry in state["activity_log"]:
                    text_box.insert(tk.END, f"{entry['activity']}\t{entry['start']}\t{entry['end']}\n")

            def save_activity_log_tab():
                saves_dir = ensure_saves_dir()
                out_path = ask_save_file(
                    title="Save activity log",
                    initialdir=str(saves_dir),
                    filetypes=[("CSV", "*.csv")]
                )
                if not out_path:
                    return
                try:
                    save_activity_log(out_path, state)
                    messagebox.showinfo("Success", f"Activity log saved in:\n{out_path}")
                except Exception as e:
                    messagebox.showerror("Error", f"Impossible to save activity log:\n{e}")

            action_bar = ttk.Frame(act_frame)
            action_bar.pack(fill="x", padx=10, pady=(0, 10))
            ttk.Button(action_bar, text="Save activity log", command=save_activity_log_tab).pack(side="right")

    # selection window for sensors
    log_window = tk.Toplevel()
    log_window.title("generate log")

    tk.Label(log_window, text="select the sensors for which the log should be generated:").pack(pady=10)

    sensor_selection = {}
    for sensor in sensor_states.keys():
        sensor_selection[sensor] = tk.BooleanVar(value=False)

    tk.Button(
        log_window,
        text="Select all / none",
        command=on_toggle_select_all,
        fg="blue"
    ).pack(anchor="w", padx=5)

    list_container = tk.Frame(log_window)
    list_container.pack(fill="both", expand=True, padx=10, pady=(5, 10))
    canvas_s = tk.Canvas(list_container, height=220)
    scroll_s = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=canvas_s.yview)
    inner = tk.Frame(canvas_s)

    inner.bind("<Configure>", lambda e: canvas_s.configure(scrollregion=canvas_s.bbox("all")))
    canvas_s.create_window((0, 0), window=inner, anchor="nw")
    canvas_s.configure(yscrollcommand=scroll_s.set)

    canvas_s.pack(side="left", fill="both", expand=True)
    scroll_s.pack(side="right", fill="y")

    for sensor, state in sensor_selection.items():
        tk.Checkbutton(inner, text=sensor, variable=state).pack(anchor="w")

    activity_var = tk.BooleanVar()
    tk.Checkbutton(log_window, text="Include Activity Log", variable=activity_var).pack(anchor="w", padx=5, pady=(8, 0))

    buttons_frame = tk.Frame(log_window)
    buttons_frame.pack(pady=10)
    tk.Button(buttons_frame, text="Open Preview", command=open_detail_window).grid(row=0, column=0, padx=5)
    tk.Button(buttons_frame, text="Save directly", command=save_selected_logs).grid(row=0, column=1, padx=5)

def start_interaction_log_session(house_state, session_label: str = ""):
    state = _interaction_log_state(house_state)
    suffix = "manual"
    if session_label:
        safe = str(session_label).replace(":", "").replace("/", "-").replace("\\", "-").strip()
        if safe:
            suffix = f"manual_{safe}"

    # Reuse the same save session folder for the current app run.
    session_dir = get_or_create_current_save_session(suffix=suffix)
    state["interaction_session_dir"] = str(session_dir)
    interactions_dir = get_session_subdir("interactions", session_dir)
    state["interaction_file_path"] = str(interactions_dir / "interactions.csv")
    file_exists = os.path.exists(state["interaction_file_path"])
    state["interaction_file"] = open(state["interaction_file_path"], mode="a", newline="", encoding="utf-8")
    import csv as _csv
    writer = _csv.writer(state["interaction_file"])
    # Write header only when creating the file for the first time.
    if not file_exists or os.path.getsize(state["interaction_file_path"]) == 0:
        writer.writerow(["timestamp_sim", "event_type", "subject", "name", "x", "y", "value", "extra"])
    state["interaction_file"].flush()
    print(f"[LOG] Interaction Session: {state['interaction_file_path']}")

def stop_interaction_log_session(house_state):
    state = _interaction_log_state(house_state)
    try:
        if state["interaction_file"]:
            state["interaction_file"].flush()
            state["interaction_file"].close()
            state["interaction_file"] = None
            print("[LOG] Interaction Session closed.")
    except Exception as e:
        print(f"[ERROR] Closing Interaction Session: {e}")

def append_interaction_row(house_state, row):
    state = _interaction_log_state(house_state)
    if state["interaction_file"] is None:
        # Ignore early events before the session file is opened.
        return
    try:
        import csv as _csv
        writer = _csv.writer(state["interaction_file"])
        writer.writerow(row)
        state["interaction_file"].flush()
    except Exception as e:
        print(f"[ERROR] Writing interaction log: {e}")

def log_move(house_state, timestamp_sim: str, x:int, y:int):
    #Record a movement of the avatar
    append_interaction_row(house_state, [timestamp_sim, "move", "user", "", int(x), int(y), "", ""])

def log_sensor_event(house_state, timestamp_sim: str, name:str, sensor_type:str, x:int, y:int, value, extra:str=""):
    #Record a sensor event
    append_interaction_row(house_state, [timestamp_sim, "sensor", sensor_type, name, int(x), int(y), value, extra])

def log_device_event(house_state, timestamp_sim: str, name:str, dev_type:str, x:int, y:int, state:int, extra:str=""):
    #Record a device event on/off
    append_interaction_row(house_state, [timestamp_sim, "device", dev_type, name, int(x), int(y), state, extra])

def log_llm_cycle_event(house_state, timestamp_sim: str, sensor_name: str, cycle: dict):
    payload = {
        "cycle_id": cycle.get("cycle_id"),
        "cluster": cycle.get("cluster"),
        "duration_minutes": cycle.get("duration_minutes"),
        "case_type": cycle.get("case_type", "normal"),
        "event_type": cycle.get("event_type", "normal"),
        "source_name": cycle.get("source_name", ""),
    }
    append_interaction_row(
        house_state,
        [
            timestamp_sim,
            "llm_cycle",
            payload["case_type"],
            sensor_name,
            "",
            "",
            payload["cycle_id"],
            json.dumps(payload, separators=(",", ":")),
        ],
    )

def log_door_event(house_state, timestamp_sim: str, door_id:str, x1:int, y1:int, x2:int, y2:int, state:int):
    #Record interaction with the door
    extra = f"({x1},{y1})-({x2},{y2})"
    append_interaction_row(house_state, [timestamp_sim, "door", "door", door_id, "", "", state, extra])
