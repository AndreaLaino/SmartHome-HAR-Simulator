#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path


OUTPUT_HEADER = [
    "timestamp_iso",
    "device",
    "device_id",
    "ip",
    "power_W",
    "voltage_V",
    "current_A",
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def sanitize_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in value.strip())
    return cleaned or "device"


def resolve_input_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_file():
        return path

    root = project_root()
    candidates = [
        Path.cwd() / path,
        root / path,
        root / "scripts" / path,
        root / "devices" / path,
        root / "devices" / "raw" / path,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    checked = "\n".join(f"  - {candidate}" for candidate in candidates)
    raise FileNotFoundError(
        f"File sorgente non trovato: {value}\n"
        "Percorsi controllati:\n"
        f"{checked}\n"
        "Suggerimento: trascina il file nel terminale oppure indica il percorso completo."
    )


def resolve_output_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute() or path.parent != Path("."):
        return path
    if path.name.startswith("smartmeter_"):
        return project_root() / "devices" / path.name
    return path


def parse_source(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8-sig").splitlines()
    lines = [line.strip() for line in text if line.strip()]
    if not lines:
        return []

    header_line = lines[0]
    if "\t" in header_line:
        delimiter = "\t"
    elif ";" in header_line:
        delimiter = ";"
    elif "," in header_line:
        delimiter = ","
    else:
        delimiter = None

    rows: list[dict[str, str]] = []

    if delimiter:
        reader = csv.DictReader(lines, delimiter=delimiter)
        for row in reader:
            normalized = {str(k).strip(): (v or "").strip() for k, v in row.items() if k}
            rows.append(normalized)
        return rows

    # Fallback for space-separated files with a timestamp containing a space:
    # item_id 2017-06-06 15:29:23.448459 0.0
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        rows.append(
            {
                "item_id": parts[0],
                "time": f"{parts[1]} {parts[2]}",
                "value": parts[-1],
            }
        )
    return rows


def to_float(value: str) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def sensible_voltage() -> float:
    return max(218.0, min(242.0, random.gauss(230.0, 3.0)))


def sensible_current(power_w: float, voltage_v: float) -> float:
    if power_w <= 0:
        return 0.0
    noise = random.uniform(0.97, 1.03)
    return max(0.0, (power_w / voltage_v) * noise)


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        if default is not None:
            return default
        raise
    if value:
        return value
    if default is not None:
        return default
    return ask(prompt, default)


def convert(
    input_path: Path,
    output_path: Path,
    device: str,
    device_id: str,
    ip: str,
    item_id_filter: str | None = None,
) -> int:
    source_rows = parse_source(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_HEADER)

        for row in source_rows:
            item_id = row.get("item_id", "")
            if item_id_filter and item_id != item_id_filter:
                continue

            timestamp = row.get("time") or row.get("timestamp") or row.get("timestamp_iso")
            power = to_float(row.get("value") or row.get("power") or row.get("power_W"))
            if not timestamp or power is None:
                continue

            power = max(0.0, power)
            voltage = sensible_voltage()
            current = sensible_current(power, voltage)

            writer.writerow(
                [
                    timestamp,
                    device,
                    device_id,
                    ip,
                    f"{power:.3f}",
                    f"{voltage:.1f}",
                    f"{current:.3f}",
                ]
            )
            written += 1

    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Converte un file item_id/time/value in un CSV smartmeter compatibile col simulatore."
    )
    parser.add_argument("input", nargs="?", help="File sorgente con colonne item_id, time, value.")
    parser.add_argument("-o", "--output", help="File CSV di destinazione.")
    parser.add_argument("--device", help="Nome dispositivo, ad esempio dishwasher.")
    parser.add_argument("--device-id", help="ID dispositivo, ad esempio sm_dw.")
    parser.add_argument("--ip", help="Indirizzo IP associato allo smart meter.")
    parser.add_argument("--item-id", help="Filtra solo un item_id del file sorgente.")
    parser.add_argument("--seed", type=int, help="Seed opzionale per rendere ripetibili tensione e corrente.")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    input_path = resolve_input_path(args.input or ask("File sorgente"))
    device = args.device or ask("Dispositivo", input_path.stem)
    device_id = args.device_id or ask("ID dispositivo", f"sm_{sanitize_filename(device)}")
    ip = args.ip or ask("IP smart meter", "0.0.0.0")
    if args.item_id is not None:
        item_id_filter = args.item_id
    elif sys.stdin.isatty():
        item_id_filter = ask("Item ID da filtrare (invio = tutti)", "")
    else:
        item_id_filter = ""
    item_id_filter = item_id_filter or None

    default_output = Path("devices") / f"smartmeter_{sanitize_filename(device_id)}.csv"
    output_path = resolve_output_path(args.output or ask("File di output", str(default_output)))

    written = convert(input_path, output_path, device, device_id, ip, item_id_filter)
    print(f"Creato {output_path} con {written} record.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
