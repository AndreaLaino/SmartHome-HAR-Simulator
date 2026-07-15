from __future__ import annotations

import os, csv, shutil, json, glob
from tkinter import messagebox, simpledialog
from app.io.safe_dialog import ask_open_file, ask_open_files, ask_save_file, ask_directory
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from point import points
from wall import walls
from sensor import sensors
from device import devices
from door import doors
from read import (
    read_coordinates_from_file, draw_points, draw_walls, draw_sensors, draw_devices, draw_doors
)
from app.context import AppContext
from app.logging_setup import setup_logging
from app.save_paths import ensure_houses_dir, ensure_saves_dir, ensure_devices_dir
from models import Point, Sensor, Device, Door, Wall


def _ask_overwrite_or_append(parent, dest_path: str) -> str:
    """Ask user if they want to overwrite or append when file exists.
    Returns: 'overwrite', 'append', or empty string if cancelled.
    """
    if not os.path.exists(dest_path):
        return 'overwrite'  # File doesn't exist, proceed normally

    result = messagebox.askyesnocancel(
        "File exists",
        f"The file '{os.path.basename(dest_path)}' already exists.\n\n"
        "Yes: Overwrite the file\n"
        "No: Append data to existing file\n"
        "Cancel: Cancel the operation",
        parent=parent
    )

    if result is True:
        return 'overwrite'
    elif result is False:
        return 'append'
    else:
        return ''  # Cancelled




logger = setup_logging("io.scenario")


def _point_to_tuple(p: Point) -> tuple:
    return (p.name, p.x, p.y)


def _sensor_to_tuple(s: Sensor) -> tuple:
    return (
        s.name, s.x, s.y, s.type, s.min_val, s.max_val, s.step, s.state,
        s.direction, s.consumption, s.associated_device
    )


def _device_to_tuple(d: Device) -> tuple:
    return (
        d.name, d.x, d.y, d.type, d.power, d.state,
        d.min_consumption, d.max_consumption, d.current_consumption,
        d.consumption_direction
    )


def _door_to_tuple(d: Door) -> tuple:
    return (d.x1, d.y1, d.x2, d.y2, d.state)


def _wall_to_point_names(wall: Wall, points_source: list[Point]) -> tuple[str, str] | None:
    point_a = next((p for p in points_source if p.x == wall.x1 and p.y == wall.y1), None)
    point_b = next((p for p in points_source if p.x == wall.x2 and p.y == wall.y2), None)
    if point_a is None or point_b is None:
        return None
    return point_a.name, point_b.name


def merge_smartmeter_files(logs_dir: str = "logs") -> bool:
    """
    Merge all smartmeter_a_*.csv files into a single smartmeter_a.csv file.
    Returns True if successful, False otherwise.
    """
    try:
        # Find all smartmeter_a_*.csv files
        pattern = os.path.join(logs_dir, "smartmeter_a_*.csv")
        input_files = sorted(glob.glob(pattern))

        if not input_files:
            logger.warning("No smartmeter_a_*.csv files found in %s", logs_dir)
            return False

        logger.info("Found %d files to merge", len(input_files))

        # Output file path
        output_file = os.path.join(logs_dir, "smartmeter_a.csv")

        # Write header and all data rows
        with open(output_file, 'w', newline='', encoding='utf-8') as outfile:
            writer = None
            total_rows = 0

            for i, filepath in enumerate(input_files):
                logger.info("Processing: %s", os.path.basename(filepath))

                with open(filepath, 'r', encoding='utf-8') as infile:
                    reader = csv.reader(infile)

                    # Read header
                    try:
                        header = next(reader)
                    except StopIteration:
                        logger.warning("File %s is empty", filepath)
                        continue

                    # Write header only on first file
                    if i == 0:
                        writer = csv.writer(outfile, quoting=csv.QUOTE_NONE, escapechar='\\')
                        writer.writerow(header)

                    # Write all data rows (skip header)
                    for row in reader:
                        writer.writerow(row)
                        total_rows += 1

        logger.info("Merge completed! Combined %d data rows into %s", total_rows, output_file)
        return True

    except Exception as e:
        logger.exception("Failed to merge smartmeter files: %s", e)
        return False


