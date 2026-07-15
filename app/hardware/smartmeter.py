# smartmeter.py
from __future__ import annotations

import os, csv, time, threading, logging, requests, glob
from typing import Optional, Dict, List, Tuple
from datetime import datetime
import pandas as pd
from app.save_paths import ensure_devices_dir

logger = logging.getLogger("smartmeter")
logger.setLevel(logging.INFO)

DEFAULT_INTERVAL = 10  # seconds
DEVICES_DIR = str(ensure_devices_dir())
LOGGERS: Dict[str, "SmartMeterLogger"] = {}  # logger_key (device_id or device_name) -> logger
CSV_WRITE_LOCKS: Dict[str, threading.Lock] = {}  # filepath -> lock (one lock per file)

# Rules to derive a canonical ID (case-insensitive) from the device name
DEFAULT_ID_RULES: List[Tuple[str, str]] = [
    ("pc", "PC"),
    ("laptop", "PC"),
    ("notebook", "PC"),
    ("wash", "WASHER"),
    ("lavatrice", "WASHER"),
    ("dryer", "DRYER"),
    ("forno", "OVEN"),
    ("oven", "OVEN"),
]

def _canon_id(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())

def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in (name or "").strip())

def csv_path_for_device(device_name: str) -> str:
    safe = _sanitize(device_name or "device")
    return os.path.join(DEVICES_DIR, f"smartmeter_{safe}.csv")

def csv_ensure_header(path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not os.path.isfile(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(
                ["timestamp_iso", "device", "device_id", "ip", "power_W", "voltage_V", "current_A"]
            )
    else:
        _ensure_trailing_newline(path)


def _ensure_trailing_newline(path: str):
    try:
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return
        with open(path, "rb+") as f:
            f.seek(-1, os.SEEK_END)
            last = f.read(1)
            if last not in (b"\n", b"\r"):
                f.write(b"\n")
    except OSError as exc:
        logger.warning("Unable to normalize CSV newline for %s: %s", path, exc)

def get_device_name_from_shelly(ip: str, timeout=2, auth: Optional[tuple]=None) -> str:
    try:
        url = f"http://{ip}/rpc/Shelly.GetDeviceInfo"
        r = requests.get(url, timeout=timeout, auth=auth)
        data = r.json()
        name = (data.get("name") or data.get("id") or "").strip()
        if name:
            return name
    except Exception:
        pass
    return f"Shelly_{ip.replace('.', '-')}"

def derive_device_id(device_name: str, rules: Optional[List[Tuple[str, str]]] = None, default: str = "UNKNOWN") -> str:
    rules = rules or DEFAULT_ID_RULES
    low = (device_name or "").lower()
    for sub, dev_id in rules:
        if sub.lower() in low:
            return dev_id
    return default

def _get_voltage_gen2(shelly_ip: str, timeout=2, auth: Optional[tuple]=None):
    url = f"http://{shelly_ip}/rpc/Switch.GetStatus?id=0"
    r = requests.get(url, timeout=timeout, auth=auth)
    d = r.json()
    return d.get("voltage"), d.get("apower") or d.get("power"), d.get("current")

def _get_voltage_gen1(shelly_ip: str, timeout=2, auth: Optional[tuple]=None):
    url = f"http://{shelly_ip}/status"
    r = requests.get(url, timeout=timeout, auth=auth)
    data = r.json()
    v = p = a = None
    meters = data.get("meters") or data.get("emeter") or []
    if meters and isinstance(meters, list):
        m0 = meters[0]
        v = m0.get("voltage")
        p = m0.get("power") or m0.get("apower")
        a = m0.get("current")
    v = v or data.get("voltage")
    p = p or data.get("power")
    a = a or data.get("current")
    return v, p, a

#logger for a single device
class SmartMeterLogger:
    def __init__(
        self,
        device_name: str,
        shelly_ip: str,
        csv_path: Optional[str] = None,
        interval: int = DEFAULT_INTERVAL,
        auth: Optional[tuple] = None,          
        device_id: Optional[str] = None,       
        id_rules: Optional[List[Tuple[str,str]]] = None
    ):
        self.auth = auth
        self.shelly_ip = shelly_ip
        self.device_name = device_name or get_device_name_from_shelly(shelly_ip, auth=auth)
        self.csv_path = csv_path or csv_path_for_device(self.device_name)
        self.interval = interval
        self.device_id = device_id or derive_device_id(self.device_name, rules=id_rules)
        self._thread = None
        self._stop_event = threading.Event()
        csv_ensure_header(self.csv_path)

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.info(f"Logger '{self.device_name}' already in execution")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name=f"SMLogger-{self.device_name}")
        self._thread.start()
        logger.info(f"SmartMeterLogger '{self.device_name}' started (id={self.device_id})")

    def stop(self):
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join(timeout=2)
        logger.info(f"SmartMeterLogger '{self.device_name}' stopped")

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                v = p = a = None
                try:
                    v, p, a = _get_voltage_gen2(self.shelly_ip, auth=self.auth)
                except Exception:
                    try:
                        v, p, a = _get_voltage_gen1(self.shelly_ip, auth=self.auth)
                    except Exception:
                        pass

                def _tof(x):
                    try: return float(x)
                    except Exception: return None
                v, p, a = _tof(v), _tof(p), _tof(a)

                if v is None and p is not None and a is not None and a > 1e-3:
                    v = p / a

                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]  # with milliseconds

                # Per-file lock (not global)
                if self.csv_path not in CSV_WRITE_LOCKS:
                    CSV_WRITE_LOCKS[self.csv_path] = threading.Lock()
                
                with CSV_WRITE_LOCKS[self.csv_path]:
                    _ensure_trailing_newline(self.csv_path)
                    with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow([ts, self.device_name, self.device_id, self.shelly_ip,
                                                "" if p is None else p,
                                                "" if v is None else v,
                                                "" if a is None else a])
            except Exception as e:
                logger.warning(f"[{self.device_name}] polling error: {e}")

            for _ in range(int(max(1, self.interval))):
                if self._stop_event.is_set(): break
                time.sleep(1)

