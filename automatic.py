import tkinter as tk
from tkinter import messagebox, ttk
from app.io.safe_dialog import ask_directory, ask_open_file, ask_open_files, ask_save_file
import os
import pandas as pd

from app.context import AppContext

from graph import show_graphs_auto
from models import Sensor, Device

class ScrollableArea:
    def __init__(self, parent):
        self.canvas = tk.Canvas(parent, bd=1, relief=tk.SUNKEN)
        self.vbar = tk.Scrollbar(parent, orient=tk.VERTICAL, command=self.canvas.yview)
        self.hbar = tk.Scrollbar(parent, orient=tk.HORIZONTAL, command=self.canvas.xview)

        self.canvas.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")

        self.content = tk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        parent.grid_rowconfigure(0, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        self.content.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # Mouse wheel (Windows/Mac/Linux) + Shift for horizontal
        self._bind_mousewheel()

    def _on_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.window_id, width=event.width)

    def _bind_mousewheel(self):
        def _on_mousewheel(event):
            # Shift = horizontal scroll
            if event.state & 0x0001:  # Shift pressed
                delta = -1 if event.delta > 0 else 1
                self.canvas.xview_scroll(delta, "units")
            else:
                units = -1 if event.delta > 0 else 1
                self.canvas.yview_scroll(units, "units")

        # Windows/Mac
        self.canvas.bind_all("<MouseWheel>", _on_mousewheel)
        # Linux
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

    def scroll_to_top(self):
        self.canvas.yview_moveto(0)

    def update_scrollregion(self):
        self.canvas.update_idletasks()
        self._on_configure()

def _auto_state(ctx: AppContext) -> dict:
    return ctx.automatic_state

# folder mode
# Ask a folder and list supported data files (txt/csv/tsv) for Folder mode.
def select_folder(ctx: AppContext):
    state = _auto_state(ctx)

    folder_path = ask_directory(title="Select the folder with the data files")
    if not folder_path:
        return
    state["selected_folder_path"] = folder_path

    supported_extensions = (".txt", ".csv", ".tsv")
    state["files_in_folder"] = []
    display_list = []
    for f in os.listdir(folder_path):
        full_path = os.path.join(folder_path, f)
        if os.path.isfile(full_path) and f.lower().endswith(supported_extensions):
            state["files_in_folder"].append(f)
            display_list.append("• " + f)

    file_list_var = state.get("file_list_var")
    if file_list_var is not None:
        file_list_var.set(display_list)
    #messagebox.showinfo("Selected folder", f"You have chosen:\n{folder_path}\nFiles found:\n{files_in_folder}")

