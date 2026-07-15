from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import argparse
from pathlib import Path
from typing import Any

import pandas as pd


ENABLE_VALUES = {"1", "true", "yes", "y", "on"}
DEFAULT_BASE_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in ENABLE_VALUES


def _to_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    try:
        if pd.isna(value):
            return None
        return int(float(value))
    except Exception:
        return None


def _round(value: Any, digits: int = 3) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    return round(number, digits)


def _load_selected_case(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    df = pd.read_csv(path)
    if df.empty:
        return None
    row = df.iloc[0]
    return {
        "cycle_id": _to_int(row.get("cycle_id")),
        "cluster": _to_int(row.get("cluster")),
        "start_time": str(row.get("start_time", "")),
        "duration_minutes": _round(row.get("duration_minutes"), 1),
        "max_power_w": _round(row.get("max_power"), 1),
        "mean_power_w": _round(row.get("mean_power"), 1),
        "energy_kwh": _round(row.get("energy_kwh"), 3),
        "distance_to_last_incomplete_day": _round(row.get("distance_to_last_incomplete_day"), 3),
    }


def _load_completion_summary(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return {
        "matched_cycle_id": _to_int(raw.get("matched_cycle_id")),
        "matched_cluster": _to_int(raw.get("matched_cluster")),
        "partial_duration_minutes": _round(raw.get("partial_duration_minutes"), 1),
        "matched_duration_minutes": _round(raw.get("matched_duration_minutes"), 1),
        "completion_added_minutes": _round(raw.get("completion_added_minutes"), 1),
        "matched_start_time": raw.get("matched_start_time"),
        "matched_end_time": raw.get("matched_end_time"),
    }


def _cluster_rows(
    results_dir: Path,
    selected_cluster: int,
    dominant_cluster: int,
    max_clusters: int,
) -> list[dict[str, Any]]:
    summary_path = results_dir / "cluster_summary.csv"
    reps_path = results_dir / "cluster_representatives.csv"
    if not summary_path.exists():
        raise FileNotFoundError(f"Cluster summary file not found: {summary_path}")
    if not reps_path.exists():
        raise FileNotFoundError(f"Cluster representatives file not found: {reps_path}")

    summary = pd.read_csv(summary_path)
    reps = pd.read_csv(reps_path)
    if summary.empty:
        return []

    summary["cluster"] = pd.to_numeric(summary["cluster"], errors="coerce")
    summary["n_cycles"] = pd.to_numeric(summary["n_cycles"], errors="coerce")
    reps["cluster"] = pd.to_numeric(reps["cluster"], errors="coerce")

    selected_ids = {int(selected_cluster), int(dominant_cluster)}
    ordered = summary.sort_values(["n_cycles", "cluster"], ascending=[False, True], kind="mergesort")
    ordered_clusters: list[int] = []
    for cluster_id in selected_ids:
        if cluster_id not in ordered_clusters:
            ordered_clusters.append(cluster_id)
    for cluster_id in ordered["cluster"].dropna().astype(int).tolist():
        if cluster_id not in ordered_clusters:
            ordered_clusters.append(cluster_id)
        if len(ordered_clusters) >= max(1, int(max_clusters)):
            break

    rows: list[dict[str, Any]] = []
    for cluster_id in ordered_clusters[: max(1, int(max_clusters))]:
        cluster_summary = summary[summary["cluster"] == cluster_id]
        if cluster_summary.empty:
            continue
        row = cluster_summary.iloc[0]
        rep_row = reps[reps["cluster"] == cluster_id]
        rep = rep_row.iloc[0] if not rep_row.empty else {}
        rows.append(
            {
                "cluster": int(cluster_id),
                "n_cycles": _to_int(row.get("n_cycles")),
                "representative_cycle_id": _to_int(rep.get("cycle_id")),
                "duration_mean_minutes": _round(row.get("duration_mean"), 1),
                "duration_std_minutes": _round(row.get("duration_std"), 1),
                "max_power_mean_w": _round(row.get("max_power_mean"), 1),
                "mean_power_mean_w": _round(row.get("mean_power_mean"), 1),
                "energy_mean_kwh": _round(row.get("energy_mean_kwh"), 3),
                "peak_time_mean_norm": _round(row.get("peak_time_mean"), 3),
            }
        )
    return rows


def build_interpretation_prompt(
    *,
    appliance_label: str,
    appliance_key: str,
    chosen_k: int,
    selected_cluster: int,
    dominant_cluster: int,
    selected_cycle_id: int,
    cluster_profiles: list[dict[str, Any]],
    selected_case: dict[str, Any] | None,
    completion: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = {
        "appliance_label": appliance_label,
        "appliance_key": appliance_key,
        "chosen_k": int(chosen_k),
        "selected_cluster": int(selected_cluster),
        "dominant_cluster": int(dominant_cluster),
        "selected_cycle_id": int(selected_cycle_id),
        "cluster_profiles": cluster_profiles,
        "selected_case": selected_case,
        "completion": completion,
    }

    system = (
        "Sei un assistente tecnico che interpreta profili di consumo energetico "
        "per un simulatore smart home. Non devi inventare dati, non devi modificare "
        "i valori numerici e devi distinguere chiaramente tra dato osservato e "
        "interpretazione plausibile."
    )
    user = (
        "Analizza il seguente output numerico prodotto da clustering e matching dei "
        "cicli di consumo. Rispondi solo con un oggetto JSON valido in italiano, "
        "seguendo questo schema:\n"
        "{\n"
        '  "cluster_profiles": [\n'
        "    {\n"
        '      "cluster": 0,\n'
        '      "profile_name": "nome breve",\n'
        '      "description": "descrizione sintetica del comportamento energetico",\n'
        '      "possible_activity": "attivita plausibile o non determinabile",\n'
        '      "confidence": "alta|media|bassa",\n'
        '      "reason": "motivo basato solo sui dati numerici",\n'
        '      "simulator_use": "come riusare questo profilo nel simulatore"\n'
        "    }\n"
        "  ],\n"
        '  "selected_case_explanation": "spiegazione del caso selezionato",\n'
        '  "completion_explanation": "spiegazione del completamento, oppure null",\n'
        '  "limitations": ["limite 1", "limite 2"]\n'
        "}\n\n"
        "Dati numerici:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return {
        "payload": payload,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


def extract_json_object(text: str) -> dict[str, Any]:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("LLM response does not contain a JSON object.")
    obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("LLM response JSON is not an object.")
    return obj


def call_openai_compatible_chat(
    *,
    messages: list[dict[str, str]],
    api_key: str,
    model: str,
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        base_url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=int(timeout_seconds)) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP error {exc.code}: {detail}") from exc

    data = json.loads(raw)
    content = data["choices"][0]["message"]["content"]
    parsed = extract_json_object(content)
    parsed["_llm_metadata"] = {
        "provider": "openai_compatible",
        "model": model,
        "base_url": base_url,
    }
    return parsed


def generate_llm_interpretation(
    *,
    appliance_label: str,
    appliance_key: str,
    output_dir: Path,
    results_dir: Path,
    chosen_k: int,
    selected_cluster: int,
    dominant_cluster: int,
    selected_cycle_id: int,
    selected_case_csv: Path | None = None,
    completion_summary_json: Path | None = None,
    max_clusters: int = 8,
    enable_llm: bool | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = output_dir / "llm_interpretation_prompt.json"
    status_path = output_dir / "llm_interpretation_status.json"
    output_path = output_dir / "llm_interpretation.json"

    cluster_profiles = _cluster_rows(
        results_dir=results_dir,
        selected_cluster=selected_cluster,
        dominant_cluster=dominant_cluster,
        max_clusters=max_clusters,
    )
    prompt = build_interpretation_prompt(
        appliance_label=appliance_label,
        appliance_key=appliance_key,
        chosen_k=chosen_k,
        selected_cluster=selected_cluster,
        dominant_cluster=dominant_cluster,
        selected_cycle_id=selected_cycle_id,
        cluster_profiles=cluster_profiles,
        selected_case=_load_selected_case(selected_case_csv),
        completion=_load_completion_summary(completion_summary_json),
    )
    prompt_path.write_text(json.dumps(prompt, ensure_ascii=False, indent=2), encoding="utf-8")

    should_call = _enabled(os.environ.get("LLM_SM_ENABLE_INTERPRETATION")) if enable_llm is None else bool(enable_llm)
    api_key = api_key or os.environ.get("LLM_SM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    model = model or os.environ.get("LLM_SM_MODEL") or DEFAULT_MODEL
    base_url = base_url or os.environ.get("LLM_SM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL

    status: dict[str, Any] = {
        "enabled": should_call,
        "prompt_path": str(prompt_path),
        "output_path": str(output_path),
        "status_path": str(status_path),
        "model": model,
        "base_url": base_url,
    }
    if not should_call:
        status["status"] = "prompt_ready"
        status["reason"] = "Set LLM_SM_ENABLE_INTERPRETATION=1 to call the configured LLM endpoint."
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        return status
    if not api_key:
        status["status"] = "missing_api_key"
        status["reason"] = "Set LLM_SM_API_KEY or OPENAI_API_KEY to generate llm_interpretation.json."
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        return status

    interpretation = call_openai_compatible_chat(
        messages=prompt["messages"],
        api_key=api_key,
        model=model,
        base_url=base_url,
    )
    output_path.write_text(json.dumps(interpretation, ensure_ascii=False, indent=2), encoding="utf-8")
    status["status"] = "completed"
    status["interpretation_path"] = str(output_path)
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate LLM interpretations for Smart Meter clustering results.")
    parser.add_argument("--appliance-label", required=True)
    parser.add_argument("--appliance-key", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--chosen-k", required=True, type=int)
    parser.add_argument("--selected-cluster", required=True, type=int)
    parser.add_argument("--dominant-cluster", required=True, type=int)
    parser.add_argument("--selected-cycle-id", required=True, type=int)
    parser.add_argument("--selected-case-csv", type=Path, default=None)
    parser.add_argument("--completion-summary-json", type=Path, default=None)
    parser.add_argument("--max-clusters", type=int, default=8)
    parser.add_argument("--call-llm", action="store_true", help="Call the configured LLM endpoint.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    status = generate_llm_interpretation(
        appliance_label=args.appliance_label,
        appliance_key=args.appliance_key,
        output_dir=args.output_dir,
        results_dir=args.results_dir,
        chosen_k=args.chosen_k,
        selected_cluster=args.selected_cluster,
        dominant_cluster=args.dominant_cluster,
        selected_cycle_id=args.selected_cycle_id,
        selected_case_csv=args.selected_case_csv,
        completion_summary_json=args.completion_summary_json,
        max_clusters=args.max_clusters,
        enable_llm=args.call_llm,
    )
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