logger = setup_logging("io.scenario")


def _convert_timestamp(ts):
    """Convert Unix timestamp (seconds or milliseconds) to ISO format: YYYY-MM-DD HH:MM:SS.mmm"""
    if ts is None or ts == "":
        return ""
    try:
        if isinstance(ts, str):
            stripped = ts.strip()
            if not stripped:
                return ""
            try:
                numeric_ts = float(stripped)
            except ValueError:
                parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
                return parsed.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            ts = numeric_ts
        # If timestamp is in milliseconds (JS format), convert to seconds
        if isinstance(ts, (int, float)) and ts > 10000000000:
            ts = ts / 1000
        dt = datetime.fromtimestamp(float(ts))
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # con millisecondi
    except (TypeError, ValueError, OverflowError):
        return ""


def _first_present(data: dict, *keys):
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _parse_json_to_records(content: str, data_type: str) -> list:
    """Parse JSON lines to records based on data type."""
    records = []
    for line in content.strip().split('\n'):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            if data_type == "smartmeter":
                # SmartMeter format: {"ts":1765405428639,"apower":37.5,"voltage":229.7,"current":0.291,"energy_total":134.083}
                records.append({
                    'timestamp': _convert_timestamp(data.get('ts')),
                    'power': _first_present(data, 'apower', 'power', 'power_w'),
                    'voltage': _first_present(data, 'voltage', 'voltage_v'),
                    'current': _first_present(data, 'current', 'current_a'),
                    'energy': _first_present(data, 'energy_total', 'energy')
                })
            elif data_type == "dht":
                # DHT format: {"timestamp_iso": ..., "label": "t1", "gpio": 4, "temperature_c": 16.4, "humidity_rh": 74.6}
                records.append({
                    'timestamp': _convert_timestamp(_first_present(data, 'timestamp_iso', 'ts')),
                    'label': data.get('label'),
                    'gpio': data.get('gpio'),
                    'temperature': data.get('temperature_c'),
                    'humidity': data.get('humidity_rh')
                })
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON line: {line[:50]}... - {e}")
    return records


def _save_records_to_csv(records: list, filepath: str, data_type: str, device_name: str = "", ip: str = "", append_mode: bool = False) -> None:
    """Save records to CSV file. If append_mode is True, append to existing file (skip headers) and deduplicate by timestamp."""
    if not records:
        return

    file_exists = os.path.exists(filepath)

    # If appending, load existing data into dict (keyed by timestamp for dedup)
    existing_data = {}
    if append_mode and file_exists:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader, None)  # Skip header
                for row in reader:
                    if row and row[0] and row[0].strip():  # Validate non-empty timestamp
                        existing_data[row[0]] = row
        except Exception as e:
            logger.warning("Failed to read existing file for dedup: %s", e)

    # Collect new records into dict (keyed by timestamp)
    new_data = {}
    if data_type == "smartmeter":
        for r in records:
            if 'raw_csv' in r:
                continue
            ts = r.get('timestamp', '').strip()
            if ts:  # Validate non-empty timestamp
                new_data[ts] = [
                    ts,
                    device_name,
                    device_name,
                    ip,
                    r.get('power', ''),
                    r.get('voltage', ''),
                    r.get('current', '')
                ]
    elif data_type == "dht":
        for r in records:
            if 'raw_csv' in r:
                continue
            ts = r.get('timestamp', '').strip()
            if ts:  # Validate non-empty timestamp
                new_data[ts] = [
                    ts,
                    r.get('label', ''),
                    r.get('gpio', ''),
                    r.get('temperature', ''),
                    r.get('humidity', '')
                ]

    # Merge: existing_data + new_data (new data overwrites duplicates)
    merged_data = {**existing_data, **new_data}

    # Write merged data
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_NONE, escapechar='\\')

        # Write header
        if data_type == "smartmeter":
            writer.writerow(['timestamp_iso', 'device', 'device_id', 'ip', 'power_W', 'voltage_V', 'current_A'])
        elif data_type == "dht":
            writer.writerow(['timestamp_iso', 'label', 'gpio', 'temp_C', 'hum_%'])

        # Write all merged rows (sorted by timestamp)
        for ts in sorted(merged_data.keys()):
            writer.writerow(merged_data[ts])


