from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from starter.agent import Agent


ALLOWED_ATTRIBUTES = {
    "category",
    "material",
    "color",
    "size",
    "style",
    "brand",
    "budget",
    "feature",
    "use_case",
    "other",
    None,
}


def product(
    asin: str,
    title: str,
    *,
    category: str = "Shirts",
    features: list[str] | None = None,
    details: dict[str, str] | None = None,
    rating_number: int = 10,
    store: str = "Test Store",
) -> dict:
    return {
        "parent_asin": asin,
        "title": title,
        "features": features or [],
        "description": [],
        "price": None,
        "categories": ["Clothing, Shoes & Jewelry", "Men", "Clothing", category],
        "details": details or {"Department": "Mens"},
        "average_rating": 4.0,
        "rating_number": rating_number,
        "store": store,
    }


class AgentContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.catalog_path = Path(self.temporary_directory.name) / "catalog.jsonl"
        rows = [
            product(
                "TARGET",
                "Blue cotton button-down shirt",
                features=["cotton", "color: blue", "Button closure", "Machine Wash"],
                rating_number=50,
            ),
            product(
                "DECOY01",
                "Black cotton casual shirt",
                features=["cotton", "color: black", "Pull On closure"],
                rating_number=500,
            ),
            product(
                "DECOY02",
                "Blue polyester sports shirt",
                features=["polyester", "color: blue", "Zipper closure"],
                rating_number=400,
            ),
        ]
        for number in range(3, 18):
            rows.append(
                product(
                    f"DECOY{number:02d}",
                    f"Generic casual shirt number {number}",
                    features=["rayon", f"style number {number}", "Imported"],
                    rating_number=number,
                )
            )
        rows.extend(
            [
            product(
                "BOOTTOP",
                "Black polyester hiking boot",
                category="Boots",
                features=["polyester", "color: black", "Hiking traction"],
                rating_number=9_000,
            ),
            product(
                "BOOT001",
                "Blue leather hiking boot",
                category="Boots",
                features=["leather", "color: blue", "Hiking traction"],
            ),
            product(
                "DRESS01",
                "Red silk evening dress",
                category="Dresses",
                features=["silk", "color: red", "Evening style"],
            ),
            product(
                "AAAA_COTTON",
                "Cotton travel gloves",
                category="Accessories",
                features=["cotton", "Breathable travel gloves"],
                rating_number=12,
            ),
            product(
                "ZZZZ_LEATHER",
                "Leather travel gloves",
                category="Accessories",
                features=["leather", "Durable travel gloves"],
                rating_number=12,
            ),
            product(
                "HAT_A1",
                "Popular sun hat",
                category="Hats",
                features=["rayon", "Wide brim"],
                rating_number=300,
                store="Same Brand",
            ),
            product(
                "HAT_A2",
                "Classic sun hat",
                category="Hats",
                features=["cotton", "Wide brim"],
                rating_number=200,
                store="Same Brand",
            ),
            product(
                "HAT_A3",
                "Modern sun hat",
                category="Hats",
                features=["silk", "Wide brim"],
                rating_number=150,
                store="Same Brand",
            ),
            product(
                "HAT_B1",
                "Alternative sun hat",
                category="Hats",
                features=["nylon", "Wide brim"],
                rating_number=100,
                store="Other Brand",
            ),
            product(
                "COAT_GRAY",
                "Gray rayon insulated trail coat",
                category="Coats",
                features=["rayon", "color: gray", "Insulated trail style"],
                rating_number=10_000,
            ),
            product(
                "COAT_BLACK",
                "Black cotton insulated trail coat",
                category="Coats",
                features=["cotton", "color: black", "Insulated trail style"],
                rating_number=9_000,
            ),
            product(
                "COAT_BLUEP",
                "Blue polyester insulated trail coat",
                category="Coats",
                features=["polyester", "color: blue", "Insulated trail style"],
                rating_number=8_000,
            ),
            product(
                "COAT_TARGET",
                "Blue cotton insulated trail coat",
                category="Coats",
                features=["cotton", "color: blue", "Insulated trail style"],
                rating_number=5,
            ),
            {
                "parent_asin": "MISSING1",
                "title": None,
                "features": None,
                "description": None,
                "price": None,
                "categories": [],
                "details": None,
                "average_rating": None,
                "rating_number": None,
                "store": None,
            },
        ]
        )
        self.catalog_ids = {row["parent_asin"] for row in rows}
        self.catalog_path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
        self.agent = Agent(self.catalog_path)

    def reset(self, session_id: str = "session-a") -> None:
        self.agent.reset(
            session_id,
            {
                "purchase_frequency": "3-4 prior purchases",
                "average_prior_rating": 4.5,
                "rating_style": "usually positive",
                "preference_tags": ["fit", "material"],
                "summary": "Prior purchases emphasize fit and material.",
            },
        )

    def assert_valid_response(self, response: dict, top_k: int) -> list[str]:
        self.assertIsInstance(response, dict)
        self.assertEqual(
            set(response),
            {"message", "ask_attribute", "recommendations", "usage"},
        )
        self.assertIsInstance(response["message"], str)
        self.assertIn(response["ask_attribute"], ALLOWED_ATTRIBUTES)
        self.assertIsInstance(response["recommendations"], list)
        self.assertLessEqual(len(response["recommendations"]), top_k)
        asins: list[str] = []
        for item in response["recommendations"]:
            self.assertIsInstance(item, dict)
            self.assertIn("parent_asin", item)
            self.assertTrue(
                set(item).issubset({"parent_asin", "score"}),
                "A recommendation may contain only parent_asin and optional score",
            )
            self.assertIsInstance(item["parent_asin"], str)
            if "score" in item:
                self.assertIsInstance(item["score"], (int, float))
            self.assertIn(item["parent_asin"], self.catalog_ids)
            asins.append(item["parent_asin"])
        self.assertEqual(len(asins), len(set(asins)))
        self.assertIsInstance(response["usage"], dict)
        self.assertEqual(set(response["usage"]), {"prompt_tokens", "completion_tokens"})
        for value in response["usage"].values():
            self.assertIsInstance(value, int)
            self.assertGreaterEqual(value, 0)
        return asins

    def test_reset_is_required_before_respond(self) -> None:
        with self.assertRaises(RuntimeError):
            self.agent.respond("missing", "I need a shirt.", 1, 10)

    def test_missing_catalog_path_raises_actionable_file_error(self) -> None:
        missing_path = Path(self.temporary_directory.name) / "missing.jsonl"
        with self.assertRaises(FileNotFoundError):
            Agent(missing_path)

    def test_reset_copies_profile_instead_of_sharing_mutable_input(self) -> None:
        profile = {
            "preference_tags": ["fit", "material"],
            "summary": "Prior purchases emphasize fit and material.",
        }
        self.agent.reset("profile-session", profile)
        self.agent.respond("profile-session", "shirts", 1, 1)
        self.assertEqual(
            profile,
            {
                "preference_tags": ["fit", "material"],
                "summary": "Prior purchases emphasize fit and material.",
            },
        )
        profile["summary"] = "mutated outside the Agent"
        profile["preference_tags"].append("mutated")
        self.assertEqual(
            self.agent.sessions["profile-session"]["profile"]["summary"],
            "Prior purchases emphasize fit and material.",
        )
        self.assertEqual(
            self.agent.sessions["profile-session"]["profile"]["preference_tags"],
            ["fit", "material"],
        )
        profile_slots = self.agent.sessions["profile-session"]["profile_slot_metadata"]
        self.assertTrue(profile_slots)
        self.assertTrue(all(slot["source"] == "profile" for slot in profile_slots))
        self.assertTrue(all(not slot["hard"] for slot in profile_slots))

    def test_supplied_profile_is_a_soft_deterministic_rank_signal(self) -> None:
        profile = {
            "preference_tags": ["leather"],
            "summary": "Prior purchases emphasize leather.",
        }
        self.agent.reset("profile-on", profile)
        enabled = self.agent.respond(
            "profile-on",
            "I'm looking for Men Accessories, but I'm still exploring.",
            1,
            2,
        )
        enabled_asins = self.assert_valid_response(enabled, 2)
        without_profile = Agent(self.catalog_path, enable_profile=False)
        without_profile.reset("profile-off", profile)
        disabled = without_profile.respond(
            "profile-off",
            "I'm looking for Men Accessories, but I'm still exploring.",
            1,
            2,
        )
        disabled_asins = self.assert_valid_response(disabled, 2)
        self.assertEqual(enabled_asins[0], "ZZZZ_LEATHER")
        self.assertEqual(disabled_asins[0], "AAAA_COTTON")

    def test_response_matches_published_contract(self) -> None:
        self.reset()
        response = self.agent.respond(
            "session-a",
            "I'm looking for Shirts, but I'm still exploring.",
            1,
            10,
        )
        asins = self.assert_valid_response(response, 10)
        self.assertTrue(asins, "The Agent should recommend while asking a question")

    def test_ambiguous_turn_recommends_and_asks_other(self) -> None:
        self.reset()
        response = self.agent.respond(
            "session-a",
            "I'm looking for Shirts, but I'm still exploring.",
            1,
            10,
        )
        self.assert_valid_response(response, 10)
        self.assertEqual(response["ask_attribute"], "other")
        self.assertTrue(response["message"].strip())

    def test_recommendations_are_not_repeated_across_turns(self) -> None:
        self.reset()
        first = self.agent.respond("session-a", "blue cotton shirt", 1, 5)
        second = self.agent.respond("session-a", "blue cotton shirt", 2, 5)
        first_asins = set(self.assert_valid_response(first, 5))
        second_asins = set(self.assert_valid_response(second, 5))
        self.assertTrue(first_asins)
        self.assertTrue(second_asins)
        self.assertTrue(first_asins.isdisjoint(second_asins))

    def test_seen_asins_and_constraints_are_isolated_by_session(self) -> None:
        self.reset("session-a")
        self.reset("session-b")
        a_first = self.agent.respond("session-a", "blue cotton shirt", 1, 5)
        self.agent.respond("session-a", "blue cotton shirt", 2, 5)
        b_first = self.agent.respond("session-b", "blue cotton shirt", 1, 5)
        self.assertEqual(
            self.assert_valid_response(a_first, 5),
            self.assert_valid_response(b_first, 5),
        )

    def test_boundary_no_preference_keeps_valid_fallback(self) -> None:
        self.reset()
        response = self.agent.respond(
            "session-a",
            "I don't have a preference for other; please use your judgment.",
            2,
            10,
        )
        asins = self.assert_valid_response(response, 10)
        self.assertTrue(asins)
        self.assertEqual(response["ask_attribute"], "other")

    def test_exact_buying_constraint_outranks_popular_decoy(self) -> None:
        self.reset()
        response = self.agent.respond(
            "session-a",
            "I'm looking for Shirts. A key requirement is: color: blue.",
            1,
            10,
        )
        asins = self.assert_valid_response(response, 10)
        self.assertIn("TARGET", asins)
        self.assertLess(asins.index("TARGET"), asins.index("DECOY01"))
        state = self.agent.sessions["session-a"]
        self.assertEqual(state["mode"], "buying")
        self.assertEqual(state["route"], "precision")
        self.assertEqual(state["category"], "shirts")
        self.assertEqual(state["constraints"], ["color: blue"])
        self.assertEqual(state["slot_metadata"]["color: blue"]["source"], "opening")
        self.assertTrue(state["slot_metadata"]["color: blue"]["hard"])
        self.assertEqual(state["route_history"][-1]["route"], "precision")

    def test_browsing_opening_records_mode_without_fabricating_constraint(self) -> None:
        self.reset()
        response = self.agent.respond(
            "session-a",
            "I'm looking for Men Boots, but I'm still exploring.",
            1,
            3,
        )
        self.assert_valid_response(response, 3)
        state = self.agent.sessions["session-a"]
        self.assertEqual(state["mode"], "browsing")
        self.assertEqual(state["route"], "discovery")
        self.assertEqual(state["category"], "men boots")
        self.assertEqual(state["constraints"], [])

    def test_unconstrained_browsing_covers_distinct_product_families(self) -> None:
        self.reset()
        response = self.agent.respond(
            "session-a",
            "I'm looking for Men Hats, but I'm still exploring.",
            1,
            3,
        )
        asins = self.assert_valid_response(response, 3)
        self.assertEqual(asins, ["HAT_A1", "HAT_A2", "HAT_B1"])

        no_diversity = Agent(self.catalog_path, enable_diversity=False)
        no_diversity.reset("no-diversity", {})
        response = no_diversity.respond(
            "no-diversity",
            "I'm looking for Men Hats, but I'm still exploring.",
            1,
            3,
        )
        self.assertEqual(
            self.assert_valid_response(response, 3),
            ["HAT_A1", "HAT_A2", "HAT_A3"],
        )

    def test_browsing_category_persists_into_constraint_reply(self) -> None:
        self.reset()
        first = self.agent.respond(
            "session-a",
            "I'm looking for Men Boots, but I'm still exploring.",
            1,
            1,
        )
        first_asins = self.assert_valid_response(first, 1)
        self.assertEqual(first_asins, ["BOOTTOP"])
        self.assertEqual(first["ask_attribute"], "other")
        second = self.agent.respond(
            "session-a",
            "For that, what matters is: leather; color: blue.",
            2,
            5,
        )
        asins = self.assert_valid_response(second, 5)
        self.assertEqual(asins[0], "BOOT001")
        self.assertNotIn("BOOTTOP", asins)
        self.assertIsNone(second["ask_attribute"])

    def test_constraints_accumulate_in_disclosure_order(self) -> None:
        self.reset()
        first = self.agent.respond(
            "session-a",
            "I'm looking for Men Coats, but I'm still exploring.",
            1,
            1,
        )
        self.assert_valid_response(first, 1)
        second = self.agent.respond(
            "session-a",
            "For that, what matters is: cotton.",
            2,
            1,
        )
        self.assert_valid_response(second, 1)
        third = self.agent.respond(
            "session-a",
            "For that, what matters is: color: blue.",
            3,
            5,
        )
        asins = self.assert_valid_response(third, 5)
        self.assertIn("COAT_TARGET", asins)
        self.assertIn("COAT_BLUEP", asins)
        self.assertLess(asins.index("COAT_TARGET"), asins.index("COAT_BLUEP"))
        self.assertEqual(
            self.agent.sessions["session-a"]["constraints"],
            ["cotton", "color: blue"],
        )
        state = self.agent.sessions["session-a"]
        self.assertFalse(state["profile_active"])
        self.assertEqual(
            state["truncated_soft_slots"],
            list(state["profile_context"]),
        )

    def test_repeated_constraint_is_stored_once(self) -> None:
        self.reset()
        self.agent.respond(
            "session-a",
            "I'm looking for Men Coats, but I'm still exploring.",
            1,
            1,
        )
        self.agent.respond(
            "session-a",
            "For that, what matters is: cotton.",
            2,
            1,
        )
        response = self.agent.respond(
            "session-a",
            "For that, what matters is: cotton.",
            3,
            1,
        )
        self.assert_valid_response(response, 1)
        self.assertEqual(self.agent.sessions["session-a"]["constraints"], ["cotton"])

    def test_override_removes_old_preference_but_keeps_independent_constraint(self) -> None:
        self.reset()
        first = self.agent.respond(
            "session-a",
            "I'm looking for Men Coats. color: black.",
            1,
            1,
        )
        self.assert_valid_response(first, 1)
        second = self.agent.respond(
            "session-a",
            "For that, what matters is: cotton.",
            2,
            1,
        )
        self.assert_valid_response(second, 1)
        third = self.agent.respond(
            "session-a",
            "Actually, ignore my earlier preference. What I need is: color: blue.",
            3,
            5,
        )
        asins = self.assert_valid_response(third, 5)
        self.assertIn("COAT_TARGET", asins)
        self.assertIn("COAT_BLUEP", asins)
        self.assertLess(asins.index("COAT_TARGET"), asins.index("COAT_BLUEP"))

    def test_explicit_override_starts_new_recommendation_eligibility_epoch(self) -> None:
        self.reset()
        before = self.agent.respond(
            "session-a",
            "I'm looking for Men Coats. lightweight casual style",
            1,
            4,
        )
        before_asins = self.assert_valid_response(before, 4)
        self.assertIn("COAT_GRAY", before_asins)
        after = self.agent.respond(
            "session-a",
            "Actually, ignore my earlier preference. What I need is: rayon.",
            3,
            4,
        )
        after_asins = self.assert_valid_response(after, 4)
        self.assertEqual(after_asins[0], "COAT_GRAY")
        self.assertIn("COAT_GRAY", set(before_asins) & set(after_asins))
        self.assertEqual(self.agent.sessions["session-a"]["mode"], "intent_override")
        state = self.agent.sessions["session-a"]
        self.assertEqual(state["route"], "override_recovery")
        self.assertEqual(state["eligibility_epoch"], 1)
        self.assertEqual(state["slot_metadata"]["rayon"]["source"], "override")

    def test_empty_lexical_query_uses_nonempty_valid_fallback(self) -> None:
        self.reset()
        response = self.agent.respond("session-a", "", 1, 5)
        asins = self.assert_valid_response(response, 5)
        self.assertTrue(asins)
        self.assertEqual(self.agent.sessions["session-a"]["route"], "fallback")

    def test_unknown_constraint_does_not_collapse_candidate_pool(self) -> None:
        self.reset()
        response = self.agent.respond(
            "session-a",
            "I'm looking for Shirts. A key requirement is: unobtainium weave.",
            1,
            5,
        )
        asins = self.assert_valid_response(response, 5)
        self.assertTrue(asins)

    def test_top_k_is_respected_and_filled_when_catalog_has_capacity(self) -> None:
        self.reset()
        response = self.agent.respond("session-a", "shirts", 1, 3)
        asins = self.assert_valid_response(response, 3)
        self.assertEqual(len(asins), 3)

    def test_missing_optional_catalog_metadata_is_safe(self) -> None:
        self.reset()
        response = self.agent.respond("session-a", "clothing", 1, 10)
        self.assert_valid_response(response, 10)

    def test_final_turn_does_not_ask_an_unusable_question(self) -> None:
        self.reset()
        response = self.agent.respond(
            "session-a",
            "For that, what matters is: cotton; color: blue.",
            10,
            10,
        )
        self.assert_valid_response(response, 10)
        self.assertIsNone(response["ask_attribute"])

    def test_identical_fresh_agents_are_deterministic(self) -> None:
        self.reset()
        first = self.agent.respond("session-a", "blue cotton shirt", 1, 10)
        other = Agent(self.catalog_path)
        other.reset(
            "session-a",
            {
                "purchase_frequency": "3-4 prior purchases",
                "average_prior_rating": 4.5,
                "rating_style": "usually positive",
                "preference_tags": ["fit", "material"],
                "summary": "Prior purchases emphasize fit and material.",
            },
        )
        second = other.respond("session-a", "blue cotton shirt", 1, 10)
        self.assertEqual(first["recommendations"], second["recommendations"])


if __name__ == "__main__":
    unittest.main()
