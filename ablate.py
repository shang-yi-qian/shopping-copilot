"""Reproduce the submission-v1 profile/diversity ablations on the public set."""

from __future__ import annotations

import argparse
import json

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from starter.agent import AgentV1


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an IntentCart feature ablation")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--disable-profile", action="store_true")
    parser.add_argument("--disable-diversity", action="store_true")
    args = parser.parse_args()

    catalog_ids, categories, products = catalog_index(args.catalog)
    result = evaluate(
        AgentV1(
            args.catalog,
            enable_profile=not args.disable_profile,
            enable_diversity=not args.disable_diversity,
        ),
        load_jsonl(args.dataset),
        catalog_ids,
        categories,
        products,
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "sessions"},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
