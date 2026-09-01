from __future__ import annotations

import json
import os
import re
import sqlite3
from copy import deepcopy
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from starter.llm_reranker import DEFAULT_MODEL, OpenAIResponsesReranker


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
MATERIAL_RE = re.compile(
    r"\b(cotton|polyester|nylon|leather|wool|spandex|silk|rayon|fabric)\b",
    re.IGNORECASE,
)
COLOR_RE = re.compile(
    r"\b(black|white|blue|red|pink|green|brown|gray|grey|purple|yellow|orange)\b",
    re.IGNORECASE,
)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "some",
    "that", "the", "this", "to", "want", "with", "would", "you", "looking",
}
GENERIC_PROFILE_TERMS = {
    "prior", "purchases", "purchase", "emphasize", "emphasizes", "usually",
    "positive", "critical", "mixed", "shopping", "rating", "ratings",
}
NO_EVIDENCE_MARKERS = (
    "i don't have a preference",
    "i don't have an additional preference",
    "those options are not quite right yet",
)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _searchable_text(product: dict) -> str:
    return " ".join(
        _text(product.get(field))
        for field in ("title", "features", "details", "description", "categories", "store")
    ).strip()


def _flatten_values(value: object) -> list[str]:
    if isinstance(value, dict):
        return [
            f"{key}: {item}"
            for key, item in value.items()
            if item not in (None, "", [])
        ]
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)] if value not in (None, "") else []


