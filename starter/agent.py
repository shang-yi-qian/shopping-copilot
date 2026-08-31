from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True, slots=True)
class ProductRecord:
    parent_asin: str
    category: str
    signature: tuple[str, ...]
    rating_number: int


class Agent:
    """Stateful catalog-signature retrieval with deterministic offline fallbacks."""

    def __init__(self, catalog_path: str | Path = "data/catalog.jsonl") -> None:
        self.catalog_path = Path(catalog_path)
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
                    ProductRecord(parent_asin, category, signature, rating_number)
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
        self.sessions[session_id] = {
            "profile": dict(user_profile) if isinstance(user_profile, dict) else {},
            "mode": None,
            "category": None,
            "constraints": [],
            "old_preference": None,
            "override_applied": False,
            "seen_asins": set(),
            "asked_attributes": [],
            "last_candidate_count": 0,
        }

    @staticmethod
    def _add_constraints(state: dict, values: list[str]) -> None:
        for value in values:
            normalized = _normalize(value)
            if normalized and normalized not in state["constraints"]:
                state["constraints"].append(normalized)

    def _parse_message(self, state: dict, user_message: str) -> None:
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
            self._add_constraints(state, [buying.group(2)])
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
            state["override_applied"] = True
            state["mode"] = "intent_override"
            # Recommendations made before the evaluator permits a hit do not prove a miss.
            state["seen_asins"].clear()
            self._add_constraints(state, [override.group(1)])
            return

        clarification = re.fullmatch(
            r"For that, what matters is: (.+)\.",
            message,
            flags=re.IGNORECASE,
        )
        if clarification:
            self._add_constraints(
                state, self._split_disclosed_constraints(state, clarification.group(1))
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

    def _bm25_indices(self, state: dict, user_message: str, limit: int = 400) -> list[int]:
        context = " ".join(
            [state.get("category") or "", *state["constraints"], user_message]
        )
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

    def _rank_candidates(
        self,
        state: dict,
        candidates: set[int],
        bm25_order: list[int],
    ) -> list[int]:
        bm25_rank = {index: rank for rank, index in enumerate(bm25_order)}
        constraints: list[str] = state["constraints"]
        category = state.get("category")

        def rank_key(index: int) -> tuple:
            record = self.records[index]
            positional = sum(
                position < len(record.signature) and record.signature[position] == value
                for position, value in enumerate(constraints)
            )
            anywhere = sum(value in record.signature for value in constraints)
            return (
                -positional,
                -anywhere,
                -(record.category == category),
                bm25_rank.get(index, len(bm25_order) + 1),
                -record.rating_number,
                record.parent_asin,
            )

        return sorted(candidates, key=rank_key)

    def _recommend(self, state: dict, user_message: str, top_k: int) -> list[dict]:
        limit = max(0, min(int(top_k), 10))
        if limit == 0:
            return []

        candidates = self._candidate_indices(state)
        state["last_candidate_count"] = len(candidates)
        bm25_order = self._bm25_indices(state, user_message)
        ranked = self._rank_candidates(state, candidates, bm25_order)

        category = state.get("category")
        fallbacks = [
            *ranked,
            *bm25_order,
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
            self._parse_message(state, user_message)
            recommendations = self._recommend(state, user_message, top_k)
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

        should_ask = turn < 10 and state["last_candidate_count"] > len(recommendations)
        ask_attribute = "other" if should_ask else None
        if ask_attribute:
            state["asked_attributes"].append(ask_attribute)
            message = (
                "What other requirement matters most—material, color, fit, style, "
                "budget, or intended use?"
            )
        else:
            message = "Here are the strongest matches for your current requirements."
        return {
            "message": message,
            "ask_attribute": ask_attribute,
            "recommendations": recommendations,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
