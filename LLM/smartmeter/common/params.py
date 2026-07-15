from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "default_parameters.json"


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_update(out[key], value)
        else:
            out[key] = value
    return out


def load_default_config() -> dict[str, Any]:
    with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_appliance_config(appliance_key: str, custom_path: str | None = None) -> dict[str, Any]:
    full_default = load_default_config()
    default_cfg = full_default.get(appliance_key)
    if default_cfg is None:
        raise KeyError(f"No default configuration found for '{appliance_key}'.")

    if not custom_path:
        return deepcopy(default_cfg)

    custom_file = Path(custom_path)
    if not custom_file.exists():
        raise FileNotFoundError(f"Custom parameters file not found: {custom_file}")

    with open(custom_file, "r", encoding="utf-8") as f:
        custom_cfg = json.load(f)

    # Accepts either a full file with the appliance key or just the appliance block.
    appliance_custom = custom_cfg.get(appliance_key, custom_cfg)
    if not isinstance(appliance_custom, dict):
        raise ValueError("The custom file content must be a JSON object.")

    return _deep_update(default_cfg, appliance_custom)


def choose_config(appliance_key: str, custom_path_arg: str | None = None) -> dict[str, Any]:
    if custom_path_arg:
        return load_appliance_config(appliance_key, custom_path_arg)

    print("\nSelect parameter configuration:")
    print("1) Default parameters")
    print("2) Load parameters from JSON file")

    while True:
        choice = input("Enter 1 or 2: ").strip()
        if choice == "1":
            return load_appliance_config(appliance_key)
        if choice == "2":
            user_path = input("Custom JSON file path: ").strip().strip('"')
            return load_appliance_config(appliance_key, user_path)
        print("Invalid value. Enter only 1 or 2.")


def ask_for_exact_k(default_k: int | None = None) -> int:
    prompt = "Desired number of clusters (k)"
    if default_k is not None:
        prompt += f" [default {default_k}]"
    prompt += ": "

    while True:
        raw = input(prompt).strip()
        if not raw and default_k is not None:
            return int(default_k)
        try:
            k = int(raw)
        except ValueError:
            print("Enter a valid integer.")
            continue
        if k < 2:
            print("k must be >= 2.")
            continue
        return k