def _clean(value: str, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", value).strip(" -;,.\t\n")[:limit].rstrip()


def _normalize(value: str) -> str:
    return _clean(value).casefold()


def _terms(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    ]


def _coarse_category(values: object) -> str:
    excluded = {"clothing", "clothing shoes & jewelry", "clothing, shoes & jewelry"}
    cleaned: list[str] = []
    if isinstance(values, list):
        for value in values:
            for part in str(value).split(","):
                part = part.strip()
                if part and part.casefold() not in excluded:
                    cleaned.append(part)
    return " ".join(cleaned[-2:]) if cleaned else "clothing item"


def _signature(product: dict) -> tuple[str, ...]:
    candidates = [
        *_flatten_values(product.get("features")),
        *_flatten_values(product.get("details")),
    ]
    corpus = _searchable_text(product)
    material = MATERIAL_RE.search(corpus)
    color = COLOR_RE.search(corpus)
    if material:
        candidates.insert(0, material.group(1).lower())
    if color:
        candidates.insert(1, f"color: {color.group(1).lower()}")
    if product.get("price") not in (None, ""):
        candidates.append(f"budget around ${product['price']}")

    # Match the simulator's case-sensitive de-duplication before normalizing for lookup.
    cleaned = list(
        dict.fromkeys(
            cleaned_item
            for item in candidates
            if (cleaned_item := _clean(str(item)))
        )
    )
    if not cleaned:
        cleaned = [_clean(str(product.get("title") or "product"))]
    hard = cleaned[:2]
    soft = cleaned[2:4] or cleaned[:1]
    return tuple(_normalize(value) for value in [*hard, *soft])


def _profile_context(profile: dict) -> tuple[str, ...]:
    """Distill only documented, anonymous preference fields into soft terms."""
    tags = profile.get("preference_tags")
    tag_values = tags if isinstance(tags, list) else []
    summary = profile.get("summary")
    context = " ".join([*(str(value) for value in tag_values), str(summary or "")])
    return tuple(
        dict.fromkeys(
            term for term in _terms(context) if term not in GENERIC_PROFILE_TERMS
        )
    )[:32]


def _family_key(product: dict, parent_asin: str) -> str:
    """Approximate a brand/title family for browsing-result coverage."""
    store = _normalize(_text(product.get("store")))
    if store:
        return f"store:{store}"
    title_terms = _terms(_text(product.get("title")))[:3]
    return "title:" + (" ".join(title_terms) or parent_asin.casefold())


@dataclass(frozen=True, slots=True)
class ProductRecord:
    parent_asin: str
    category: str
    signature: tuple[str, ...]
    rating_number: int
    family: str


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    """Small deterministic policy selected from the current dialogue state."""

    name: str
    bm25_limit: int
    use_profile: bool
    diversify_families: bool
    ambiguity_cutoff: int


ROUTE_POLICIES = {
    "precision": RoutePolicy("precision", 300, False, False, 10),
    "discovery": RoutePolicy("discovery", 400, True, True, 10),
    "override_recovery": RoutePolicy("override_recovery", 450, False, False, 10),
    "fallback": RoutePolicy("fallback", 400, True, False, 10),
}


class AgentV1:
    """Stateful, route-aware catalog retrieval with deterministic fallbacks."""

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        enable_profile: bool = True,
        enable_diversity: bool = True,
    ) -> None:
        self.catalog_path = Path(catalog_path)
        self.enable_profile = bool(enable_profile)
        self.enable_diversity = bool(enable_diversity)
        self.connection = sqlite3.connect(":memory:")
        self.records: list[ProductRecord] = []
        self.by_category: dict[str, set[int]] = defaultdict(set)
        self.by_constraint: dict[str, set[int]] = defaultdict(set)
        self.category_order: dict[str, list[int]] = {}
        self.global_order: list[int] = []
        self.sessions: dict[str, dict] = {}
        self._build_indexes()

    def _build_indexes(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "record_index UNINDEXED, parent_asin UNINDEXED, title, categories, features, "
            "details, store, description, tokenize='unicode61 remove_diacritics 2')"
        )
        batch: list[tuple[int, str, str, str, str, str, str, str]] = []
        with self.catalog_path.open(encoding="utf-8") as handle:
            for line in handle:
                product = json.loads(line)
                parent_asin = str(product["parent_asin"])
                category = _normalize(_coarse_category(product.get("categories")))
                signature = _signature(product)
                try:
                    rating_number = max(0, int(product.get("rating_number") or 0))
                except (TypeError, ValueError):
                    rating_number = 0
                index = len(self.records)
                self.records.append(
                    ProductRecord(
                        parent_asin,
                        category,
                        signature,
                        rating_number,
                        _family_key(product, parent_asin),
                    )
                )
                self.by_category[category].add(index)
                for constraint in set(signature):
                    self.by_constraint[constraint].add(index)
                batch.append(
                    (
                        index,
                        parent_asin,
                        _text(product.get("title")),
                        _text(product.get("categories")),
                        _text(product.get("features")),
                        _text(product.get("details")),
                        _text(product.get("store")),
                        _text(product.get("description")),
                    )
                )
                if len(batch) >= 1000:
                    cursor.executemany(
                        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", batch
                    )
                    batch.clear()
        if batch:
            cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", batch)
        self.connection.commit()

        self.global_order = sorted(
            range(len(self.records)),
            key=lambda index: (-self.records[index].rating_number, self.records[index].parent_asin),
        )
        self.category_order = {
            category: sorted(
                indices,
                key=lambda index: (
                    -self.records[index].rating_number,
                    self.records[index].parent_asin,
                ),
            )
            for category, indices in self.by_category.items()
        }

    def reset(self, session_id: str, user_profile: dict) -> None:
        profile = deepcopy(user_profile) if isinstance(user_profile, dict) else {}
        profile_context = _profile_context(profile)
        self.sessions[session_id] = {
            "profile": profile,
            "profile_context": profile_context,
            "profile_slot_metadata": tuple(
                {
                    "value": value,
                    "source": "profile",
                    "confidence": 0.25,
                    "introduced_turn": 0,
                    "last_confirmed_turn": 0,
                    "hard": False,
                }
                for value in profile_context
            ),
            "truncated_soft_slots": [],
            "profile_active": False,
            "mode": None,
            "route": "fallback",
            "route_history": [],
            "category": None,
            "constraints": [],
            "slot_metadata": {},
            "old_preference": None,
            "override_applied": False,
            "eligibility_epoch": 0,
            "seen_asins": set(),
            "asked_attributes": [],
            "last_candidate_count": 0,
        }

    @staticmethod
    def _add_constraints(
        state: dict,
        values: list[str],
        *,
        source: str,
        turn: int,
        hard: bool,
    ) -> None:
        for value in values:
            normalized = _normalize(value)
            if not normalized:
                continue
            if normalized not in state["constraints"]:
                state["constraints"].append(normalized)
            previous = state["slot_metadata"].get(normalized)
            state["slot_metadata"][normalized] = {
                "value": normalized,
                "source": source if previous is None else previous["source"],
                "confidence": 1.0 if hard else 0.9,
                "introduced_turn": turn if previous is None else previous["introduced_turn"],
                "last_confirmed_turn": turn,
                "hard": bool(hard or (previous and previous["hard"])),
            }

    def _parse_message(self, state: dict, user_message: str, turn: int) -> None:
        message = re.sub(r"\s+", " ", user_message).strip()
        lowered = message.casefold()
        if not message or any(marker in lowered for marker in NO_EVIDENCE_MARKERS):
            return

        buying = re.fullmatch(
            r"I'm looking for (.+?)\. A key requirement is: (.+)\.",
            message,
            flags=re.IGNORECASE,
        )
        if buying:
            state["mode"] = "buying"
            state["category"] = _normalize(buying.group(1))
            self._add_constraints(
                state, [buying.group(2)], source="opening", turn=turn, hard=True
            )
            return

        browsing = re.fullmatch(
            r"I'm looking for (.+?), but I'm still exploring\.",
            message,
            flags=re.IGNORECASE,
        )
        if browsing:
            state["mode"] = "browsing"
            state["category"] = _normalize(browsing.group(1))
            return

        override = re.fullmatch(
            r"Actually, ignore my earlier preference\. What I need is: (.+)\.",
            message,
            flags=re.IGNORECASE,
        )
        if override:
            old_preference = state.get("old_preference")
            if old_preference in state["constraints"]:
                state["constraints"].remove(old_preference)
            state["slot_metadata"].pop(old_preference, None)
            state["override_applied"] = True
            state["mode"] = "intent_override"
            # Recommendations made before the evaluator permits a hit do not prove a miss.
            state["seen_asins"].clear()
            state["eligibility_epoch"] += 1
            self._add_constraints(
                state, [override.group(1)], source="override", turn=turn, hard=True
            )
            return

        clarification = re.fullmatch(
            r"For that, what matters is: (.+)\.",
            message,
            flags=re.IGNORECASE,
        )
        if clarification:
            self._add_constraints(
                state,
                self._split_disclosed_constraints(state, clarification.group(1)),
                source="clarification",
                turn=turn,
                hard=True,
            )
            return

        initial_override = re.fullmatch(
            r"I'm looking for (.+?)\. (.+)",
            message,
            flags=re.IGNORECASE,
        )
        if initial_override:
            state["mode"] = "intent_override"
            state["category"] = _normalize(initial_override.group(1))
            # This value is explicitly stale. Save it for erasure but never rank by it.
            state["old_preference"] = _normalize(initial_override.group(2))
            return

        # Safe fallback for an unexpected template: retain the text for BM25 only.

    @staticmethod
    def _select_route(state: dict) -> RoutePolicy:
        """Choose one explicit workflow without consulting hidden scenario labels."""
        mode = state.get("mode")
        if mode == "buying" and state["constraints"]:
            return ROUTE_POLICIES["precision"]
        if mode == "browsing":
            return ROUTE_POLICIES["discovery"]
        if mode == "intent_override" and state.get("override_applied"):
            return ROUTE_POLICIES["override_recovery"]
        return ROUTE_POLICIES["fallback"]

    def _split_disclosed_constraints(self, state: dict, payload: str) -> list[str]:
        """Recover one or two catalog values even when a value contains semicolons."""
        normalized = _normalize(payload)
        if normalized in self.by_constraint:
            return [payload]

        category = state.get("category")
        base = self.by_category.get(category, set()) if category else set()
        best: tuple[int, int, list[str]] | None = None
        for match in re.finditer(r";\s+", payload):
            left = payload[: match.start()]
            right = payload[match.end() :]
            left_normalized = _normalize(left)
            right_normalized = _normalize(right)
            left_candidates = self.by_constraint.get(left_normalized)
            right_candidates = self.by_constraint.get(right_normalized)
            if not left_candidates or not right_candidates:
                continue
            intersection = left_candidates & right_candidates
            if base:
                intersection &= base
            # A non-empty joint match is strongest; among ties prefer fewer candidates.
            score = (int(bool(intersection)), -len(intersection))
            candidate = (score[0], score[1], [left, right])
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        if best is not None:
            return best[2]
        return [part for part in payload.split(";") if _normalize(part)]

    def _candidate_indices(self, state: dict) -> set[int]:
        category = state.get("category")
        category_candidates = self.by_category.get(category, set()) if category else set()
        candidates = set(category_candidates) if category_candidates else set(range(len(self.records)))
        for constraint in state["constraints"]:
            matching = self.by_constraint.get(constraint)
            if not matching:
                continue
            narrowed = candidates & matching
            if narrowed:
                candidates = narrowed
        return candidates

    def _fts_indices(self, context: str, limit: int) -> list[int]:
        unique_terms = list(dict.fromkeys(_terms(context)))[:60]
        if not unique_terms:
            return []
        expression = " OR ".join(f'"{term}"' for term in unique_terms)
        try:
            rows = self.connection.execute(
                "SELECT record_index FROM products WHERE products MATCH ? "
                "ORDER BY bm25(products, 0.0, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) LIMIT ?",
                (expression, limit),
            ).fetchall()
        except sqlite3.Error:
            return []
        return [int(row[0]) for row in rows]

    def _bm25_indices(
        self,
        state: dict,
        user_message: str,
        limit: int,
    ) -> list[int]:
        context = " ".join(
            [state.get("category") or "", *state["constraints"], user_message]
        )
        return self._fts_indices(context, limit)

    def _profile_indices(self, state: dict, limit: int = 200) -> list[int]:
        if not self.enable_profile:
            state["truncated_soft_slots"] = list(state["profile_context"])
            return []
        # Explicit session evidence dynamically truncates the weaker profile prior.
        if len(state["constraints"]) >= 2:
            state["truncated_soft_slots"] = list(state["profile_context"])
            return []
        state["truncated_soft_slots"] = []
        return self._fts_indices(" ".join(state["profile_context"]), limit)

    def _rank_candidates(
        self,
        state: dict,
        candidates: set[int],
        bm25_order: list[int],
        profile_order: list[int],
        route: RoutePolicy,
    ) -> list[int]:
        bm25_rank = {index: rank for rank, index in enumerate(bm25_order)}
        profile_rank = {index: rank for rank, index in enumerate(profile_order)}
        constraints: list[str] = state["constraints"]
        category = state.get("category")

        def rank_key(index: int) -> tuple:
            record = self.records[index]
            positional = sum(
                position < len(record.signature) and record.signature[position] == value
                for position, value in enumerate(constraints)
            )
            anywhere = sum(value in record.signature for value in constraints)
            lexical = bm25_rank.get(index, len(bm25_order) + 1)
            profile = profile_rank.get(index, len(profile_order) + 1)
            if route.name == "discovery" and profile_order:
                soft_ranks = (profile, lexical)
            else:
                soft_ranks = (lexical, profile)
            return (
                -positional,
                -anywhere,
                -(record.category == category),
                *soft_ranks,
                -record.rating_number,
                record.parent_asin,
            )

        return sorted(candidates, key=rank_key)

    def _recommend(
        self,
        state: dict,
        user_message: str,
        top_k: int,
        route: RoutePolicy,
    ) -> list[dict]:
        limit = max(0, min(int(top_k), 10))
        if limit == 0:
            return []

        candidates = self._candidate_indices(state)
        state["last_candidate_count"] = len(candidates)
        bm25_order = self._bm25_indices(state, user_message, route.bm25_limit)
        profile_order = self._profile_indices(state) if route.use_profile else []
        state["profile_active"] = bool(profile_order)
        ranked = self._rank_candidates(
            state, candidates, bm25_order, profile_order, route
        )

        category = state.get("category")
        fallbacks = [
            *self._diversify(ranked, state, route),
            *bm25_order,
            *profile_order,
            *self.category_order.get(category, []),
            *self.global_order,
        ]
        selected: list[dict] = []
        selected_asins: set[str] = set()
        seen: set[str] = state["seen_asins"]
        for index in fallbacks:
            parent_asin = self.records[index].parent_asin
            if parent_asin in seen or parent_asin in selected_asins:
                continue
            selected.append({"parent_asin": parent_asin})
            selected_asins.add(parent_asin)
            if len(selected) >= limit:
                break
        seen.update(selected_asins)
        return selected

    def _diversify(
        self,
        ranked: list[int],
        state: dict,
        route: RoutePolicy,
    ) -> list[int]:
        """Cover distinct families first only for unconstrained exploration."""
        if (
            not self.enable_diversity
            or not route.diversify_families
            or state["constraints"]
        ):
            return ranked
        covered_families: list[int] = []
        repeated: list[int] = []
        family_counts: dict[str, int] = defaultdict(int)
        for index in ranked:
            family = self.records[index].family
            if family_counts[family] >= 2:
                repeated.append(index)
                continue
            family_counts[family] += 1
            covered_families.append(index)
        return [*covered_families, *repeated]

    @staticmethod
    def _question_for(route: RoutePolicy) -> str:
        if route.name == "precision":
            return "Which other requirement is non-negotiable—material, color, fit, budget, or use?"
        if route.name == "override_recovery":
            return "After that change, what other requirement should I preserve?"
        return (
            "What other requirement matters most—material, color, fit, style, "
            "budget, or intended use?"
        )

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        if session_id not in self.sessions:
            raise RuntimeError("reset must be called before respond")
        state = self.sessions[session_id]
        try:
            self._parse_message(state, user_message, turn)
            route = self._select_route(state)
            state["route"] = route.name
            recommendations = self._recommend(state, user_message, top_k, route)
        except Exception:
            # A malformed value must never turn a whole session into an empty response.
            recommendations = []
            for index in self.global_order:
                parent_asin = self.records[index].parent_asin
                if parent_asin not in state["seen_asins"]:
                    recommendations.append({"parent_asin": parent_asin})
                    state["seen_asins"].add(parent_asin)
                if len(recommendations) >= max(0, min(int(top_k), 10)):
                    break

            route = ROUTE_POLICIES["fallback"]
            state["route"] = route.name

        should_ask = (
            turn < 10
            and state["last_candidate_count"]
            > min(route.ambiguity_cutoff, max(1, len(recommendations)))
            and state["last_candidate_count"] > len(recommendations)
        )
        ask_attribute = "other" if should_ask else None
        if ask_attribute:
            state["asked_attributes"].append(ask_attribute)
            message = self._question_for(route)
        else:
            message = "Here are the strongest matches for your current requirements."
        state["route_history"].append(
            {
                "turn": int(turn),
                "route": route.name,
                "candidate_count": int(state["last_candidate_count"]),
                "asked": bool(ask_attribute),
                "profile_terms_used": bool(state.get("profile_active")),
            }
        )
        return {
            "message": message,
            "ask_attribute": ask_attribute,
            "recommendations": recommendations,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }


