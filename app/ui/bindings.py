from __future__ import annotations

import os, re, json, tkinter as tk
from tkinter import messagebox
from typing import Dict, Any

from sensor import sensors
from read import read_sensors, read_devices
from app.logging_setup import setup_logging

logger = setup_logging("ui.bindings")

# ---------- shared helpers ----------

def _sensor_type(name: str, sensor_states: dict) -> str | None:
    t = (sensor_states.get(name) or {}).get("type")
    if t:
        return t
    try:
        for s in (sensors or []):
            if s.name == name:
                return s.type
    except Exception:
        pass
    try:
        for s in (read_sensors or []):
            if s.name == name:
                return s.type
    except Exception:
        pass
    return None

def _is_smart_meter_sensor(name: str, sensor_states: dict) -> bool:
    return _sensor_type(name, sensor_states) == "Smart Meter"

def _is_sensor_type(name: str, sensor_states: dict, wanted_type: str) -> bool:
    return _sensor_type(name, sensor_states) == wanted_type

def _all_sensor_names(sensor_states: dict) -> list[str]:
    names = set()
    try:
        names.update((sensor_states or {}).keys())
    except Exception:
        pass
    try:
        for s in (sensors or []):
            names.add(s.name)
    except Exception:
        pass
    try:
        for s in (read_sensors or []):
            names.add(s.name)
    except Exception:
        pass
    return sorted(names)

def _normalize_device_label(value: str) -> str:
    txt = (value or "").replace("_", " ").strip().lower()
    return re.sub(r"\s+", " ", txt)

def _smart_meter_display_name(sensor_name: str, sensor_states: dict) -> str:
    """Return a human-readable device label for Smart Meter logs."""
    data = (sensor_states or {}).get(sensor_name) or {}
    assoc = (data.get("associated_device") or "").strip()

    if not assoc:
        try:
            for s in (sensors or []):
                if s.name == sensor_name and getattr(s, "associated_device", None) not in (None, "", "None"):
                    assoc = str(s.associated_device).strip()
                    break
        except Exception:
            pass

    if not assoc:
        try:
            for s in (read_sensors or []):
                if s.name == sensor_name and getattr(s, "associated_device", None) not in (None, "", "None"):
                    assoc = str(s.associated_device).strip()
                    break
        except Exception:
            pass

    if assoc:
        try:
            for d in (read_devices or []):
                if d.name == assoc:
                    return _normalize_device_label(str(d.type))
        except Exception:
            pass

        try:
            from device import devices as runtime_devices
            for d in (runtime_devices or []):
                if hasattr(d, "name") and hasattr(d, "type") and d.name == assoc:
                    return _normalize_device_label(str(d.type))
        except Exception:
            pass

        return _normalize_device_label(assoc)

    # Heuristic fallback: smart meter ids usually follow "sm_<device_name>".
    # Example: sm_pc -> device "pc" -> type "Computer".
    compact = (sensor_name or "").strip()
    if compact.startswith("sm_") and len(compact) > 3:
        guessed_dev_name = compact[3:]

        try:
            for d in (read_devices or []):
                if d.name == guessed_dev_name:
                    return _normalize_device_label(str(d.type))
        except Exception:
            pass

        try:
            from device import devices as runtime_devices
            for d in (runtime_devices or []):
                if hasattr(d, "name") and hasattr(d, "type") and d.name == guessed_dev_name:
                    return _normalize_device_label(str(d.type))
        except Exception:
            pass

    return _normalize_device_label(sensor_name)

def _load_sensor_map_json(path="sensor_map.json") -> Dict[str, Any]:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
    except Exception:
        logger.exception("Failed to load sensor_map.json")
    return {}

def _save_sensor_map_json(mapping: dict, path="sensor_map.json"):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        messagebox.showinfo("Saved", f"Mapping saved in {path}")
    except Exception as e:
        logger.exception("Failed to save sensor_map.json")
        messagebox.showerror("Error", f"Unable to save {path}:\n{e}")