# multi-device  API 

def start_logger(
    device_name: Optional[str],
    ip: str,
    interval: int = DEFAULT_INTERVAL,
    auth: Optional[tuple] = None,
    csv_path: Optional[str] = None,
    device_id: Optional[str] = None,
    id_rules: Optional[List[Tuple[str,str]]] = None,
) -> SmartMeterLogger:
   
    device_name = device_name or get_device_name_from_shelly(ip, auth=auth)
    logger_key = (device_id or device_name or ip).strip()
    logger_obj = LOGGERS.get(logger_key)
    if logger_obj is None:
        logger_obj = SmartMeterLogger(
            device_name=device_name,
            shelly_ip=ip,
            csv_path=csv_path,
            interval=interval,
            auth=auth,
            device_id=device_id,
            id_rules=id_rules
        )
        LOGGERS[logger_key] = logger_obj
    else:
        # Keep existing logger instance but refresh runtime settings.
        logger_obj.device_name = device_name or logger_obj.device_name
        logger_obj.shelly_ip = ip or logger_obj.shelly_ip
        logger_obj.interval = interval or logger_obj.interval
        if auth is not None:
            logger_obj.auth = auth
        if device_id is not None:
            logger_obj.device_id = device_id
        if csv_path and csv_path != logger_obj.csv_path:
            logger_obj.csv_path = csv_path
            csv_ensure_header(logger_obj.csv_path)
    logger_obj.start()
    return logger_obj

def stop_logger(logger_key: str):
    logger_obj = LOGGERS.pop(logger_key, None)
    if logger_obj:
        logger_obj.stop()

def stop_all():
    for name in list(LOGGERS.keys()):
        stop_logger(name)



# ---data loading functions---

def load_csv(csv_path: str, device: Optional[str] = None) -> dict:
    out = {}
    if not os.path.isfile(csv_path):
        return out
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if device and (r.get("device") != device):
                continue
            ts = (r.get("timestamp_iso") or "").strip()
            if not ts:
                continue
            def fnum(x):
                try: return float(x) if x not in (None, "") else None
                except: return None
            out[ts] = {
                "device": r.get("device"),
                "device_id": r.get("device_id"),
                "ip": r.get("ip"),
                "power": fnum(r.get("power_W")),
                "voltage": fnum(r.get("voltage_V")),
                "current": fnum(r.get("current_A")),
            }
    return out

def load_power_df(csv_path: str, device: Optional[str] = None, rule: str = "1min", agg: str = "median") -> pd.DataFrame:
    raw = load_csv(csv_path, device=device)
    rows = []
    for ts, d in raw.items():
        p = d.get("power")
        if p is not None and p > 0:
            rows.append({"timestamp": ts, "value": p})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="%Y-%m-%d %H:%M:%S.%f", errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    # Use max to keep highest power value per minute
    result = df.resample(rule).max()
    return result.fillna(0)

def load_power_by_device_id_any_csv(device_id_wanted: str, logs_dir=DEVICES_DIR) -> pd.DataFrame:
    want = _canon_id(device_id_wanted)
    rows = []
    for path in glob.glob(os.path.join(logs_dir, "smartmeter_*.csv")):
        with open(path, "r", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                if _canon_id(row.get("device_id")) != want:
                    continue
                ts = row.get("timestamp_iso"); p = row.get("power_W")
                try:
                    p = float(p) if p and p.strip() else 0.0
                except Exception:
                    p = 0.0
                rows.append({"timestamp": ts, "value": p})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    return df.resample("1min").max().fillna(0)

def load_power_by_ip_any_csv(ip_wanted: str, logs_dir=DEVICES_DIR) -> pd.DataFrame:
    rows = []
    for path in glob.glob(os.path.join(logs_dir, "smartmeter_*.csv")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                r = csv.DictReader(f)
                for row in r:
                    if (row.get("ip") or "").strip() != ip_wanted:
                        continue
                    ts = row.get("timestamp_iso")
                    p = row.get("power_W")
                    try:
                        p = float(p) if p and p.strip() else 0.0
                    except Exception:
                        p = 0.0
                    rows.append({"timestamp": ts, "value": p})
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    return df.resample("1min").max().fillna(0)
