# IntentCart Engineering Notes

This file records evaluator facts, catalog audits, implementation decisions, and
release evidence for the complete Person A + Person B integration. Historical
planning remains in `CLAUDE.md`; this file describes the selected build.

`submission-v1` remains the immutable release control. `submission-v2` is the
selected build after the paired analysis in [V2_COMPARISON.md](V2_COMPARISON.md).

## Source precedence

When sources differ, the project follows:

1. `docs/submission_rules.md`, `docs/competition_specification.md`,
   `docs/final_evaluation_faq.md`, and `docs/agent_api_contract.json`;
2. the unmodified official evaluator and evaluation config;
3. the published workshop brief; and
4. the local implementation plan.

The official policy allows keyword, dense, hybrid, local-model, external-API,
and non-LLM solutions. Dense retrieval and LLM use are optional, so omitted
components are disclosed rather than simulated or misrepresented.

## Evaluator facts that drive the design

- Exactly 200 public sessions are evaluated sequentially; the private/final set
  contains 800 separate sessions released after the Devpost deadline.
- Maximum turns are 10 and scored recommendations are the first 10 unique,
  catalog-valid parent ASINs.
- A question and recommendations may coexist in one response.
- `ask_attribute="other"` can disclose up to two remaining constraints across
  classes; a specific attribute returns only matching undisclosed constraints.
- A Boundary session returns one neutral no-preference answer when the Agent
  asks; neutral evidence must not become a negative slot.
- Intent Override cannot score before its explicit replacement turn.
- MRR is the target rank within the successful turn, not a cross-turn rank.
- The evaluator does not deduplicate recommendations across turns. If a normal
  eligible response misses, those ASINs are useful negative evidence.
- Pre-override recommendations are not scored evidence. The Agent starts a new
  eligibility epoch so an early product can be considered after the new intent.
- Unknown natural-language paraphrases are not part of the published final
  simulator policy, though fallback behavior is still required.
- Public `scenario_type`, target ASIN, and generated intent cards remain inside
  the evaluator and are never passed to `Agent`.

## Catalog audit

The Agent reads the organizer's immutable 50,000-row
`Clothing_Shoes_and_Jewelry` catalog.

| Property | Result |
|---|---:|
| Rows | 50,000 |
| Unique parent ASINs | 50,000 |
| Normalized coarse categories | 1,115 |
| Unique catalog signatures | 40,732 |
| Singleton signatures | 38,653 |
| Largest signature collision | 442 |

Field coverage:

| Field | Coverage |
|---|---:|
| parent ASIN, categories, ratings | 100% |
| title | 99.996% |
| store | 99.372% |
| details | 96.660% |
| features | 89.562% |
| description | 52.226% |
| price | 21.054% |

Archive SHA-256:
`07fd142631fd6b03e2b4d09988c3eb7d53720e9d57010c79db48eeaada50a8f8`.

Decompressed catalog SHA-256:
`da979b05a68af864cb0dcf9ee6a81c010c7e66a57978ad286c7a2e005fc69a67`.

Design consequences:

- Optional metadata never gates initialization.
- Price is an optional signature value, never an unconditional hard filter.
- Popularity is a late tie-break because raw `rating_number` is highly skewed.
- Category, signatures, BM25, and deterministic fallback are all needed.
- Signature collisions explain why clarification, lexical ordering, popularity,
  and unseen coverage still matter after exact matching.

## Selected v2 Agent architecture

### Immutable indexes

Initialization creates:

- a compact `ProductRecord` for every catalog row;
- normalized category and signature reverse indexes;
- deterministic category/global popularity orders; and
- an in-memory SQLite FTS5 index over title, category, features, details, store,
  and description.

No index contains public labels, targets, intent cards, scenario types, or user
identifiers. The catalog is read but never modified.

### Session state

Each `reset()` deep-copies the documented aggregate profile and creates an
isolated state containing:

```text
profile + normalized profile_context + profile slot metadata
mode + active route + route history
category + ordered constraints + constraint metadata
explicitly stale old_preference + override_applied + eligibility_epoch
seen_asins + asked_attributes + last_candidate_count
active/truncated profile indicators
```

Constraint metadata contains value, source, confidence, introduction turn, last
confirmation turn, and a hard/soft marker. Customer-stated constraints are hard
and never decay. Supplied profile terms have confidence 0.25 and are dynamically
truncated after two explicit session constraints.

### Orchestrator

Observed message/state—not hidden scenario labels—selects:

