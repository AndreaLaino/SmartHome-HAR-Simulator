from __future__ import annotations

from pathlib import Path


def resolve_input_tsv(base_dir: Path, appliance_key: str, output_dir: Path) -> Path:
    candidates = [
        base_dir / f"{appliance_key}_data.tsv",
        output_dir / "llm_runtime_input.tsv",
    ]
    candidates.extend(sorted(base_dir.glob("*_data.tsv")))
    candidates.extend(
        sorted(
            base_dir.glob("*/llm_runtime_input.tsv"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    searched = "\n".join(f"- {candidate}" for candidate in candidates)
    raise FileNotFoundError(
        "No TSV input file found for the LLM Smart Meter script. "
        "Run the GUI pipeline first or add an appliance data TSV.\n"
        f"Searched:\n{searched}"
    )