def _load_scenario(ctx: AppContext, canvas, filename: str) -> None:
    """Load the scenario from 'filename' and draw it on the canvas."""
    (ctx.r_points,
     ctx.read_walls,
     ctx.read_sensors,
     ctx.read_devices,
     ctx.read_doors) = read_coordinates_from_file(filename)
    from read import read_room_overrides
    ctx.room_overrides.clear()
    ctx.room_overrides.update(read_room_overrides)

    ctx.load_active = True
    ctx.current_file = filename

    draw_points(ctx.r_points, canvas)
    draw_walls(ctx.read_walls, ctx.r_points, canvas)
    draw_sensors(ctx.read_sensors, canvas)
    draw_devices(ctx.read_devices, canvas)
    draw_doors(ctx.read_doors, canvas)
    from app.ui.rooms import refresh_rooms
    refresh_rooms(ctx, draw=True)

    # Auto-start real sensor loggers only after scenario sensors are available.
    try:
        from app.ui.bindings import autostart_bound_gpio_loggers, autostart_bound_ip_loggers
        autostart_bound_ip_loggers(ctx.house_state.sensor_states())
        autostart_bound_gpio_loggers(ctx.house_state.sensor_states())
    except Exception as e:
        logger.warning("Real sensor autostart after scenario load failed: %s", e)

    logger.info("Scenario loaded from %s", filename)


def load_scenario_from_file(ctx: AppContext, canvas) -> None:
    """Load the default save file: 'saved.csv'."""
    default_file = "saved.csv"
    if not os.path.exists(default_file):
        messagebox.showwarning("File not found", f"'{default_file}' not found.")
        return

    # Clear the current scenario (no prompt – this is a "quick load")
    _clear_scenario(ctx, canvas)
    _load_scenario(ctx, canvas, default_file)


def _write_scenario(ctx: AppContext, filename: str) -> None:
    """Write the current scenario to the given file."""
    if not messagebox.askyesno("Save", f"Do you want to save to:\n{filename}?"):
        return

    with open(filename, "w", newline='') as csvfile:
        csvwriter = csv.writer(csvfile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)

        # Points
        csvwriter.writerow(["Positions"])
        data = points if not ctx.load_active else ctx.r_points
        for p in data:
            name, x, y = _point_to_tuple(p)
            csvwriter.writerow([name, x, y])

        # Walls
        csvwriter.writerow([])
        csvwriter.writerow(["Walls"])
        wall_source = walls if not ctx.load_active else ctx.read_walls
        point_source = points if not ctx.load_active else ctx.r_points
        for wall in wall_source:
            names = _wall_to_point_names(wall, point_source)
            if names is not None:
                csvwriter.writerow([names[0], names[1]])

        # Sensors
        csvwriter.writerow([])
        csvwriter.writerow(["Sensors"])
        src = sensors if not ctx.load_active else ctx.read_sensors
        for s in src:
            (name, x, y, typ, min_val, max_val, step, state,
             direction, consumption, associated_device) = _sensor_to_tuple(s)
            min_val = float(min_val)
            max_val = float(max_val)
            step = float(step)
            state = float(state)
            if typ in ("PIR", "Weight"):
                state = 0.0
            consumption = float(consumption) if consumption is not None else "None"
            if typ == "Smart Meter":
                state = 0.0
                consumption = 0.0

            csvwriter.writerow([
                name, x, y, typ, min_val, max_val, step, state,
                direction if direction is not None else "None",
                consumption, associated_device
            ])

        # Devices
        csvwriter.writerow([])
        csvwriter.writerow(["Devices"])
        srcd = devices if not ctx.load_active else ctx.read_devices
        for d in srcd:
            (name, x, y, typ, power, state, min_c, max_c, curr_c, c_dir) = _device_to_tuple(d)
            state = 0
            curr_c = 0.0
            c_dir = 1
            csvwriter.writerow([
                name, int(x), int(y), typ, float(power), int(state),
                float(min_c), float(max_c), float(curr_c),
                c_dir if c_dir is not None else "None"
            ])

        # Doors
        csvwriter.writerow([])
        csvwriter.writerow(["Doors"])
        srcdoors = doors if not ctx.load_active else ctx.read_doors
        for d in srcdoors:
            x1, y1, x2, y2, state = _door_to_tuple(d)
            csvwriter.writerow([x1, y1, x2, y2, state])

        # Manual room labels. Geometry and automatic labels are recomputed.
        csvwriter.writerow([])
        csvwriter.writerow(["Rooms"])
        for room_id, room_type in sorted(ctx.room_overrides.items()):
            csvwriter.writerow([room_id, room_type])

    logger.info("Scenario saved successfully to %s.", filename)
    messagebox.showinfo("Saved", f"Scenario saved successfully to:\n{filename}")


