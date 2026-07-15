from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from common.cycle_pipeline import run_cycle_pipeline
from common.params import ask_for_exact_k, choose_config


BASE_DIR = Path(__file__).resolve().parent
INPUT_PATH = BASE_DIR / "coffee_machine_data.tsv"
OUTPUT_DIR = BASE_DIR / "coffee_machine_output"
PKL_PATH = OUTPUT_DIR / "coffee_machine_cycles_raw_data.pkl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coffee machine clustering")
    parser.add_argument("--config", type=str, default=None, help="Path to custom parameters JSON file")
    parser.add_argument("--exact-k", type=int, default=None, help="Exact requested number of clusters")
    return parser.parse_args()


def choose_k_mode(args: argparse.Namespace, params: dict) -> int | None:
    if args.exact_k is not None:
        return int(args.exact_k)

    print("\nClustering mode:")
    print("1) Automatic (suggested k)")
    print("2) Fixed exact k (user-selected)")

    while True:
        choice = input("Enter 1 or 2: ").strip()
        if choice == "1":
            return None
        if choice == "2":
            default_k = int(params.get("k", {}).get("human_k_min", 20))
            return ask_for_exact_k(default_k=default_k)
        print("Invalid value. Enter only 1 or 2.")


def main() -> None:
    args = parse_args()
    params = choose_config("coffee_machine", custom_path_arg=args.config)
    exact_k = choose_k_mode(args, params)

    run_cycle_pipeline(
        input_path=INPUT_PATH,
        output_dir=OUTPUT_DIR,
        pkl_path=PKL_PATH,
        chart_title_prefix="coffee machine",
        params=params,
        exact_k=exact_k,
    )


if __name__ == "__main__":
    main()