| Route | Trigger | Retrieval/policy behavior |
|---|---|---|
| `precision` | Buying plus hard evidence | 300-item BM25 recall, exact intersections, no diversity/profile override |
| `discovery` | Browsing | 400-item lexical recall, weak profile prior, conservative family coverage |
| `override_recovery` | Explicit replacement applied | Recomputed pool and new eligibility epoch |
| `fallback` | Incomplete/unknown/pre-override | Lexical/category/global safety path |

The route trace stores only turn, route, candidate count, question decision, and
whether profile terms participated. It is inspectable in tests and `demo.py` but
not printed by production inference.

### Ranking and coverage

V1 rank tuples prioritize:

1. positional constraint matches;
2. matches anywhere in the catalog signature;
3. category equality;
4. route-appropriate lexical/profile ranks;
5. popularity; and
6. parent ASIN for deterministic ties.

V2 preserves items 1–3. After explicit customer constraints narrow the
candidate pool to at most 20 products, it moves catalog `rating_number` ahead of
the weaker lexical/profile ranks. Broad or unconstrained pools retain v1 order.
This calibration uses no targets, intent cards, or scenario labels.

Profile evidence remains soft because all explicit signature evidence and
category equality outrank it; after enough current evidence it is removed.
Unconstrained discovery covers at most two products per store/title family
before backfill. Constrained retrieval never applies diversity.

Shown ASINs are excluded for the current eligibility epoch. Exact, BM25,
category, and global lists are merged with stable deduplication, yielding up to
`min(top_k, 10)` catalog-valid unique recommendations.

### Clarification and failures

The Agent asks `other` when the pool exceeds the output capacity, the question
can still be used, and uncertainty remains. It always recommends on the same
turn. Turn 10 never asks.

On an exact miss, candidate narrowing retains the last non-empty pool. FTS errors
return an empty lexical route and fall through to category/global order. Missing
metadata is safe. A response-level exception still attempts a catalog-valid,
unseen global fallback.

## Supplied-profile audit

Public aggregate fields are `purchase_frequency`, `average_prior_rating`,
`rating_style`, `preference_tags`, and `summary`. All 200 public profiles use the
same purchase-frequency band. Common tags are fit, material, comfort, style,
durability, performance, warmth, and weather.

The implementation:

- reads only `preference_tags` and `summary` for ranking context;
- deep-copies input and never mutates the caller's nested list;
- stores no state across sessions;
- never logs profile values in normal output;
- never infers identity, demographics, or private history;
- never uses profile evidence as a hard filter; and
- truncates the prior as explicit session evidence accumulates.

This is accurately called supplied-profile conditioning, not learned long-term
memory.

## Public metrics and ablations

Control `00f1e413f4e984cc9dbfd7e44f2e8c7a051b566f`:

```text
Hit@10          1.000000
MRR             0.707655
MTTC            2.120000
Efficiency      0.888000
TechnicalScore  0.889896
```

Enhanced Agent `0ea7705f9e295855cadc85bd44141b841ff0685f`:

```text
Hit@10          1.000000
MRR             0.709905
MTTC            2.120000
Efficiency      0.888000
TechnicalScore  0.890571
```

Selected v2 (`submission-v2`):

```text
Hit@10          1.000000
MRR             0.856504
MTTC            2.090000
Efficiency      0.891000
TechnicalScore  0.935151
```

Scenario metrics (`Hit@10 / MRR / MTTC`):

| Scenario | Control | Enhanced |
|---|---|---|
| Buying | 1.000000 / 0.697480 / 1.600000 | 1.000000 / 0.703730 / 1.612500 |
| Browsing | 1.000000 / 0.671657 / 1.962500 | 1.000000 / 0.671032 / 1.950000 |
| Intent Override | 1.000000 / 0.811667 / 3.666667 | 1.000000 / 0.811667 / 3.666667 |
| Boundary | 1.000000 / 0.765000 / 2.900000 | 1.000000 / 0.765000 / 2.900000 |

V2 scenario metrics are Buying 1.000000/0.893889/1.550000, Browsing
1.000000/0.791329/1.950000, Intent Override
1.000000/0.909444/3.633333, and Boundary 1.000000/0.920000/2.900000. The
feature-off and v2 threshold tables are in `ABLATION.md`. Public differences are
not claimed as statistically proven generalization.

## Reproduction and performance record

Selected Agent: the exact commit resolved by `submission-v2`.

Environment:

```text
Apple M2 Pro MacBook Pro, 16 GB
macOS 26.5.2 arm64
Python 3.12.5
SQLite 3.45.3 with FTS5
```

Verification:

