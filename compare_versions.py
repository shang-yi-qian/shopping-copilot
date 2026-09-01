"""Compare immutable v1 behavior with the submission-v2 candidate.

The official evaluator remains unchanged. This harness only selects which Agent
class it instantiates, computes paired diagnostics, and optionally loads the
expected OpenAI variables from an ignored .env file without executing shell
syntax or printing secret values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import defaultdict
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl, metric_summary
from starter.agent import Agent, AgentV1
from starter.llm_reranker import DEFAULT_MODEL


EXPECTED_ENV_KEYS = {
    "OPENAI_API_KEY",
    "INTENTCART_LLM_MODEL",
}
PINNED_INPUT_USD_PER_MILLION = 0.20
PINNED_OUTPUT_USD_PER_MILLION = 1.25


def load_expected_env(path: Path) -> set[str]:
    """Load only known literal KEY=VALUE pairs; never execute file contents."""
    loaded: set[str] = set()
    if not path.is_file():
        return loaded
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in EXPECTED_ENV_KEYS or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[:1] == value[-1:] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value
        loaded.add(key)
    return loaded


def technical_score(summary: dict) -> float:
    efficiency = max(0.0, min(1.0, (11.0 - float(summary["mttc"])) / 10.0))
    return round(
        0.50 * float(summary["hit_rate_at_10"])
        + 0.30 * float(summary["mrr"])
        + 0.20 * efficiency,
        6,
    )


def paired_diagnostics(v1_sessions: list[dict], v2_sessions: list[dict]) -> dict:
    v1 = {item["sample_id"]: item for item in v1_sessions}
    v2 = {item["sample_id"]: item for item in v2_sessions}
    counts = {"better": 0, "worse": 0, "equal": 0}
    for sample_id, first in v1.items():
        second = v2[sample_id]
        first_quality = (
            -(first["first_hit_turn"] or 11),
            float(first["reciprocal_rank"]),
        )
        second_quality = (
            -(second["first_hit_turn"] or 11),
            float(second["reciprocal_rank"]),
        )
        if second_quality > first_quality:
            counts["better"] += 1
        elif second_quality < first_quality:
            counts["worse"] += 1
        else:
            counts["equal"] += 1

    slices: list[dict] = []
    grouped_v1: dict[int, list[dict]] = defaultdict(list)
    grouped_v2: dict[int, list[dict]] = defaultdict(list)
    for sample_id, item in v1.items():
        fold = int.from_bytes(
            hashlib.sha256(sample_id.encode("utf-8")).digest()[:4], "big"
        ) % 5
        grouped_v1[fold].append(item)
        grouped_v2[fold].append(v2[sample_id])
    for fold in range(5):
        if not grouped_v1[fold]:
            continue
        first = metric_summary(grouped_v1[fold])
        second = metric_summary(grouped_v2[fold])
        first_score = technical_score(first)
        second_score = technical_score(second)
        slices.append(
            {
                "fold": fold,
                "sample_count": len(grouped_v1[fold]),
                "v1_score": first_score,
                "v2_score": second_score,
                "delta": round(second_score - first_score, 6),
            }
        )
    return {"paired_sessions": counts, "deterministic_slices": slices}


def public_summary(result: dict) -> dict:
    return {key: value for key, value in result.items() if key != "sessions"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare IntentCart v1 and v2")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", default="comparison_results.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--with-llm", action="store_true")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--model",
        default=None,
        help="optional model override; otherwise use INTENTCART_LLM_MODEL or the pinned default",
    )
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--popularity-cutoff", type=int, default=20)
    args = parser.parse_args()

    loaded_env_keys: set[str] = set()
    if args.with_llm:
        loaded_env_keys = load_expected_env(Path(args.env_file))
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise SystemExit(
                "--with-llm requires OPENAI_API_KEY in the process environment "
                "or the specified ignored env file"
            )
    resolved_model = (
        args.model or os.environ.get("INTENTCART_LLM_MODEL") or DEFAULT_MODEL
    )

    samples = load_jsonl(args.dataset)
    if args.limit is not None:
        if args.limit < 1:
            raise SystemExit("--limit must be at least 1")
        samples = samples[: args.limit]
    catalog_ids, categories, products = catalog_index(args.catalog)

    started = time.perf_counter()
    v1_agent = AgentV1(args.catalog)
    v1_result = evaluate(v1_agent, samples, catalog_ids, categories, products)
    v1_seconds = time.perf_counter() - started

    started = time.perf_counter()
    v2_agent = Agent(
        args.catalog,
        enable_llm=False,
        llm_model=resolved_model,
        llm_timeout_seconds=args.timeout,
        popularity_pool_cutoff=args.popularity_cutoff,
    )
    v2_result = evaluate(v2_agent, samples, catalog_ids, categories, products)
    v2_seconds = time.perf_counter() - started

    hybrid_agent = None
    hybrid_result = None
    hybrid_seconds = None
    if args.with_llm:
        started = time.perf_counter()
        hybrid_agent = Agent(
            args.catalog,
            enable_llm=True,
            llm_model=resolved_model,
            llm_timeout_seconds=args.timeout,
            popularity_pool_cutoff=args.popularity_cutoff,
        )
        hybrid_result = evaluate(
            hybrid_agent, samples, catalog_ids, categories, products
        )
        hybrid_seconds = time.perf_counter() - started

    deterministic_diagnostics = paired_diagnostics(
        v1_result["sessions"], v2_result["sessions"]
    )
    hybrid_diagnostics = (
        paired_diagnostics(v2_result["sessions"], hybrid_result["sessions"])
        if hybrid_result is not None
        else None
    )
    v1_score = float(v1_result["recommended_technical_score"])
    v2_score = float(v2_result["recommended_technical_score"])
    v2_reliability_preserved = all(
        float(v2_result["scenario_metrics"][name]["hit_rate_at_10"])
        >= float(v1_result["scenario_metrics"][name]["hit_rate_at_10"])
        for name in v1_result["scenario_metrics"]
    )
    hybrid_score = (
        float(hybrid_result["recommended_technical_score"])
        if hybrid_result is not None
        else None
    )
    hybrid_reliability_preserved = (
        all(
            float(hybrid_result["scenario_metrics"][name]["hit_rate_at_10"])
            >= float(v2_result["scenario_metrics"][name]["hit_rate_at_10"])
            for name in v2_result["scenario_metrics"]
        )
        if hybrid_result is not None
        else None
    )
    usage = (
        hybrid_result["reported_token_usage"]
        if hybrid_result is not None
        else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    )
    estimated_cost = None
    if hybrid_result is not None and resolved_model == DEFAULT_MODEL:
        estimated_cost = round(
            float(usage["prompt_tokens"]) / 1_000_000 * PINNED_INPUT_USD_PER_MILLION
            + float(usage["completion_tokens"])
            / 1_000_000
            * PINNED_OUTPUT_USD_PER_MILLION,
            6,
        )

    score_candidates = [("v1", v1_score), ("v2", v2_score)]
    if hybrid_score is not None:
        score_candidates.append(("v2_llm", hybrid_score))

    comparison = {
        "sample_count": len(samples),
        "catalog": args.catalog,
        "dataset": args.dataset,
        "v1_class": "starter.agent.AgentV1",
        "v2_class": "starter.agent.Agent",
        "v2_llm_requested": bool(args.with_llm),
        "v2_model": resolved_model if args.with_llm else None,
        "v2_popularity_pool_cutoff": args.popularity_cutoff,
        "env_file_used": bool(args.with_llm and loaded_env_keys),
        "env_keys_loaded": sorted(key for key in loaded_env_keys if key != "OPENAI_API_KEY"),
        "api_key_loaded": bool(args.with_llm and os.environ.get("OPENAI_API_KEY")),
        "v1_elapsed_seconds": round(v1_seconds, 6),
        "v2_elapsed_seconds": round(v2_seconds, 6),
        "v2_llm_elapsed_seconds": (
            round(hybrid_seconds, 6) if hybrid_seconds is not None else None
        ),
        "v1": v1_result,
        "v2": v2_result,
        "v2_llm": hybrid_result,
        "v2_llm_stats": (
            {
                **{
                    key: value
                    for key, value in hybrid_agent.llm_stats.items()
                    if key != "errors"
                },
                "errors": dict(hybrid_agent.llm_stats["errors"]),
            }
            if hybrid_agent is not None
            else None
        ),
        "estimated_model_cost_usd": estimated_cost,
        "pricing_basis": (
            {
                "model": DEFAULT_MODEL,
                "input_usd_per_million": PINNED_INPUT_USD_PER_MILLION,
                "output_usd_per_million": PINNED_OUTPUT_USD_PER_MILLION,
                "checked_date": "2026-09-01",
            }
            if estimated_cost is not None
            else None
        ),
        "v1_vs_v2": deterministic_diagnostics,
        "v2_vs_v2_llm": hybrid_diagnostics,
        "selection": {
            "v2_scenario_hit_reliability_preserved": v2_reliability_preserved,
            "v2_score_delta_vs_v1": round(v2_score - v1_score, 6),
            "v2_llm_scenario_hit_reliability_preserved": hybrid_reliability_preserved,
            "v2_llm_score_delta_vs_v2": (
                round(hybrid_score - v2_score, 6)
                if hybrid_score is not None
                else None
            ),
            "higher_public_score": max(score_candidates, key=lambda item: item[1])[0],
            "automatic_release_decision": "none_public_set_is_not_an_untouched_holdout",
        },
    }
    Path(args.output).write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "sample_count": len(samples),
                "v1": public_summary(v1_result),
                "v2": public_summary(v2_result),
                "v2_llm": (
                    public_summary(hybrid_result)
                    if hybrid_result is not None
                    else None
                ),
                "v2_llm_stats": comparison["v2_llm_stats"],
                "estimated_model_cost_usd": estimated_cost,
                "v1_vs_v2": deterministic_diagnostics,
                "v2_vs_v2_llm": hybrid_diagnostics,
                "selection": comparison["selection"],
                "output": args.output,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