def read_timestamp_state_file(file_path: str):
    df = pd.read_csv(file_path, sep=None, engine='python', header=0)
    required_cols = {"time", "state"}
    if not required_cols.issubset(df.columns):
        raise ValueError(f"Requested rows missing {required_cols}")

    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time")
    df = df.drop_duplicates(subset=["time"], keep="last")

    time_str_list = df["time"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist()
    state_list = pd.to_numeric(df["state"], errors="coerce").tolist()
    return time_str_list, state_list

def clear_plot_area(ctx: AppContext):
    # Cleans the graphics area in the scroll frame.
    state = _auto_state(ctx)
    graph_canvas = state.get("graph_canvas_frame")
    if graph_canvas is None:
        return
    for w in graph_canvas.winfo_children():
        w.destroy()
    graph_area_obj_local = state.get("graph_area_obj")
    if graph_area_obj_local is not None:
        graph_area_obj_local.update_scrollregion()

def generate_graphs(ctx: AppContext):
    state = _auto_state(ctx)
    selected_folder_path = state.get("selected_folder_path")
    if not selected_folder_path:
        messagebox.showerror("Error", "No folder selected.")
        return

    file_listbox = state.get("file_listbox")
    selected_indices = file_listbox.curselection() if file_listbox is not None else ()
    if not selected_indices:
        messagebox.showerror("Error", "No file selected from the list.")
        return

    sensor_states = {}
    errors = []
    selected_keys = []

    for idx in selected_indices:
        file_name = state.get("files_in_folder", [])[idx]
        file_path = os.path.join(selected_folder_path, file_name)
        sensor_name = os.path.splitext(file_name)[0]
        try:
            t_list, s_list = read_timestamp_state_file(file_path)
            # filter NaNs keeping alignment
            clean_time, clean_state = [], []
            for t, s in zip(t_list, s_list):
                if pd.isna(s):
                    continue
                clean_time.append(t)
                clean_state.append(float(s))
            if not clean_time:
                continue
            sensor_states[sensor_name] = {"time": clean_time, "state": clean_state}
            selected_keys.append(sensor_name)
        except Exception as e:
            errors.append(f"{file_name}: {e}")

    if not sensor_states:
        msg = "No valid data found in the selected files."
        if errors:
            msg += "\n\nDetails:\n" + "\n".join(errors)
        messagebox.showerror("Error", msg)
        return

    clear_plot_area(ctx)
    try:
        show_graphs_auto(
            sensor_states=sensor_states,
            selected_keys=selected_keys,
            target_frame=state.get("graph_canvas_frame")
        )

        graph_area_obj_local = state.get("graph_area_obj")
        if graph_area_obj_local is not None:
            graph_area_obj_local.update_scrollregion()
    except Exception as e:
        messagebox.showerror("Error", f"Unable to generate graphs:\n{e}")

def clear_all(ctx: AppContext):
    # cleans graphs and resets selections (both modes)
    state = _auto_state(ctx)

    clear_plot_area(ctx)

    # reset folder mode
    file_list_var = state.get("file_list_var")
    if file_list_var is not None:
        file_list_var.set([])
    state["selected_folder_path"] = None
    state["files_in_folder"] = []

    # reset user path mode
    sensors_list_var = state.get("sensors_list_var")
    if sensors_list_var is not None:
        sensors_list_var.set([])
    state["sensors_in_csv"] = []
    state["interactions_df"] = None
    state["interactions_path"] = None

    messagebox.showinfo("Reset", "Graphs and selections deleted.")


# user path mode
# Load path; require columns {timestamp_sim,event_type,subject,name,x,y,value,extra}.
def select_path_csv(ctx: AppContext):
    state = _auto_state(ctx)

    path = ask_open_file(title="Select file", filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
    if not path:
        return

    try:
        df = pd.read_csv(path)
    except Exception as e:
        messagebox.showerror("Error", f"The CSV could not be read:\n{e}")
        return

    required = {"timestamp_sim", "event_type", "subject", "name", "x", "y", "value", "extra"}
    if not required.issubset(df.columns):
        messagebox.showerror("Error", f"The file does not contain the required columns:\n{sorted(required)}")
        return

    try:
        df["timestamp_sim"] = pd.to_datetime(df["timestamp_sim"])
    except Exception as e:
        messagebox.showerror("Error", f"timestamp_sim conversion error:\n{e}")
        return

    state["interactions_df"] = df.sort_values("timestamp_sim").reset_index(drop=True)
    state["interactions_path"] = path

    # extract sensors (event_type = sensor)
    sensor_rows = state["interactions_df"][state["interactions_df"]["event_type"] == "sensor"].copy()
    sensors = sensor_rows[["subject", "name"]].drop_duplicates().values.tolist()
    state["sensors_in_csv"] = [(subj, name) for subj, name in sensors]

    display = [f"• {subj} — {name}" for subj, name in state["sensors_in_csv"]]
    sensors_list_var = state.get("sensors_list_var")
    if sensors_list_var is not None:
        sensors_list_var.set(display)

    messagebox.showinfo("CSV loaded", f"{os.path.basename(path)}\nSensors found: {len(state['sensors_in_csv'])}")

# Build series per sensor; stable sort; aggregate by minute:
# Smart Meter -> MAX; others -> last event per minute.
def build_sensor_states_from_interactions(ctx: AppContext, selected_indices):
    state = _auto_state(ctx)
    sensor_states = {}
    selected_keys = []

    for idx in selected_indices:
        subj, name = state["sensors_in_csv"][idx]

        sub = state["interactions_df"][
            (state["interactions_df"]["event_type"] == "sensor")
            & (state["interactions_df"]["subject"] == subj)
            & (state["interactions_df"]["name"] == name)
        ][["timestamp_sim", "value"]].copy()

        if sub.empty:
            continue
        sub["timestamp_sim"] = pd.to_datetime(sub["timestamp_sim"], errors="coerce")
        sub = sub.dropna(subset=["timestamp_sim"])
        sub = sub.sort_values("timestamp_sim", kind="mergesort")  # mergesort = stable

        sub["value"] = pd.to_numeric(sub["value"], errors="coerce")
        sub = sub.dropna(subset=["value"])

        if subj == "Smart Meter":
            # if there are multiple events in the same minute take the maximum (more representative of minute consumption)
            sub = sub.groupby("timestamp_sim", as_index=False, sort=True)["value"].max()
        else:
            # For other sensors keep the last recorded event in that minute
            sub = sub[~sub["timestamp_sim"].duplicated(keep="last")]

        if sub.empty:
            continue

        sensor_key = f"{subj}:{name}"
        time_list = sub["timestamp_sim"].dt.strftime("%Y-%m-%d %H:%M:%S").tolist()
        state_list = sub["value"].astype(float).tolist()

        sensor_states[sensor_key] = {"time": time_list, "state": state_list}
        selected_keys.append(sensor_key)

    return sensor_states, selected_keys

def generate_graphs_from_csv_ctx(ctx: AppContext):
    state = _auto_state(ctx)
    if state.get("interactions_df") is None:
        messagebox.showerror("Error", "Load a simulation log first.")
        return

    sensors_listbox = state.get("sensors_listbox")
    selected_indices = sensors_listbox.curselection() if sensors_listbox is not None else ()
    if not selected_indices:
        messagebox.showerror("Error", "Select at least one sensor in the list.")
        return

    sensor_states, selected_keys = build_sensor_states_from_interactions(ctx, selected_indices)
    if not sensor_states:
        messagebox.showerror("Error", "No valid data for the selected sensors.")
        return

    clear_plot_area(ctx)
    try:
        show_graphs_auto(
            sensor_states=sensor_states,
            selected_keys=selected_keys,
            target_frame=state.get("graph_canvas_frame")
        )

        graph_area_obj = state.get("graph_area_obj")
        if graph_area_obj is not None:
            graph_area_obj.update_scrollregion()
    except Exception as e:
        messagebox.showerror("Error", f"Cannot generate graphs:\n{e}")


def export_logs_from_csv_ctx(ctx: AppContext):
    state = _auto_state(ctx)
    if state.get("interactions_df") is None:
        messagebox.showerror("Error", "Load a simulation log first.")
        return

    sensors_listbox = state.get("sensors_listbox")
    selected_indices = sensors_listbox.curselection() if sensors_listbox is not None else ()
    if not selected_indices:
        messagebox.showerror("Error", "Select at least one sensor.")
        return

    exported, skipped = 0, 0
    last_dir = state.get("SAVE_LAST_DIR") or os.path.expanduser("~")

    for idx in selected_indices:
        subj, name = state["sensors_in_csv"][idx]
        sub = state["interactions_df"][
            (state["interactions_df"]["event_type"] == "sensor")
            & (state["interactions_df"]["subject"] == subj)
            & (state["interactions_df"]["name"] == name)
        ][["timestamp_sim", "value"]].copy()
        if sub.empty:
            skipped += 1
            continue

        sub["timestamp_sim"] = pd.to_datetime(sub["timestamp_sim"], errors="coerce")
        sub = sub.dropna(subset=["timestamp_sim"]).sort_values("timestamp_sim", kind="mergesort")
        sub["value"] = pd.to_numeric(sub["value"], errors="coerce")
        sub = sub.dropna(subset=["value"])

        if subj == "Smart Meter":
            # consistent with graphs: max for each minute
            ts = sub.groupby("timestamp_sim", as_index=True, sort=True)["value"].max().to_frame()
        else:
            # last event of the minute
            sub = sub[~sub["timestamp_sim"].duplicated(keep="last")]
            ts = sub.set_index("timestamp_sim")[["value"]]

        if ts.index.nunique() == 1:
            idx0 = ts.index[0]
            full_index = pd.date_range(start=idx0, end=idx0 + pd.Timedelta(minutes=1), freq="1min")
        else:
            full_index = pd.date_range(ts.index.min(), ts.index.max(), freq="1min")
        ts = ts.reindex(full_index).ffill().reset_index()
        ts.columns = ["time", "value"]

        safe_name = f"{subj}_{name}".replace(" ", "_")
        default_name = f"{safe_name}_from_interactions.csv"

        out_path = ask_save_file(
            title=f"Choose file name for {subj} — {name}",
            initialdir=last_dir,
            filetypes=[("CSV", "*.csv")],
        )
        if not out_path:
            skipped += 1
            continue

        last_dir = os.path.dirname(out_path)
        state["SAVE_LAST_DIR"] = last_dir

        # Write the file
        try:
            ts.to_csv(out_path, index=False)
            exported += 1
        except Exception as e:
            messagebox.showerror("Error", f"Impossible to save {out_path}:\n{e}")
            skipped += 1

    messagebox.showinfo("Export",f"Exported {exported} files.")



#  Interface layout
# Build the two tabs; keep graph_canvas_frame/graph_area_obj pointing to the active tab.
def launch_automatic_interface(ctx: AppContext):
    """ Two-mode interface via tab (ttk.Notebook):
      - Tab 1: Folder with file (timestamp, status)
      - Tab 2: User path """
    window = ctx.window
    state = _auto_state(ctx)

    if getattr(ctx, "home_frame", None) is not None:
        ctx.home_frame.pack_forget()
    
    auto_frame = tk.Frame(window)
    auto_frame.pack(fill=tk.BOTH, expand=True)
    ctx.auto_frame = auto_frame

    window.title("Automatic Simulation")
    window.minsize(1100, 700)
    
    main_frame = tk.Frame(auto_frame, padx=16, pady=16)
    main_frame.pack(fill=tk.BOTH, expand=True)

    title_label = tk.Label(main_frame, text="Automatic Simulation Interface", font=("Helvetica", 18))
    title_label.pack(pady=(0,12))

    nb = ttk.Notebook(main_frame)
    nb.pack(fill=tk.BOTH, expand=True)

    # tab 1: Folder
    tab1 = tk.Frame(nb)
    nb.add(tab1, text="Folder Mode")

    section_a = tk.LabelFrame(tab1, padx=10, pady=10)
    section_a.pack(fill=tk.BOTH, expand=False, pady=(0,10))

    button_frame_a = tk.Frame(section_a)
    button_frame_a.pack(pady=5)

    tk.Button(button_frame_a, text="Select folder", command=lambda: select_folder(ctx), width=20).grid(row=0, column=0, padx=5, pady=5)
    tk.Button(button_frame_a, text="Generate graphs", command=lambda: generate_graphs(ctx), width=20).grid(row=0, column=1, padx=5, pady=5)
    tk.Button(button_frame_a, text="Delete", command=lambda: clear_all(ctx), width=20, bg="#f44336", fg="white").grid(row=0, column=2, padx=5, pady=5)

    listbox_frame = tk.Frame(section_a)
    listbox_frame.pack(pady=5, fill=tk.BOTH)

    tk.Label(listbox_frame, text="Files found in folder:").pack(anchor="w")

    file_list_var = tk.StringVar(value=[])
    file_listbox = tk.Listbox(listbox_frame, listvariable=file_list_var, selectmode=tk.MULTIPLE, width=50, height=6)
    file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    tk.Scrollbar(listbox_frame, orient=tk.VERTICAL, command=file_listbox.yview).pack(side=tk.RIGHT, fill=tk.Y)
    file_listbox.config(yscrollcommand=listbox_frame.winfo_children()[-1].set)
    state["file_list_var"] = file_list_var
    state["file_listbox"] = file_listbox

    graph_wrap1 = tk.LabelFrame(tab1, text="Graphs", padx=8, pady=8)
    graph_wrap1.pack(fill=tk.BOTH, expand=True)

    area1_container = tk.Frame(graph_wrap1)
    area1_container.pack(fill=tk.BOTH, expand=True)
    area1 = ScrollableArea(area1_container)
    graph_canvas_frame_tab1 = area1.content

    # tab 2: User path
    tab2 = tk.Frame(nb)
    nb.add(tab2, text="User path mode (log simulation)")

    section_b = tk.LabelFrame(tab2, padx=10, pady=10)
    section_b.pack(fill=tk.BOTH, expand=False, pady=(0,10))

    button_frame_b = tk.Frame(section_b)
    button_frame_b.pack(pady=5)

    tk.Button(button_frame_b, text="Select file", command=lambda: select_path_csv(ctx), width=20).grid(row=0, column=0, padx=5, pady=5)
    tk.Button(button_frame_b, text="Graphs from CSV", command=lambda: generate_graphs_from_csv_ctx(ctx), width=20).grid(row=0, column=1, padx=5, pady=5)
    tk.Button(button_frame_b, text="Export log from CSV", command=lambda: export_logs_from_csv_ctx(ctx), width=20).grid(row=0, column=2, padx=5, pady=5)
    tk.Button(button_frame_b, text="Delete", command=lambda: clear_all(ctx), width=20, bg="#f44336", fg="white").grid(row=0, column=3, padx=5, pady=5)

    sensors_list_frame = tk.Frame(section_b)
    sensors_list_frame.pack(pady=5, fill=tk.BOTH)

    tk.Label(sensors_list_frame, text="Sensors found:").pack(anchor="w")

    sensors_list_var = tk.StringVar(value=[])
    sensors_listbox = tk.Listbox(sensors_list_frame, listvariable=sensors_list_var, selectmode=tk.MULTIPLE, width=50, height=6)
    sensors_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    tk.Scrollbar(sensors_list_frame, orient=tk.VERTICAL, command=sensors_listbox.yview).pack(side=tk.RIGHT, fill=tk.Y)
    sensors_listbox.config(yscrollcommand=sensors_list_frame.winfo_children()[-1].set)
    state["sensors_list_var"] = sensors_list_var
    state["sensors_listbox"] = sensors_listbox

    graph_wrap2 = tk.LabelFrame(tab2, text="Graphs", padx=8, pady=8)
    graph_wrap2.pack(fill=tk.BOTH, expand=True)

    area2_container = tk.Frame(graph_wrap2)
    area2_container.pack(fill=tk.BOTH, expand=True)
    area2 = ScrollableArea(area2_container)
    graph_canvas_frame_tab2 = area2.content

    bottom_bar = tk.Frame(main_frame)
    bottom_bar.pack(fill=tk.X, pady=(8,0))

    def back_to_main():
        try:
            # Recreate the main interface from main.py
            if getattr(ctx, "auto_frame", None) is not None:
                ctx.auto_frame.destroy()
                ctx.auto_frame = None 

            from main import rebuild_main_interface
            rebuild_main_interface(ctx)  # Rebuild main UI
        except Exception as e:
            messagebox.showerror("Error", f"Unable to return to the initial interface:\n{e}")


    tk.Button(
        bottom_bar,
        text="← Return to the main interface",
        command=back_to_main,
        bg="#2196F3",
        fg="white",
        font=("Helvetica", 12)
    ).pack(side=tk.RIGHT, padx=6)

    # default tab 1 (folder)
    state["graph_canvas_frame"] = graph_canvas_frame_tab1
    state["graph_area_obj"] = area1

    def on_tab_change(_event=None):
        current = nb.index(nb.select())
        if current == 0:
            state["graph_canvas_frame"] = graph_canvas_frame_tab1
            state["graph_area_obj"] = area1
        else:
            state["graph_canvas_frame"] = graph_canvas_frame_tab2
            state["graph_area_obj"] = area2

    nb.bind("<<NotebookTabChanged>>", on_tab_change)