def _clear_scenario(ctx: AppContext, canvas) -> None:
    """Internal helper: clear the scenario from canvas and memory, no confirmation."""
    from label_layout import forget_canvas_labels

    forget_canvas_labels(canvas)
    for tag in [
        'point', 'wall', 'sensor', 'line', 'device', 'door', 'fov',
        'room_overlay', 'map_tooltip', 'label_leader',
    ]:
        canvas.delete(tag)

    for lst in [points, walls, sensors, devices, doors,
                ctx.r_points, ctx.read_walls, ctx.read_sensors, ctx.read_devices, ctx.read_doors]:
        lst.clear()

    try:
        ctx.house_state.clear_runtime_state()
    except Exception:
        pass
    try:
        from sensor import reset_temperature_runtime_state
        reset_temperature_runtime_state()
    except Exception:
        pass
    ctx.rooms.clear()
    ctx.room_overrides.clear()

    ctx.load_active = False
    ctx.current_file = None

    logger.info("Scenario cleared from canvas and memory.")


def delete_scenario(ctx: AppContext, canvas) -> None:
    """Clear the current scenario from canvas and memory, with confirmation."""
    if not messagebox.askyesno(
        "Delete",
        "Are you sure you want to delete the current scenario?\nAll unsaved changes will be lost."
    ):
        return

    _clear_scenario(ctx, canvas)


def open_scenario(ctx: AppContext, canvas) -> None:
    """Open... – let the user choose the scenario file to open."""
    filename = ask_open_file(
        title="Open scenario",
        filetypes=[("Scenario files", "*.csv"), ("All files", "*.*")],
    )
    if not filename:
        return  # user cancelled

    # Clear current scenario silently, then load the new one
    _clear_scenario(ctx, canvas)
    _load_scenario(ctx, canvas, filename)



def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in (name or "").strip())

def _unique_path(path: str) -> str:
    """If it already exists, add _2, _3, ... before the extension."""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 2
    while True:
        p2 = f"{root}_{i}{ext}"
        if not os.path.exists(p2):
            return p2
        i += 1