def autostart_bound_ip_loggers(sensor_states: dict):
    """Start Smart Meter loggers from persisted IP mappings in sensor_map.json."""
    mapping = _load_sensor_map_json()
    if not mapping:
        return

    sm_sensor_names = {
        n for n in _all_sensor_names(sensor_states)
        if _is_smart_meter_sensor(n, sensor_states)
    }

    try:
        from app.hardware.smartmeter import start_logger, csv_path_for_device
    except Exception as e:
        logger.warning("Cannot import smartmeter logger APIs: %s", e)
        return

    started = 0
    for sensor_name, cfg in mapping.items():
        if sm_sensor_names and sensor_name not in sm_sensor_names:
            # Ignore stale entries not present in the currently loaded scenario.
            continue

        if not isinstance(cfg, dict) or cfg.get("by") != "ip":
            continue

        ip = (cfg.get("value") or "").strip()
        if not ip:
            continue

        try:
            display_name = _smart_meter_display_name(sensor_name, sensor_states)
            start_logger(
                device_name=display_name,
                ip=ip,
                interval=int(cfg.get("interval", 10) or 10),
                device_id=sensor_name,
                csv_path=csv_path_for_device(sensor_name),
            )
            started += 1
        except Exception as e:
            logger.warning("Cannot auto-start smartmeter logger for %s (%s): %s", sensor_name, ip, e)

    if started:
        logger.info("[SmartMeter] auto-started %d logger(s) from sensor_map.json", started)


def autostart_bound_gpio_loggers(sensor_states: dict):
    """Start DHT, PIR, Switch, and Weight GPIO loggers from persisted mappings."""
    mapping = _load_sensor_map_json()
    if not mapping:
        return

    valid_sensor_names = {
        n for n in _all_sensor_names(sensor_states)
        if _sensor_type(n, sensor_states) in GPIO_BINDABLE_TYPES
    }

    try:
        from app.hardware.real_sensors import start_bound_logger
    except Exception as e:
        logger.warning("Cannot import GPIO logger APIs: %s", e)
        return

    started = 0
    for sensor_name, cfg in mapping.items():
        if valid_sensor_names and sensor_name not in valid_sensor_names:
            continue
        if not isinstance(cfg, dict) or cfg.get("by") not in ("dht", "gpio"):
            continue

        try:
            gpio = int(cfg.get("gpio"))
        except Exception:
            continue

        kind = (cfg.get("kind") or _binding_kind_for_type(_sensor_type(sensor_name, sensor_states) or "")).strip()
        try:
            start_bound_logger(
                sensor_label=sensor_name,
                kind=kind,
                gpio=gpio,
                interval=int(cfg.get("interval", 10) or 10),
                pull_up=bool(cfg.get("pull_up", False)),
                active_low=bool(cfg.get("active_low", False)),
            )
            started += 1
        except Exception as e:
            logger.warning("Cannot auto-start %s logger for %s on GPIO %s: %s", kind, sensor_name, gpio, e)

    if started:
        logger.info("[GPIO] auto-started %d logger(s) from sensor_map.json", started)

# ---------- Smart Meter (IP) ----------