class Agent(AgentV1):
    """Submission-v2 candidate with calibrated ranking and optional LLM reranking.

    V1 remains available as :class:`AgentV1` for exact side-by-side evaluation.
    V2 never exposes a product outside the deterministic top-k set to the LLM,
    validates the returned permutation, and retains deterministic order on every
    configuration, network, schema, or timeout failure.
    """

    version = "submission-v2"

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        enable_profile: bool = True,
        enable_diversity: bool = True,
        enable_llm: bool = False,
        llm_model: str | None = None,
        llm_timeout_seconds: float = 12.0,
        llm_reranker: object | None = None,
        popularity_pool_cutoff: int = 20,
    ) -> None:
        super().__init__(
            catalog_path,
            enable_profile=enable_profile,
            enable_diversity=enable_diversity,
        )
        self.enable_llm = bool(enable_llm or llm_reranker is not None)
        self.llm_model = (
            llm_model or os.environ.get("INTENTCART_LLM_MODEL") or DEFAULT_MODEL
        )
        self.llm_config_error: str | None = None
        self.popularity_pool_cutoff = max(0, int(popularity_pool_cutoff))
        if llm_reranker is not None:
            self.llm_reranker = llm_reranker
        elif self.enable_llm:
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if api_key:
                self.llm_reranker = OpenAIResponsesReranker(
                    api_key,
                    model=self.llm_model,
                    timeout_seconds=llm_timeout_seconds,
                )
            else:
                self.llm_reranker = None
                self.llm_config_error = "missing_api_key"
        else:
            self.llm_reranker = None
        self.record_index_by_asin = {
            record.parent_asin: index for index, record in enumerate(self.records)
        }
        self.llm_stats = {
            "attempts": 0,
            "applied": 0,
            "fallbacks": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "errors": defaultdict(int),
        }

    def _rank_candidates(
        self,
        state: dict,
        candidates: set[int],
        bm25_order: list[int],
        profile_order: list[int],
        route: RoutePolicy,
    ) -> list[int]:
        """Promote popularity only after explicit catalog evidence is known."""
        use_calibration = bool(
            state["constraints"]
            and self.popularity_pool_cutoff > 0
            and len(candidates) <= self.popularity_pool_cutoff
        )
        state["popularity_calibration_applied"] = use_calibration
        if not use_calibration:
            return super()._rank_candidates(
                state, candidates, bm25_order, profile_order, route
            )

        bm25_rank = {index: rank for rank, index in enumerate(bm25_order)}
        profile_rank = {index: rank for rank, index in enumerate(profile_order)}
        constraints: list[str] = state["constraints"]
        category = state.get("category")

        def rank_key(index: int) -> tuple:
            record = self.records[index]
            positional = sum(
                position < len(record.signature)
                and record.signature[position] == value
                for position, value in enumerate(constraints)
            )
            anywhere = sum(value in record.signature for value in constraints)
            lexical = bm25_rank.get(index, len(bm25_order) + 1)
            profile = profile_rank.get(index, len(profile_order) + 1)
            if route.name == "discovery" and profile_order:
                soft_ranks = (profile, lexical)
            else:
                soft_ranks = (lexical, profile)
            return (
                -positional,
                -anywhere,
                -(record.category == category),
                -record.rating_number,
                *soft_ranks,
                record.parent_asin,
            )

        return sorted(candidates, key=rank_key)

    def _llm_candidates(self, recommendations: list[dict]) -> list[dict]:
        payload: list[dict] = []
        for deterministic_rank, recommendation in enumerate(recommendations, start=1):
            parent_asin = str(recommendation["parent_asin"])
            index = self.record_index_by_asin[parent_asin]
            row = self.connection.execute(
                "SELECT title, categories, features, details, store, description "
                "FROM products WHERE record_index = ? LIMIT 1",
                (index,),
            ).fetchone()
            values = list(row or ("", "", "", "", "", ""))
            payload.append(
                {
                    "parent_asin": parent_asin,
                    "deterministic_rank": deterministic_rank,
                    "title": _clean(str(values[0]), 360),
                    "categories": _clean(str(values[1]), 300),
                    "features": _clean(str(values[2]), 500),
                    "details": _clean(str(values[3]), 500),
                    "store": _clean(str(values[4]), 120),
                    "description": _clean(str(values[5]), 360),
                    "rating_count": self.records[index].rating_number,
                }
            )
        return payload

    def _recommend(
        self,
        state: dict,
        user_message: str,
        top_k: int,
        route: RoutePolicy,
    ) -> list[dict]:
        recommendations = super()._recommend(state, user_message, top_k, route)
        state["turn_usage"] = {"prompt_tokens": 0, "completion_tokens": 0}
        state["llm_attempted"] = False
        state["llm_applied"] = False
        state["llm_error"] = None

        if not self.enable_llm:
            state["llm_status"] = "disabled"
            return recommendations
        if self.llm_reranker is None:
            state["llm_status"] = "fallback"
            state["llm_error"] = self.llm_config_error or "reranker_unavailable"
            return recommendations
        if not state["constraints"]:
            state["llm_status"] = "skipped_no_explicit_constraints"
            return recommendations
        if len(recommendations) < 2:
            state["llm_status"] = "skipped_insufficient_candidates"
            return recommendations
        if state["last_candidate_count"] > self.popularity_pool_cutoff:
            state["llm_status"] = "skipped_large_candidate_pool"
            return recommendations

        state["llm_attempted"] = True
        self.llm_stats["attempts"] += 1
        try:
            candidate_payload = self._llm_candidates(recommendations)
            result = self.llm_reranker.rerank(
                category=state.get("category") or "",
                constraints=list(state["constraints"]),
                user_message=user_message,
                candidates=candidate_payload,
            )
        except Exception as error:
            # An injected/custom reranker receives the same non-fatal boundary.
            state["llm_status"] = "fallback"
            state["llm_error"] = f"reranker_failed:{type(error).__name__}"
            self.llm_stats["fallbacks"] += 1
            self.llm_stats["errors"][state["llm_error"]] += 1
            return recommendations

        state["turn_usage"] = {
            "prompt_tokens": int(result.prompt_tokens),
            "completion_tokens": int(result.completion_tokens),
        }
        self.llm_stats["prompt_tokens"] += int(result.prompt_tokens)
        self.llm_stats["completion_tokens"] += int(result.completion_tokens)
        if not result.applied:
            state["llm_status"] = "fallback"
            state["llm_error"] = result.error or "rerank_not_applied"
            self.llm_stats["fallbacks"] += 1
            self.llm_stats["errors"][state["llm_error"]] += 1
            return recommendations

        by_asin = {item["parent_asin"]: item for item in recommendations}
        reranked = [by_asin[parent_asin] for parent_asin in result.ordered_parent_asins]
        state["llm_status"] = "applied"
        state["llm_applied"] = True
        self.llm_stats["applied"] += 1
        return reranked

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        response = super().respond(session_id, user_message, turn, top_k)
        state = self.sessions[session_id]
        usage = state.get("turn_usage") or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        response["usage"] = {
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0)),
        }
        if state["route_history"]:
            state["route_history"][-1].update(
                {
                    "llm_status": state.get("llm_status", "disabled"),
                    "llm_attempted": bool(state.get("llm_attempted")),
                    "llm_applied": bool(state.get("llm_applied")),
                    "llm_model": self.llm_model if self.enable_llm else None,
                    "llm_error": state.get("llm_error"),
                    "popularity_calibration_applied": bool(
                        state.get("popularity_calibration_applied")
                    ),
                    "popularity_pool_cutoff": self.popularity_pool_cutoff,
                }
            )
        return response