def _ask_choice(parent, title: str, label: str, choices: list[str], default: str | None = None) -> str | None:
    win = tk.Toplevel(parent) if parent else tk.Toplevel()
    win.title(title)
    win.resizable(False, False)
    if parent:
        win.transient(parent)
    win.grab_set()

    tk.Label(win, text=label).grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")

    var = tk.StringVar(value=default or (choices[0] if choices else ""))
    cb = ttk.Combobox(win, textvariable=var, values=choices, state="readonly", width=25)
    cb.grid(row=1, column=0, padx=10, pady=5)
    if choices:
        cb.current(choices.index(default) if default in choices else 0)

    out = {"value": None}

    def ok():
        out["value"] = var.get()
        win.destroy()

    def cancel():
        out["value"] = None
        win.destroy()

    btns = tk.Frame(win)
    btns.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="e")
    tk.Button(btns, text="OK", command=ok).pack(side="left", padx=5)
    tk.Button(btns, text="Cancel", command=cancel).pack(side="left")

    win.wait_window()
    return out["value"]


def import_csv_from_s3(parent=None) -> None:
    """Import sensor CSV files from AWS S3 instead of local filesystem."""
    from app.io.aws_import import AWSS3Importer, BOTO3_AVAILABLE

    if not BOTO3_AVAILABLE:
        messagebox.showerror(
            "boto3 Required",
            "boto3 not installed.\n\nInstall with:\npip install boto3"
        )
        return

    # AWS connection dialog
    conn_win = tk.Toplevel(parent) if parent else tk.Toplevel()
    conn_win.title("Connect to AWS S3")
    conn_win.geometry("450x250")
    conn_win.resizable(False, False)
    if parent:
        conn_win.transient(parent)
    conn_win.grab_set()

    tk.Label(conn_win, text="AWS S3 Connection", font=("Arial", 12, "bold")).pack(pady=10)

    frm = tk.Frame(conn_win)
    frm.pack(padx=20, pady=10)

    tk.Label(frm, text="Access Key (optional):").grid(row=0, column=0, sticky="w", pady=5)
    access_entry = tk.Entry(frm, width=30, show="*")
    access_entry.grid(row=0, column=1, pady=5, padx=5)

    tk.Label(frm, text="Secret Key (optional):").grid(row=1, column=0, sticky="w", pady=5)
    secret_entry = tk.Entry(frm, width=30, show="*")
    secret_entry.grid(row=1, column=1, pady=5, padx=5)

    tk.Label(frm, text="Region:").grid(row=2, column=0, sticky="w", pady=5)
    region_entry = tk.Entry(frm, width=30)
    region_entry.insert(0, "eu-central-1")
    region_entry.grid(row=2, column=1, pady=5, padx=5)

    result = {"importer": None}

    def connect():
        access_key = access_entry.get().strip() or None
        secret_key = secret_entry.get().strip() or None
        region = region_entry.get().strip() or "eu-central-1"

        try:
            importer = AWSS3Importer(access_key, secret_key, region)
            result["importer"] = importer
            conn_win.destroy()
        except Exception as e:
            messagebox.showerror("Connection Error", f"Failed to connect:\n{str(e)}")

    def cancel():
        conn_win.destroy()

    btn_frm = tk.Frame(conn_win)
    btn_frm.pack(pady=10)
    tk.Button(btn_frm, text="Connect", command=connect, width=10).pack(side="left", padx=5)
    tk.Button(btn_frm, text="Cancel", command=cancel, width=10).pack(side="left", padx=5)

    conn_win.wait_window()

    importer = result.get("importer")
    if not importer:
        return

    # Bucket selection (try listing, or ask for manual entry)
    try:
        buckets = importer.list_buckets()
        if buckets:
            bucket = _ask_choice(
                parent,
                title="Select Bucket",
                label="Choose S3 bucket:",
                choices=buckets,
                default=buckets[0]
            )
        else:
            bucket = simpledialog.askstring(
                "Enter Bucket Name",
                "Enter the S3 bucket name:",
                parent=parent
            )
    except Exception as e:
        if "AccessDenied" in str(e) or "not authorized" in str(e):
            messagebox.showinfo(
                "Manual Entry Required",
                "Your IAM user lacks 's3:ListAllMyBuckets' permission.\n\n"
                "Please enter the bucket name manually."
            )
            bucket = simpledialog.askstring(
                "Enter Bucket Name",
                "Enter the S3 bucket name:",
                parent=parent
            )
        else:
            messagebox.showerror("Error", f"Failed to list buckets:\n{str(e)}")
            return

    if not bucket:
        return

    bucket = bucket.strip()

    # List CSV and JSON files in bucket
    objects = importer.list_objects(bucket)
    data_files = [obj for obj in objects if obj.endswith(('.csv', '.json', '.txt', '.log'))]

    if not data_files:
        messagebox.showwarning("No Data Files", f"No CSV/JSON files found in bucket '{bucket}'.")
        return

    # File selection dialog
    sel_win = tk.Toplevel(parent) if parent else tk.Toplevel()
    sel_win.title(f"Select data files from {bucket}")
    sel_win.geometry("600x450")
    if parent:
        sel_win.transient(parent)
    sel_win.grab_set()

    tk.Label(sel_win, text=f"Bucket: {bucket}", font=("Arial", 10, "bold")).pack(pady=5)
    tk.Label(sel_win, text="Select files to import (CSV or JSON):").pack(anchor="w", padx=10)

    listbox = tk.Listbox(sel_win, selectmode=tk.MULTIPLE, width=80, height=15)
    listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    for f in data_files:
        listbox.insert(tk.END, f)

    selected_files = []

    def select_files():
        sel = listbox.curselection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select at least one file.")
            return
        selected_files.extend([listbox.get(i) for i in sel])
        sel_win.destroy()

    btn_frame = tk.Frame(sel_win)
    btn_frame.pack(pady=10)
    tk.Button(btn_frame, text="Import Selected", command=select_files, width=15).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Cancel", command=sel_win.destroy, width=10).pack(side="left", padx=5)

    sel_win.wait_window()

    if not selected_files:
        return

    # Sensor type selection
    sim_type = _ask_choice(
        parent,
        title="Sensor type",
        label="Select the sensor type:",
        choices=["PIR", "Temperature", "Switch", "Smart Meter", "Weight"],
        default="Temperature",
    )
    if not sim_type:
        return

    prefix_by_type = {
        "PIR": "pir",
        "Temperature": "dht",
        "Smart Meter": "smartmeter",
        "Switch": "switch",
        "Weight": "weight",
    }
    prefix = prefix_by_type[sim_type]

    # Base name (same as local import)
    base_name = simpledialog.askstring(
        "Name",
        "Enter the name (e.g., t1, t2, sm_pc, ...)",
        parent=parent
    )
    if not base_name:
        return
    base_name = _sanitize(base_name)

    # Save original name (for device_name in CSV)
    original_name = base_name

    # Add prefix if not already present
    if sim_type == "Temperature" and not base_name.startswith("dht_"):
        base_name = f"dht_{base_name}"
    elif sim_type == "Smart Meter" and not base_name.startswith("smartmeter_"):
        base_name = f"smartmeter_{base_name}"

    # Import files
    logs_dir = str(ensure_devices_dir())

    # Warn if too many files
    if len(selected_files) > 100:
        proceed = messagebox.askyesno(
            "Many Files Selected",
            f"You selected {len(selected_files)} files.\n"
            f"This may take several minutes to download.\n\n"
            f"Continue?",
            parent=parent
        )
        if not proceed:
            return

    imported = 0
    all_records = []  # Collect all data from all files
    failed = 0

    # Create progress window
    progress_win = tk.Toplevel(parent) if parent else tk.Toplevel()
    progress_win.title("Importing from S3...")
    progress_win.geometry("500x150")
    if parent:
        progress_win.transient(parent)

    tk.Label(progress_win, text="Downloading and parsing files...", font=("Arial", 10)).pack(pady=10)
    progress_label = tk.Label(progress_win, text="0 / 0")
    progress_label.pack()
    progress_bar = ttk.Progressbar(progress_win, length=400, mode='determinate')
    progress_bar.pack(pady=10)
    progress_bar['maximum'] = len(selected_files)

    status_label = tk.Label(progress_win, text="", fg="blue")
    status_label.pack(pady=5)

    progress_win.update()

    for idx, s3_key in enumerate(selected_files):
        try:
            status_label.config(text=f"Downloading: {s3_key}")
            progress_win.update()

            content = importer.download_csv_file(bucket, s3_key)
            if not content:
                failed += 1
                continue

            # Check if JSON and convert to CSV format if needed
            if s3_key.endswith(('.json', '.txt', '.log')):
                records = _parse_json_to_records(content, prefix)
                all_records.extend(records)
            else:
                # It's already CSV, just append content
                all_records.append({'raw_csv': content})

            imported += 1
        except Exception as e:
            logger.error(f"Error processing {s3_key}: {e}")
            failed += 1
        finally:
            progress_bar['value'] = idx + 1
            progress_label.config(text=f"{idx + 1} / {len(selected_files)}")
            progress_win.update()

    progress_win.destroy()

    if not all_records:
        messagebox.showerror("Import Failed", f"No data could be extracted from selected files.\n\nSuccessful: {imported}\nFailed: {failed}")
        return

    # Ask for IP address if SmartMeter
    device_ip = ""
    if sim_type == "Smart Meter":
        device_ip = simpledialog.askstring(
            "IP Address",
            f"Enter the IP address for device '{base_name}' (optional):",
            parent=parent
        )
        device_ip = (device_ip or "").strip()

    # Save all data to a single CSV file
    dest_name = f"{base_name}.csv"
    dest_path = os.path.join(logs_dir, dest_name)

    # Ask user if they want to overwrite or append
    action = _ask_overwrite_or_append(parent, dest_path)
    if not action:
        return  # User cancelled

    append_mode = (action == 'append')

    try:
        _save_records_to_csv(all_records, dest_path, prefix, device_name=original_name, ip=device_ip, append_mode=append_mode)
        logger.info("Imported %d records from S3 to: %s (mode: %s)", len(all_records), dest_path, action)
        messagebox.showinfo(
            "Import Complete",
            f"Successfully imported {len(all_records)} records from {imported} file(s) to:\n{dest_path}\n"
            f"Mode: {action.capitalize()}\n\n"
            f"Failed: {failed} file(s)"
        )
    except Exception as e:
        logger.exception("Failed to save combined data: %s", e)
        messagebox.showerror("Import Failed", f"Error saving data: {e}")