def open_bind_ip_ui(root_win: tk.Tk, sensor_states: dict):
    """
    Associate ONLY 'Smart Meter' sensors to a real IP.
    Persist as: { "sensor_name": {"by":"ip","value":"10.195.1.18"} }
    Optionally auto-start the logger.
    """
    win = tk.Toplevel(root_win)
    win.title("Bind by IP")
    win.geometry("760x520")

    all_names = _all_sensor_names(sensor_states)
    sm_names = [n for n in all_names if _is_smart_meter_sensor(n, sensor_states)]
    current = _load_sensor_map_json()

    container = tk.Frame(win); container.pack(fill="both", expand=True, padx=10, pady=10)
    canv = tk.Canvas(container); canv.pack(side="left", fill="both", expand=True)
    vsb = tk.Scrollbar(container, orient="vertical", command=canv.yview); vsb.pack(side="right", fill="y")
    canv.configure(yscrollcommand=vsb.set)
    inner = tk.Frame(canv); canv.create_window((0,0), window=inner, anchor="nw")

    inner.grid_columnconfigure(0, minsize=260)
    inner.grid_columnconfigure(1, minsize=220)
    inner.grid_columnconfigure(2, minsize=120)
    tk.Label(inner, text="Sensor (Smart Meter)", anchor="w", font=("Helvetica", 10, "bold")).grid(row=0, column=0, sticky="ew", padx=(0, 12), pady=(0, 8))
    tk.Label(inner, text="Real IP", anchor="w", font=("Helvetica", 10, "bold")).grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(0, 8))
    tk.Label(inner, text="Interval (s)", anchor="w", font=("Helvetica", 10, "bold")).grid(row=0, column=2, sticky="ew", pady=(0, 8))

    rows = []
    for row_idx, name in enumerate(sm_names, start=1):
        tk.Label(inner, text=name, anchor="w").grid(row=row_idx, column=0, sticky="ew", padx=(0, 12), pady=4)

        init_ip = ""
        init_interval = "10"
        v = current.get(name)
        if isinstance(v, dict) and v.get("by") == "ip":
            init_ip = v.get("value") or ""
            init_interval = str(v.get("interval") or "10")

        var_ip = tk.StringVar(value=init_ip)
        var_interval = tk.StringVar(value=init_interval)
        tk.Entry(inner, textvariable=var_ip, width=24).grid(row=row_idx, column=1, sticky="ew", padx=(0, 12), pady=4)
        tk.Entry(inner, textvariable=var_interval, width=12).grid(row=row_idx, column=2, sticky="ew", pady=4)
        rows.append((name, var_ip, var_interval))

    inner.bind("<Configure>", lambda e: canv.configure(scrollregion=canv.bbox("all")))

    btns = tk.Frame(win); btns.pack(fill="x", padx=10, pady=(6,10))
    auto_start_var = tk.BooleanVar(value=True)
    tk.Checkbutton(btns, text="Auto-start logger for associated IPs", variable=auto_start_var).pack(side="left")

    def _save_and_close():
        new_map = _load_sensor_map_json()
        changed = []  

        for name, var_ip, var_interval in rows:
            ip = var_ip.get().strip()
            if ip:
                try:
                    interval = int(var_interval.get().strip() or "10")
                except Exception:
                    messagebox.showerror("Invalid interval", f"{name}: interval must be an integer number of seconds.")
                    return
                if interval < 1:
                    messagebox.showerror("Invalid interval", f"{name}: interval must be >= 1.")
                    return
                new_map[name] = {"by": "ip", "value": ip, "interval": interval}
                changed.append((name, ip, interval))  
            else:
                if name in new_map:
                    del new_map[name]

        _save_sensor_map_json(new_map)

        if auto_start_var.get():
            from app.hardware.smartmeter import start_logger, csv_path_for_device
            for sensor_name, ip, interval in changed:
                try:
                    display_name = _smart_meter_display_name(sensor_name, sensor_states)
                    if display_name == _normalize_device_label(sensor_name):
                        logger.warning(
                            "Smart Meter %s has no associated device; using sensor name as device label.",
                            sensor_name,
                        )
                    start_logger(
                        device_name=display_name,
                        ip=ip,
                        interval=interval,
                        device_id=sensor_name,
                        csv_path=csv_path_for_device(sensor_name),
                    )
                except Exception as e:
                    logger.warning(
                        "Cannot start smartmeter logger for %s (%s): %s",
                        sensor_name, ip, e
                    )

        win.destroy()

    tk.Button(btns, text="Save", command=_save_and_close).pack(side="right")
    tk.Button(btns, text="Close", command=win.destroy).pack(side="right", padx=8)

# ---------- DHT / PIR / Switch / Weight (GPIO) ----------

GPIO_BINDABLE_TYPES = ("Temperature", "PIR", "Switch", "Weight")


def _binding_kind_for_type(sensor_type: str) -> str:
    return {
        "Temperature": "dht",
        "PIR": "pir",
        "Switch": "switch",
        "Weight": "weight",
    }.get(sensor_type, "sensor")


def _supports_gpio_line_options(sensor_type: str) -> bool:
    return sensor_type in ("PIR", "Switch", "Weight")


