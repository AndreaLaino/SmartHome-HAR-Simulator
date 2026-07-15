from __future__ import annotations

import csv
import glob
import logging
import os
import threading
import time
from datetime import datetime
from typing import Optional, Tuple

import pandas as pd

from app.save_paths import ensure_devices_dir

logger = logging.getLogger("hardware.real_sensors")
logger.setLevel(logging.INFO)

DEVICES_DIR = str(ensure_devices_dir())
DHT_DEFAULT_INTERVAL = 10
GPIO_DEFAULT_INTERVAL = 10
DHT_LOGGERS: dict[str, "DHTLogger"] = {}
GPIO_LOGGERS: dict[str, "GPIOValueLogger"] = {}

try:
    import board
    import adafruit_dht

    _USE_CPY_DHT = True
except Exception:
    board = None
    adafruit_dht = None
    _USE_CPY_DHT = False

try:
    import Adafruit_DHT as ADA_DHT

    _USE_ADA_DHT = True
except Exception:
    ADA_DHT = None
    _USE_ADA_DHT = False

try:
    from gpiozero import DigitalInputDevice

    _USE_GPIOZERO = True
except Exception:
    DigitalInputDevice = None
    _USE_GPIOZERO = False

try:
    import RPi.GPIO as RPI_GPIO

    _USE_RPI_GPIO = True
except Exception:
    RPI_GPIO = None
    _USE_RPI_GPIO = False


def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in (name or "").strip())


def _kind_prefix(kind: str) -> str:
    normalized = (kind or "sensor").strip().lower().replace(" ", "_")
    if normalized in {"dht", "pir", "switch", "weight"}:
        return normalized
    return "sensor"


def _df_from_rows(rows: list[dict], rule: str = "1min", agg: str = "median") -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    if agg == "max":
        return df.resample(rule).max()
    return df.resample(rule).median()


def _board_pin_from_bcm(gpio: int):
    if not _USE_CPY_DHT or board is None:
        return None
    return {
        4: getattr(board, "D4", None),
        17: getattr(board, "D17", None),
        27: getattr(board, "D27", None),
        22: getattr(board, "D22", None),
        5: getattr(board, "D5", None),
        6: getattr(board, "D6", None),
        13: getattr(board, "D13", None),
        19: getattr(board, "D19", None),
        26: getattr(board, "D26", None),
    }.get(int(gpio), None)


def dht_csv_path_for_label(label: str) -> str:
    safe = _sanitize(label or "sensor")
    return os.path.join(DEVICES_DIR, f"dht_{safe}.csv")