- 30/30 participant tests passed.
- 3/3 organizer tests passed.
- 200/200 evaluator sessions completed with zero Agent exceptions.
- Two full outputs were byte-identical.
- Result JSON SHA-256:
  `7b553ce517e7c3122a9df21261703027b07e03b0321c1720b60969173065d31e`.
- Catalog load: 0.439159 s.
- Agent initialization: 4.936199 s.
- Evaluation after setup: 8.873643 s.
- Cold evaluator wall time: 14.49 s.
- 418 `respond()` calls.
- Mean/median/P95/max latency:
  21.130810 / 18.925041 / 40.664084 / 66.037625 ms.
- Maximum resident memory: 438,288,384 bytes (418.0 MiB).
- Network/API/model: none.
- Prompt/completion tokens: 0/0.
- Estimated model cost: US$0.

The same control metrics were independently reproduced on the partner's Apple
M4/Python 3.12.14 environment before integration. Runtime differences are
reported as environment variance, not a performance claim.

## End-to-end demonstration

`python3 demo.py` replays `public_0007` (Browsing) and `public_0003` (Intent
Override) with the official simulator policy. It prints each customer message,
safe policy trace, ranked predictions, and scored outcome.

- Browsing: candidate pool 519 → 1; target rank 1 on turn 2.
- Override: target appears before eligibility, the epoch resets, and the target
  scores at rank 1 on turn 3.

The verified output is preserved in `DEMO_OUTPUT.md`. Ground truth is used only
by the demo/evaluator to label results after inference; the Agent never receives
it.

## Requirement-coverage audit

| Area | Final evidence | Boundary |
|---|---|---|
| Buying/Browsing | Explicit route table, route traces, direct tests | Template-aware, not a general NLU claim |
| Precision | Safe exact signature intersections and positional ranking | Sparse unknown constraints remain soft |
| Browsing recall/diversity | Category + FTS5 + profile prior + two-per-family coverage | No neural semantic embeddings |
| State | Accumulation, metadata, isolation, dynamic profile truncation | No cross-session identity/memory |
| Override | Selective erasure and eligibility epoch | Reconsideration occurs only after explicit override |
| Boundary | Neutral no-preference test | No negative preference fabricated |
| Clarification | Candidate overload plus simultaneous recommendations | Uses simulator-efficient `other`, not learned entropy |
| Personalization | Deep-copied profile prior plus off/on test and ablation | Weak supplied conditioning only |
| Orchestration | Four deterministic policies and safe traces | No learned controller |
| Vector retrieval | Not adopted under official optional-model rule | No dense claim or vector artifact |
| LLM reranking | Opt-in strict-schema comparison, not selected | Bounded trial had zero score gain; default has no model/API/tokens/cost/network |
| Metrics | Control/final overall and all scenario metrics | Public development only |
| Feasibility | CPU runtime, latency, memory, dependency/cost disclosure | Single-process evaluation, not load testing |
| Demonstration | Executable `demo.py` + captured `DEMO_OUTPUT.md` | Headless, as allowed by the brief |
| Reproducibility | Checksums, tests, evaluator command, deterministic result hash | Final clean-clone audit occurs at release freeze |
| Generalization | Catalog-only code; no target strings/imports | No untouched holdout or private claim |

## Security and provenance rules

- `.env`, decompressed catalog, result JSON, caches, and virtual environments are
  ignored.
- The selected default makes no environment-secret lookup or network call. The
  isolated opt-in reranker uses the standard-library HTTPS client and reads the
  key only when explicitly enabled by `compare_versions.py --with-llm`.
- API keys must never be committed, logged, pasted into screenshots, or shown in
  the video. Any key previously exposed outside Git should be revoked.
- Public target ASINs and `public_set` are absent from production Agent logic.
- Organizer evaluator, config, catalog archive, and public set remain unchanged.
- Dataset attribution lives in `DATA_ATTRIBUTION.md`.
- Demo assets are original terminal output/diagrams; no storefront imagery,
  logos, third-party footage, webinar content, or copyrighted music is used.

## Release actions

Internal artifacts are complete when the final freeze audit passes. Actions that
require team-owned external accounts remain:

1. publish the final branch to `main` and create `submission-v2`, preserving
   `submission-v1` as the immutable control;
2. record the demo using `DEMO_SCRIPT.md` and upload it publicly to YouTube;
3. paste `DEVPOST.md` into the Devpost entry and add the repository/video links;
4. verify repository, video, and Devpost access while signed out; and
5. retain the final submission confirmation, tag SHA, URLs, and result JSON.

After release of the 800-session package, run only `submission-v2` with the
unmodified official evaluator and retain the evidence without tuning.