def open_bind_gpio_sensors_ui(root_win: tk.Tk, sensor_states: dict):
    """
    Associate DHT, PIR, Switch, and Weight sensors to local GPIO pins (BCM numbering).
    Temperature sensors are persisted as {"by":"dht","kind":"dht","gpio":4}.
    Other GPIO sensors are persisted as {"by":"gpio","kind":"pir","gpio":17}.
    """
    names = [
        n for n in _all_sensor_names(sensor_states)
        if (_sensor_type(n, sensor_states) in GPIO_BINDABLE_TYPES)
    ]
    current = _load_sensor_map_json()

    win = tk.Toplevel(root_win)
    win.title("Bind GPIO sensors")
    win.geometry("760x520")

    container = tk.Frame(win)
    container.pack(fill="both", expand=True, padx=10, pady=10)
    canv = tk.Canvas(container)
    canv.pack(side="left", fill="both", expand=True)
    vsb = tk.Scrollbar(container, orient="vertical", command=canv.yview)
    vsb.pack(side="right", fill="y")
    canv.configure(yscrollcommand=vsb.set)
    inner = tk.Frame(canv)
    canv.create_window((0, 0), window=inner, anchor="nw")

    column_specs = [
        (0, 260, "Sensor"),
        (1, 110, "Type"),
        (2, 140, "GPIO (BCM)"),
        (3, 140, "Interval (s)"),
        (4, 90, "Pull-up"),
        (5, 110, "Active low"),
    ]
    for col, minsize, title in column_specs:
        inner.grid_columnconfigure(col, minsize=minsize)
        tk.Label(inner, text=title, anchor="w", font=("Helvetica", 10, "bold")).grid(
            row=0,
            column=col,
            sticky="ew",
            padx=(0, 12) if col < 5 else 0,
            pady=(0, 8),
        )

    rows = []
    for row_idx, name in enumerate(names, start=1):
        sensor_type = _sensor_type(name, sensor_states) or ""
        kind = _binding_kind_for_type(sensor_type)
        supports_line_options = _supports_gpio_line_options(sensor_type)
        tk.Label(inner, text=name, anchor="w").grid(row=row_idx, column=0, sticky="ew", padx=(0, 12), pady=4)
        tk.Label(inner, text=sensor_type, anchor="w").grid(row=row_idx, column=1, sticky="ew", padx=(0, 12), pady=4)

        init_gpio = ""
        init_interval = "10"
        init_pull_up = False
        init_active_low = False
        cfg = current.get(name)
        if isinstance(cfg, dict) and cfg.get("by") in ("dht", "gpio"):
            init_gpio = str(cfg.get("gpio") or "")
            init_interval = str(cfg.get("interval") or "10")
            if supports_line_options:
                init_pull_up = bool(cfg.get("pull_up", False))
                init_active_low = bool(cfg.get("active_low", False))

        gpio_var = tk.StringVar(value=init_gpio)
        interval_var = tk.StringVar(value=init_interval)
        pull_up_var = tk.BooleanVar(value=init_pull_up)
        active_low_var = tk.BooleanVar(value=init_active_low)

        tk.Entry(inner, textvariable=gpio_var, width=12).grid(row=row_idx, column=2, sticky="ew", padx=(0, 12), pady=4)
        tk.Entry(inner, textvariable=interval_var, width=12).grid(row=row_idx, column=3, sticky="ew", padx=(0, 12), pady=4)
        line_state = "normal" if supports_line_options else "disabled"
        tk.Checkbutton(inner, variable=pull_up_var, state=line_state).grid(row=row_idx, column=4, sticky="w", padx=(8, 12), pady=4)
        tk.Checkbutton(inner, variable=active_low_var, state=line_state).grid(row=row_idx, column=5, sticky="w", padx=(8, 0), pady=4)
        rows.append((name, sensor_type, kind, supports_line_options, gpio_var, interval_var, pull_up_var, active_low_var))

    if not names:
        tk.Label(
            inner,
            text="No Temperature, PIR, Switch, or Weight sensors found in the current scenario.",
            anchor="w",
        ).grid(row=1, column=0, columnspan=6, sticky="ew", pady=12)

    inner.bind("<Configure>", lambda e: canv.configure(scrollregion=canv.bbox("all")))

    bottom = tk.Frame(win)
    bottom.pack(fill="x", padx=10, pady=(6, 10))
    autostart = tk.BooleanVar(value=True)
    tk.Checkbutton(bottom, text="Auto-start GPIO loggers", variable=autostart).pack(side="left")

    def _save():
        m = _load_sensor_map_json()
        started = []
        for name, sensor_type, kind, supports_line_options, gpio_var, interval_var, pull_up_var, active_low_var in rows:
            txt = gpio_var.get().strip()
            if not txt:
                if name in m and (m.get(name) or {}).get("by") in ("dht", "gpio"):
                    del m[name]
                continue
            try:
                gpio = int(txt)
            except Exception:
                messagebox.showerror("Invalid GPIO", f"{name}: '{txt}' is not a number.")
                return
            try:
                interval = int(interval_var.get().strip() or "10")
            except Exception:
                messagebox.showerror("Invalid interval", f"{name}: interval must be an integer number of seconds.")
                return
            if interval < 1:
                messagebox.showerror("Invalid interval", f"{name}: interval must be >= 1.")
                return

            pull_up = bool(pull_up_var.get()) if supports_line_options else False
            active_low = bool(active_low_var.get()) if supports_line_options else False
            by = "dht" if kind == "dht" else "gpio"
            m[name] = {
                "by": by,
                "kind": kind,
                "type": sensor_type,
                "gpio": gpio,
                "interval": interval,
                "pull_up": pull_up,
                "active_low": active_low,
            }
            if autostart.get():
                try:
                    from app.hardware.real_sensors import start_bound_logger

                    start_bound_logger(
                        sensor_label=name,
                        kind=kind,
                        gpio=gpio,
                        interval=interval,
                        pull_up=pull_up,
                        active_low=active_low,
                    )
                    started.append((name, gpio))
                except Exception as e:
                    logger.warning("Cannot start %s logger on GPIO %s: %s", kind, gpio, e)

        _save_sensor_map_json(m)
        if started:
            logger.info("[GPIO] loggers started: %s", started)
        win.destroy()

    tk.Button(bottom, text="Save", command=_save).pack(side="right")
    tk.Button(bottom, text="Close", command=win.destroy).pack(side="right", padx=8)