def dht_csv_ensure_header(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not os.path.isfile(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp_iso", "label", "gpio", "temp_C", "hum_%"])


class DHTLogger:
    def __init__(self, sensor_label: str, gpio_bcm: int, interval: int = DHT_DEFAULT_INTERVAL):
        self.label = str(sensor_label)
        self.gpio = int(gpio_bcm)
        self.interval = max(1, int(interval))
        self.csv_path = dht_csv_path_for_label(self.label)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._dht_device = None
        dht_csv_ensure_header(self.csv_path)

        if _USE_CPY_DHT and adafruit_dht is not None:
            pin = _board_pin_from_bcm(self.gpio)
            if pin is None:
                logger.warning("Cannot map GPIO %s to a board pin; will use fallback if available.", self.gpio)
            else:
                try:
                    self._dht_device = adafruit_dht.DHT22(pin, use_pulseio=False)
                except Exception as exc:
                    logger.warning("Init adafruit_dht failed (%s); will use fallback if available.", exc)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.info("DHT logger '%s' already active", self.label)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"DHT-{self.label}")
        self._thread.start()
        logger.info("DHT logger '%s' started (GPIO BCM=%s)", self.label, self.gpio)

    def stop(self) -> None:
        if not self._thread:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        try:
            if self._dht_device is not None and hasattr(self._dht_device, "exit"):
                self._dht_device.exit()
        except Exception:
            pass
        logger.info("DHT logger '%s' stopped", self.label)

    def _read_once(self) -> Tuple[Optional[float], Optional[float]]:
        if _USE_CPY_DHT and self._dht_device is not None:
            try:
                temperature = self._dht_device.temperature
                humidity = self._dht_device.humidity
                return (
                    float(temperature) if temperature is not None else None,
                    float(humidity) if humidity is not None else None,
                )
            except RuntimeError:
                return None, None
            except Exception as exc:
                logger.warning("[DHT '%s'] CircuitPython error: %s", self.label, exc)
                return None, None

        if _USE_ADA_DHT and ADA_DHT is not None:
            try:
                humidity, temperature = ADA_DHT.read_retry(ADA_DHT.DHT22, self.gpio)
                return (
                    float(temperature) if temperature is not None else None,
                    float(humidity) if humidity is not None else None,
                )
            except Exception as exc:
                logger.warning("[DHT '%s'] Adafruit_DHT error: %s", self.label, exc)
                return None, None

        logger.warning("[DHT '%s'] no DHT library available in this host", self.label)
        return None, None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                temperature, humidity = self._read_once()
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(
                        [
                            ts,
                            self.label,
                            self.gpio,
                            "" if temperature is None else temperature,
                            "" if humidity is None else humidity,
                        ]
                    )
            except Exception as exc:
                logger.warning("[DHT '%s'] loop error: %s", self.label, exc)
            for _ in range(self.interval):
                if self._stop.is_set():
                    break
                time.sleep(1)


def start_dht_logger(sensor_label: str, gpio: int, interval: int = DHT_DEFAULT_INTERVAL) -> DHTLogger:
    label = str(sensor_label)
    logger_obj = DHT_LOGGERS.get(label)
    requested_gpio = int(gpio)
    requested_interval = max(1, int(interval))
    if logger_obj is None:
        logger_obj = DHTLogger(sensor_label=label, gpio_bcm=requested_gpio, interval=requested_interval)
        DHT_LOGGERS[label] = logger_obj
    else:
        needs_recreate = logger_obj.gpio != requested_gpio
        if needs_recreate:
            logger_obj.stop()
            logger_obj = DHTLogger(sensor_label=label, gpio_bcm=requested_gpio, interval=requested_interval)
            DHT_LOGGERS[label] = logger_obj
        else:
            logger_obj.interval = requested_interval
    logger_obj.start()
    return logger_obj


def stop_dht_logger(sensor_label: str) -> None:
    logger_obj = DHT_LOGGERS.pop(str(sensor_label), None)
    if logger_obj:
        logger_obj.stop()


def load_temp_by_label_any_csv(label: str, logs_dir=DEVICES_DIR) -> pd.DataFrame:
    path = os.path.join(logs_dir, f"dht_{_sanitize(label)}.csv")
    rows = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    rows.append({"timestamp": row.get("timestamp_iso"), "value": float(row.get("temp_C"))})
                except Exception:
                    continue
    return _df_from_rows(rows)


def load_temp_by_gpio_any_csv(gpio: int, logs_dir=DEVICES_DIR) -> pd.DataFrame:
    rows = []
    for path in glob.glob(os.path.join(logs_dir, "dht_*.csv")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        if int(row.get("gpio")) != int(gpio):
                            continue
                        rows.append({"timestamp": row.get("timestamp_iso"), "value": float(row.get("temp_C"))})
                    except Exception:
                        continue
        except Exception:
            continue
    return _df_from_rows(rows)


def gpio_csv_path_for_sensor(sensor_label: str, kind: str) -> str:
    safe_kind = _kind_prefix(kind)
    safe_label = _sanitize(sensor_label or "sensor")
    return os.path.join(DEVICES_DIR, f"{safe_kind}_{safe_label}.csv")


def gpio_csv_ensure_header(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if not os.path.isfile(path):
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp_iso", "label", "kind", "gpio", "value"])


class GPIOValueLogger:
    def __init__(
        self,
        sensor_label: str,
        kind: str,
        gpio_bcm: int,
        interval: int = GPIO_DEFAULT_INTERVAL,
        pull_up: bool = False,
        active_low: bool = False,
        csv_path: Optional[str] = None,
    ):
        self.label = str(sensor_label)
        self.kind = _kind_prefix(kind)
        self.gpio = int(gpio_bcm)
        self.interval = max(1, int(interval))
        self.pull_up = bool(pull_up)
        self.active_low = bool(active_low)
        self.csv_path = csv_path or gpio_csv_path_for_sensor(self.label, self.kind)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._device = None
        gpio_csv_ensure_header(self.csv_path)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.info("%s logger '%s' already active", self.kind, self.label)
            return
        self._init_gpio()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"{self.kind.upper()}-{self.label}")
        self._thread.start()
        logger.info("%s logger '%s' started (GPIO BCM=%s)", self.kind, self.label, self.gpio)

    def stop(self) -> None:
        if not self._thread:
            return
        self._stop.set()
        self._thread.join(timeout=2)
        try:
            if self._device is not None and hasattr(self._device, "close"):
                self._device.close()
        except Exception:
            pass
        logger.info("%s logger '%s' stopped", self.kind, self.label)

    def _init_gpio(self) -> None:
        if _USE_GPIOZERO and DigitalInputDevice is not None:
            try:
                self._device = DigitalInputDevice(self.gpio, pull_up=self.pull_up, active_state=None)
                return
            except Exception as exc:
                logger.warning("gpiozero init failed for %s on GPIO %s: %s", self.label, self.gpio, exc)

        if _USE_RPI_GPIO and RPI_GPIO is not None:
            try:
                RPI_GPIO.setmode(RPI_GPIO.BCM)
                pull = RPI_GPIO.PUD_UP if self.pull_up else RPI_GPIO.PUD_DOWN
                RPI_GPIO.setup(self.gpio, RPI_GPIO.IN, pull_up_down=pull)
                return
            except Exception as exc:
                logger.warning("RPi.GPIO init failed for %s on GPIO %s: %s", self.label, self.gpio, exc)

        logger.warning("[%s '%s'] no GPIO library available in this host", self.kind, self.label)

    def _read_once(self) -> Optional[float]:
        if self._device is not None:
            try:
                raw_value = int(bool(self._device.value))
            except Exception as exc:
                logger.warning("[%s '%s'] gpiozero read failed: %s", self.kind, self.label, exc)
                return None
        elif _USE_RPI_GPIO and RPI_GPIO is not None:
            try:
                raw_value = int(bool(RPI_GPIO.input(self.gpio)))
            except Exception as exc:
                logger.warning("[%s '%s'] RPi.GPIO read failed: %s", self.kind, self.label, exc)
                return None
        else:
            return None

        if self.active_low:
            raw_value = 0 if raw_value else 1
        return float(raw_value)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                value = self._read_once()
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with open(self.csv_path, "a", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow([ts, self.label, self.kind, self.gpio, "" if value is None else value])
            except Exception as exc:
                logger.warning("[%s '%s'] loop error: %s", self.kind, self.label, exc)
            for _ in range(self.interval):
                if self._stop.is_set():
                    break
                time.sleep(1)


def start_gpio_logger(
    sensor_label: str,
    kind: str,
    gpio: int,
    interval: int = GPIO_DEFAULT_INTERVAL,
    pull_up: bool = False,
    active_low: bool = False,
) -> GPIOValueLogger:
    key = f"{_kind_prefix(kind)}:{sensor_label}"
    logger_obj = GPIO_LOGGERS.get(key)
    requested_gpio = int(gpio)
    requested_interval = max(1, int(interval))
    requested_pull_up = bool(pull_up)
    requested_active_low = bool(active_low)
    if logger_obj is None:
        logger_obj = GPIOValueLogger(
            sensor_label=sensor_label,
            kind=kind,
            gpio_bcm=requested_gpio,
            interval=requested_interval,
            pull_up=requested_pull_up,
            active_low=requested_active_low,
        )
        GPIO_LOGGERS[key] = logger_obj
    else:
        needs_recreate = (
            logger_obj.gpio != requested_gpio
            or logger_obj.pull_up != requested_pull_up
        )
        if needs_recreate:
            logger_obj.stop()
            logger_obj = GPIOValueLogger(
                sensor_label=sensor_label,
                kind=kind,
                gpio_bcm=requested_gpio,
                interval=requested_interval,
                pull_up=requested_pull_up,
                active_low=requested_active_low,
            )
            GPIO_LOGGERS[key] = logger_obj
        else:
            logger_obj.interval = requested_interval
            logger_obj.active_low = requested_active_low
    logger_obj.start()
    return logger_obj


def stop_gpio_logger(sensor_label: str, kind: str) -> None:
    key = f"{_kind_prefix(kind)}:{sensor_label}"
    logger_obj = GPIO_LOGGERS.pop(key, None)
    if logger_obj:
        logger_obj.stop()


def load_value_by_label_any_csv(label: str, kind: str, logs_dir=DEVICES_DIR) -> pd.DataFrame:
    path = os.path.join(logs_dir, f"{_kind_prefix(kind)}_{_sanitize(label)}.csv")
    rows = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    rows.append({"timestamp": row.get("timestamp_iso"), "value": float(row.get("value"))})
                except Exception:
                    continue
    return _df_from_rows(rows)


def load_value_by_gpio_any_csv(gpio: int, kind: str, logs_dir=DEVICES_DIR) -> pd.DataFrame:
    rows = []
    pattern = os.path.join(logs_dir, f"{_kind_prefix(kind)}_*.csv")
    for path in glob.glob(pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        if int(row.get("gpio")) != int(gpio):
                            continue
                        rows.append({"timestamp": row.get("timestamp_iso"), "value": float(row.get("value"))})
                    except Exception:
                        continue
        except Exception:
            continue
    return _df_from_rows(rows)


def start_bound_logger(
    sensor_label: str,
    kind: str,
    gpio: int,
    interval: int,
    pull_up: bool = False,
    active_low: bool = False,
):
    if _kind_prefix(kind) == "dht":
        return start_dht_logger(sensor_label=sensor_label, gpio=gpio, interval=interval)
    return start_gpio_logger(
        sensor_label=sensor_label,
        kind=kind,
        gpio=gpio,
        interval=interval,
        pull_up=pull_up,
        active_low=active_low,
    )


def stop_all() -> None:
    for key in list(GPIO_LOGGERS.keys()):
        logger_obj = GPIO_LOGGERS.pop(key, None)
        if logger_obj:
            logger_obj.stop()
    for key in list(DHT_LOGGERS.keys()):
        logger_obj = DHT_LOGGERS.pop(key, None)
        if logger_obj:
            logger_obj.stop()


# Compatibility aliases used by the old root-level dhtlogger module.
DEFAULT_INTERVAL = DHT_DEFAULT_INTERVAL
LOGGERS = DHT_LOGGERS
csv_path_for_label = dht_csv_path_for_label
csv_ensure_header = dht_csv_ensure_header
