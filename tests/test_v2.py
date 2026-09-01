from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from compare_versions import load_expected_env
from starter.agent import Agent, AgentV1
from starter.llm_reranker import OpenAIResponsesReranker, RerankResult


def product(asin: str, rating_number: int) -> dict:
    return {
        "parent_asin": asin,
        "title": "Cotton everyday shirt",
        "features": ["cotton", "Machine Wash"],
        "description": [],
        "price": None,
        "categories": ["Clothing, Shoes & Jewelry", "Men", "Clothing", "Shirts"],
        "details": {"Department": "Mens"},
        "average_rating": 4.0,
        "rating_number": rating_number,
        "store": "Test Store",
    }


class FakeReranker:
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0

    def rerank(self, *, candidates: list[dict], **_: object) -> RerankResult:
        self.calls += 1
        ordered = tuple(
            candidate["parent_asin"] for candidate in reversed(candidates)
        )
        return RerankResult(ordered, 17, 5, applied=True)


class SubmissionV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.catalog_path = Path(self.temporary_directory.name) / "catalog.jsonl"
        rows = [product("LOW", 1), product("HIGH", 1_000), product("MID", 100)]
        self.catalog_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        self.profile = {
            "purchase_frequency": "3-4 prior purchases",
            "average_prior_rating": 4.5,
            "rating_style": "usually positive",
            "preference_tags": ["fit"],
            "summary": "Prior purchases emphasize fit.",
        }

    def test_v1_remains_available_and_v2_calibrates_constrained_ties(self) -> None:
        v1 = AgentV1(self.catalog_path)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "unused"}, clear=True):
            v2 = Agent(self.catalog_path)
        self.assertFalse(v2.enable_llm)
        for candidate in (v1, v2):
            candidate.reset("session", self.profile)
        message = "I'm looking for Men Shirts. A key requirement is: cotton."
        v1_asins = [
            item["parent_asin"]
            for item in v1.respond("session", message, 1, 3)["recommendations"]
        ]
        v2_asins = [
            item["parent_asin"]
            for item in v2.respond("session", message, 1, 3)["recommendations"]
        ]
        self.assertEqual(v1_asins, ["LOW", "HIGH", "MID"])
        self.assertEqual(v2_asins, ["HIGH", "MID", "LOW"])

    def test_llm_reranks_only_the_deterministic_set_and_reports_usage(self) -> None:
        deterministic = Agent(self.catalog_path, enable_llm=False)
        fake = FakeReranker()
        hybrid = Agent(self.catalog_path, llm_reranker=fake)
        for candidate in (deterministic, hybrid):
            candidate.reset("session", self.profile)
        message = "I'm looking for Men Shirts. A key requirement is: cotton."
        original = deterministic.respond("session", message, 1, 3)
        reranked = hybrid.respond("session", message, 1, 3)
        original_asins = [item["parent_asin"] for item in original["recommendations"]]
        reranked_asins = [item["parent_asin"] for item in reranked["recommendations"]]
        self.assertEqual(reranked_asins, list(reversed(original_asins)))
        self.assertEqual(set(reranked_asins), set(original_asins))
        self.assertEqual(reranked["usage"], {"prompt_tokens": 17, "completion_tokens": 5})
        self.assertEqual(fake.calls, 1)
        trace = hybrid.sessions["session"]["route_history"][-1]
        self.assertTrue(trace["llm_attempted"])
        self.assertTrue(trace["llm_applied"])
        self.assertEqual(trace["llm_status"], "applied")

    def test_llm_is_skipped_until_explicit_constraints_exist(self) -> None:
        fake = FakeReranker()
        agent = Agent(self.catalog_path, llm_reranker=fake)
        agent.reset("session", self.profile)
        response = agent.respond(
            "session",
            "I'm looking for Men Shirts, but I'm still exploring.",
            1,
            3,
        )
        self.assertEqual(fake.calls, 0)
        self.assertEqual(response["usage"], {"prompt_tokens": 0, "completion_tokens": 0})
        self.assertEqual(
            agent.sessions["session"]["route_history"][-1]["llm_status"],
            "skipped_no_explicit_constraints",
        )

    def test_llm_is_skipped_when_constrained_pool_exceeds_safe_cutoff(self) -> None:
        large_catalog = Path(self.temporary_directory.name) / "large.jsonl"
        large_catalog.write_text(
            "".join(json.dumps(product(f"ITEM{number:02d}", number)) + "\n" for number in range(25)),
            encoding="utf-8",
        )
        fake = FakeReranker()
        agent = Agent(
            large_catalog,
            llm_reranker=fake,
            popularity_pool_cutoff=20,
        )
        agent.reset("session", self.profile)
        agent.respond(
            "session",
            "I'm looking for Men Shirts. A key requirement is: cotton.",
            1,
            10,
        )
        self.assertEqual(fake.calls, 0)
        trace = agent.sessions["session"]["route_history"][-1]
        self.assertEqual(trace["llm_status"], "skipped_large_candidate_pool")
        self.assertFalse(trace["popularity_calibration_applied"])

    def test_missing_api_key_uses_deterministic_fallback(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            agent = Agent(self.catalog_path, enable_llm=True)
        agent.reset("session", self.profile)
        response = agent.respond(
            "session",
            "I'm looking for Men Shirts. A key requirement is: cotton.",
            1,
            3,
        )
        self.assertEqual(len(response["recommendations"]), 3)
        trace = agent.sessions["session"]["route_history"][-1]
        self.assertEqual(trace["llm_status"], "fallback")
        self.assertEqual(trace["llm_error"], "missing_api_key")

    def test_structured_output_accepts_only_a_complete_candidate_permutation(self) -> None:
        candidates = [
            {"parent_asin": "A", "title": "First"},
            {"parent_asin": "B", "title": "Second"},
        ]

        def valid_request(payload: dict, timeout: float) -> dict:
            self.assertFalse(payload["store"])
            self.assertEqual(payload["text"]["format"]["type"], "json_schema")
            self.assertTrue(payload["text"]["format"]["strict"])
            self.assertGreaterEqual(timeout, 1.0)
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {"ordered_parent_asins": ["B", "A"]}
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 4},
            }

        valid = OpenAIResponsesReranker("secret", requester=valid_request).rerank(
            category="shirts",
            constraints=["cotton"],
            user_message="cotton shirt",
            candidates=candidates,
        )
        self.assertTrue(valid.applied)
        self.assertEqual(valid.ordered_parent_asins, ("B", "A"))
        self.assertEqual((valid.prompt_tokens, valid.completion_tokens), (20, 4))

        def invalid_request(_: dict, __: float) -> dict:
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
                                    {"ordered_parent_asins": ["INVENTED", "A"]}
                                ),
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 3},
            }

        invalid = OpenAIResponsesReranker("secret", requester=invalid_request).rerank(
            category="shirts",
            constraints=["cotton"],
            user_message="cotton shirt",
            candidates=candidates,
        )
        self.assertFalse(invalid.applied)
        self.assertEqual(invalid.ordered_parent_asins, ("A", "B"))
        self.assertEqual(invalid.error, "invalid_candidate_permutation")

    def test_env_file_loader_never_executes_or_loads_unknown_names(self) -> None:
        env_path = Path(self.temporary_directory.name) / ".env"
        env_path.write_text(
            "OPENAI_API_KEY='test-value'\n"
            "INTENTCART_LLM_MODEL=test-model\n"
            "UNEXPECTED=$(touch should-not-exist)\n",
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            loaded = load_expected_env(env_path)
            self.assertEqual(loaded, {"OPENAI_API_KEY", "INTENTCART_LLM_MODEL"})
            self.assertEqual(os.environ["OPENAI_API_KEY"], "test-value")
            self.assertNotIn("UNEXPECTED", os.environ)
        self.assertFalse((Path.cwd() / "should-not-exist").exists())


if __name__ == "__main__":
    unittest.main()
