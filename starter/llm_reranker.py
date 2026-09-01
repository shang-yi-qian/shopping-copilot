"""Validated OpenAI Responses API reranker used by the v2 candidate.

The reranker is deliberately isolated from retrieval. It receives only catalog-
valid candidates, may only return those identifiers, and falls back to the input
order on every failure. The API key is accepted in memory and is never logged.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable


DEFAULT_MODEL = "gpt-5.4-nano-2026-03-17"
RESPONSES_URL = "https://api.openai.com/v1/responses"


@dataclass(frozen=True, slots=True)
class RerankResult:
    ordered_parent_asins: tuple[str, ...]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    applied: bool = False
    error: str | None = None


class OpenAIResponsesReranker:
    """Rerank a fixed candidate set with strict output validation."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = 12.0,
        requester: Callable[[dict, float], dict] | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self.model = model.strip() or DEFAULT_MODEL
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._requester = requester or self._post_json

    @staticmethod
    def _output_text(payload: dict) -> str | None:
        for item in payload.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    text = content.get("text")
                    if isinstance(text, str):
                        return text
        return None

    @staticmethod
    def _usage(payload: dict) -> tuple[int, int]:
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if not isinstance(usage, dict):
            return 0, 0
        prompt = usage.get("input_tokens", 0)
        completion = usage.get("output_tokens", 0)
        return (
            prompt if isinstance(prompt, int) and prompt >= 0 else 0,
            completion if isinstance(completion, int) and completion >= 0 else 0,
        )

    def _post_json(self, payload: dict, timeout_seconds: float) -> dict:
        if not self._api_key:
            raise ValueError("missing_api_key")
        request = urllib.request.Request(
            RESPONSES_URL,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read()
        decoded = json.loads(body)
        if not isinstance(decoded, dict):
            raise ValueError("response_not_object")
        return decoded

    def rerank(
        self,
        *,
        category: str,
        constraints: list[str],
        user_message: str,
        candidates: list[dict],
    ) -> RerankResult:
        deterministic = tuple(
            str(candidate.get("parent_asin", "")).strip() for candidate in candidates
        )
        if len(deterministic) < 2 or any(not value for value in deterministic):
            return RerankResult(deterministic, error="insufficient_candidates")
        if len(set(deterministic)) != len(deterministic):
            return RerankResult(deterministic, error="duplicate_candidates")

        schema = {
            "type": "object",
            "properties": {
                "ordered_parent_asins": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(deterministic)},
                    "minItems": len(deterministic),
                    "maxItems": len(deterministic),
                }
            },
            "required": ["ordered_parent_asins"],
            "additionalProperties": False,
        }
        model_input = {
            "current_intent": {
                "category": category,
                "explicit_constraints": constraints,
                "latest_message": user_message,
            },
            "candidates_in_deterministic_order": candidates,
        }
        request_payload = {
            "model": self.model,
            "store": False,
            "reasoning": {"effort": "none"},
            "instructions": (
                "Rerank the supplied shopping candidates for the current intent. "
                "Use only supplied catalog evidence. Explicit constraints outrank "
                "soft relevance; rating_count is a final tie-breaker. Return every "
                "supplied parent_asin exactly once and invent nothing."
            ),
            "input": json.dumps(model_input, ensure_ascii=True, separators=(",", ":")),
            "text": {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "intentcart_candidate_ranking",
                    "strict": True,
                    "schema": schema,
                },
            },
            "max_output_tokens": 300,
        }

        try:
            response = self._requester(request_payload, self.timeout_seconds)
            if not isinstance(response, dict) or response.get("status") != "completed":
                return RerankResult(deterministic, error="response_not_completed")
            prompt_tokens, completion_tokens = self._usage(response)
            output_text = self._output_text(response)
            if output_text is None:
                return RerankResult(
                    deterministic,
                    prompt_tokens,
                    completion_tokens,
                    error="missing_output_text",
                )
            parsed = json.loads(output_text)
            ordered = parsed.get("ordered_parent_asins") if isinstance(parsed, dict) else None
            if (
                not isinstance(ordered, list)
                or any(not isinstance(value, str) for value in ordered)
                or len(ordered) != len(deterministic)
                or len(set(ordered)) != len(ordered)
                or set(ordered) != set(deterministic)
            ):
                return RerankResult(
                    deterministic,
                    prompt_tokens,
                    completion_tokens,
                    error="invalid_candidate_permutation",
                )
            return RerankResult(
                tuple(ordered), prompt_tokens, completion_tokens, applied=True
            )
        except (
            json.JSONDecodeError,
            OSError,
            TimeoutError,
            ValueError,
            socket.timeout,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ) as error:
            # Keep diagnostics non-sensitive: never include response bodies or keys.
            return RerankResult(
                deterministic,
                error=f"request_failed:{type(error).__name__}",
            )