def import_csv(parent=None) -> None:
    """Import sensor CSV files from local filesystem."""
    files = ask_open_files(
        title="Import CSV files",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
    )
    if not files:
        return

    # 1) enum of sensor types
    sim_type = _ask_choice(
        parent,
        title="Sensor type",
        label="Select the sensor type:",
        choices=["PIR", "Temperature", "Switch", "Smart Meter", "Weight"],
        default="PIR",
    )
    if not sim_type:
        return

    # 2) mapping type → prefix (temperature -> dht, etc)
    prefix_by_type = {
        "PIR": "pir",
        "Temperature": "dht",
        "Smart Meter": "smartmeter",
        "Switch": "switch",
        "Weight": "weight",
    }
    prefix = prefix_by_type[sim_type]

    # 3) name
    base_name = simpledialog.askstring(
        "Name",
        "Enter the name (e.g., t1, t2, sm_pc, ...)",
        parent=parent
    )
    if not base_name:
        return
    base_name = _sanitize(base_name)

    # Save original name (for device_name in CSV)
    original_name = base_name

    # Add prefix if not already present
    if sim_type == "Temperature" and not base_name.startswith("dht_"):
        base_name = f"dht_{base_name}"
    elif sim_type == "Smart Meter" and not base_name.startswith("smartmeter_"):
        base_name = f"smartmeter_{base_name}"

    logs_dir = str(ensure_devices_dir())

    dest_name = f"{base_name}.csv"
    dest_path = os.path.join(logs_dir, dest_name)

    # Ask user if they want to overwrite or append (only if destination exists)
    action = _ask_overwrite_or_append(parent, dest_path)
    if not action:
        return  # User cancelled

    append_mode = (action == 'append')

    # If appending, load existing data into dict (keyed by timestamp for dedup)
    existing_data = {}
    if append_mode and os.path.exists(dest_path):
        try:
            with open(dest_path, 'r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    if row and row[0] and row[0].strip():  # Validate timestamp
                        existing_data[row[0]] = row
        except Exception as e:
            logger.warning("Failed to read existing file for dedup: %s", e)

    # Collect all new data
    all_new_data = {}
    imported = 0
    for src in files:
        try:
            with open(src, 'r', newline='', encoding='utf-8') as src_file:
                reader = csv.reader(src_file)
                header = next(reader, None)  # Skip header
                for row in reader:
                    if row and row[0] and row[0].strip():  # Validate timestamp
                        all_new_data[row[0]] = row  # Keep latest within import

            logger.info("Imported %s -> %s (mode: %s)", src, dest_path, action if imported == 0 else 'append')
            imported += 1
        except Exception as e:
            logger.exception("Failed to import %s: %s", src, e)

    # Merge: existing_data + all_new_data (new data overwrites duplicates)
    merged_data = {**existing_data, **all_new_data}

    # Write merged data to file
    try:
        with open(dest_path, 'w', newline='', encoding='utf-8') as dest_file:
            writer = csv.writer(dest_file)
            # Write header
            if files:
                with open(files[0], 'r', newline='', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                    if header:
                        writer.writerow(header)
            # Write all merged rows (sorted by timestamp)
            for ts in sorted(merged_data.keys()):
                writer.writerow(merged_data[ts])
    except Exception as e:
        logger.exception("Failed to write merged data: %s", e)
        messagebox.showerror("Import Failed", f"Error writing data: {e}")
        return

    messagebox.showinfo("Import", f"Imported {imported} file(s) into {logs_dir}/\nMode: {action.capitalize()}\nDedup: timestamp duplicates kept as latest")
def export_simulation_csv() -> None:
    """Export the latest 'interactions.csv' from saves to a chosen location."""
    logs_root = str(ensure_saves_dir())
    if not os.path.isdir(logs_root):
        messagebox.showwarning("No logs", "Folder 'saves' not found.\nStart a manual simulation before exporting.")
        return

    candidates = []
    for root, _dirs, files in os.walk(logs_root):
        if "interactions.csv" in files:
            csv_path = os.path.join(root, "interactions.csv")
            candidates.append((os.path.getmtime(csv_path), csv_path))

    if not candidates:
        messagebox.showwarning("No file", "'interactions.csv' not found.\nStart a manual simulation and retry.")
        return

    candidates.sort(reverse=True)
    src_csv = candidates[0][1]

    dest = ask_save_file(
        title="Export simulation (CSV)",
        initialfile="simulation_interactions.csv",
        filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
    )
    if not dest:
        return

    try:
        shutil.copyfile(src_csv, dest)
        messagebox.showinfo("Exported", f"File exported to:\n{dest}")
        logger.info("Exported interactions CSV to %s", dest)
    except Exception as e:
        logger.exception("Export failed")
        messagebox.showerror("Error", f"Unable to export the file:\n{e}")


def save_scenario_as(ctx: AppContext) -> None:
    """Save As... – always ask for a new file path."""
    houses_dir = ensure_houses_dir()
    filename = ask_save_file(
        title="Save scenario as",
        defaultextension=".csv",
        initialfile="saved.csv",
        initialdir=str(houses_dir),
        filetypes=[("Scenario files", "*.csv"), ("All files", "*.*")],
    )
    if not filename:
        return

    ctx.current_file = filename
    _write_scenario(ctx, filename)


def save_scenario(ctx: AppContext) -> None:
    """Save – if a file is already open, overwrite it; otherwise behave like Save As."""
    if not ctx.current_file:
        save_scenario_as(ctx)
    else:
        _write_scenario(ctx, ctx.current_file)
