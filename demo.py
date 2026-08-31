"""Run readable public-set dialogue traces without changing the evaluator.

This helper is presentation-only. Ground truth is used solely to label the
result after the Agent has responded; ``starter.agent.Agent`` never receives it.
"""

from __future__ import annotations

import argparse

from evaluator.local_evaluator import (
    MAX_TURNS,
    TOP_K,
    catalog_index,
    coarse_category,
    customer_reply,
    initial_message,
    load_jsonl,
    materialize_hidden_fields,
    normalize_recommendations,
)
from starter.agent import Agent


def run_trace(
    agent: Agent,
    sample: dict,
    catalog_ids: set[str],
    categories: dict[str, list[str]],
    products: dict[str, dict],
) -> None:
    """Replay one sample with the same deterministic policy as the evaluator."""
    sample_id = str(sample["sample_id"])
    session_id = f"demo_{sample_id}"
    target = str(sample["ground_truth"]["parent_asin"])
    card, behavior = materialize_hidden_fields(sample, products)
    effective_sample = {**sample, "intent_card": card, "behavior": behavior}
    agent.reset(session_id, sample["user_profile"])

    disclosed: set[str] = set()
    boundary_used = False
    override_applied = sample["scenario_type"] != "intent_override"
    user_message = initial_message(
        effective_sample,
        coarse_category(categories.get(target, [])),
        disclosed,
    )

    print(f"\n=== {sample_id} · {sample['scenario_type']} ===")
    for turn in range(1, MAX_TURNS + 1):
        print(f"\nTurn {turn} customer: {user_message}")
        response = agent.respond(session_id, user_message, turn, TOP_K)
        ranked = normalize_recommendations(response["recommendations"], catalog_ids)
        trace = agent.sessions[session_id]["route_history"][-1]
        print(f"Agent: {response['message']}")
        print(
            "Policy: "
            f"route={trace['route']}, candidates={trace['candidate_count']}, "
            f"ask_attribute={response['ask_attribute']}, "
            f"profile_terms_used={trace['profile_terms_used']}"
        )
        print("Ranked parent_asins: " + ", ".join(ranked))

        if override_applied and target in ranked:
            rank = ranked.index(target) + 1
            print(f"Result: target found at rank {rank} on turn {turn}.")
            return
        if not override_applied and target in ranked:
            print("Result: target present, but not score-eligible until the override.")
        if turn == MAX_TURNS:
            break

        override = effective_sample.get("behavior", {}).get("override") or {}
        if not override_applied and turn + 1 == int(override.get("turn", 3)):
            override_applied = True
            new_value = str(override.get("new_value", ""))
            if new_value:
                disclosed.add(new_value)
            user_message = str(
                override.get(
                    "message",
                    "Actually, please ignore my earlier preference.",
                )
            )
        else:
            user_message, boundary_used = customer_reply(
                effective_sample,
                response.get("ask_attribute"),
                disclosed,
                boundary_used,
            )
    print("Result: target not found within ten turns.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Show IntentCart dialogue traces")
    parser.add_argument(
        "sample_ids",
        nargs="*",
        default=["public_0007", "public_0003"],
        help="public sample IDs to trace",
    )
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    args = parser.parse_args()

    samples = {item["sample_id"]: item for item in load_jsonl(args.dataset)}
    unknown = [sample_id for sample_id in args.sample_ids if sample_id not in samples]
    if unknown:
        parser.error("unknown sample ID(s): " + ", ".join(unknown))

    catalog_ids, categories, products = catalog_index(args.catalog)
    agent = Agent(args.catalog)
    for sample_id in args.sample_ids:
        run_trace(agent, samples[sample_id], catalog_ids, categories, products)


if __name__ == "__main__":
    main()
