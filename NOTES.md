# IntentCart Engineering Notes

This file records evaluator facts, catalog evidence, and design decisions for the
TechJam 2026 Track 4 submission. It exists so every non-obvious choice can be
traced to the official contract or a measured property of the released data.

## Sources of truth

In descending order of authority:

1. `docs/final_evaluation_faq.md`
2. `docs/competition_specification.md`
3. `docs/agent_api_contract.json`
4. `docs/evaluation_config.json`
5. `evaluator/local_evaluator.py`
6. `data/public_set.jsonl` for public development measurement only
7. `CLAUDE.md` for the agreed team execution plan

The Agent must not import or inspect public ground truth, mutate the catalog, or
change the official evaluator when producing reported results.

## Evaluator recon answers

### Q1 — What does `ask_attribute` reveal?

The simulator uses the structured `ask_attribute`; it does not infer the
requested attribute from the natural-language `message`.

- A specific attribute returns an undisclosed target constraint only if
  `classify_constraint()` assigns that constraint to the requested attribute.
- If no matching undisclosed constraint exists, the reply says that there is no
  additional preference for that attribute.
- `ask_attribute="other"` accepts every constraint class and returns up to two
  undisclosed target constraints.
- A Boundary session deliberately returns one no-preference response for the
  first non-null question, then follows the normal policy.

Decision: use a natural multi-facet question with `ask_attribute="other"` while
the candidate pool remains ambiguous. Continue returning recommendations on the
same turn.

### Q2 — Does asking consume a turn?

Yes. The evaluator increments the turn for every `respond()` call, regardless of
whether the response asks a question.

Decision: asking is not free, but asking and recommending together is permitted.
Every turn should return up to ten recommendations.

### Q3 — What happens to repeated ASINs?

`normalize_recommendations()` removes invalid and repeated ASINs within the
current response. It does not deduplicate across turns and applies no explicit
cross-turn penalty.

If a previous response did not stop the session, none of its valid recommended
ASINs was the target. Decision: retain a per-session `seen_asins` set and use
later turns for unseen coverage.

### Q4 — How is MRR calculated?

MRR uses the target's rank inside the successful turn's recommendation list. It
is not calculated over a global cross-turn list. Later hits are penalized by
MTTC, not by an additional MRR turn discount.

Decision: preserve strongest-first ordering on each turn while using remaining
positions for unseen candidates.

## Other evaluator facts

- Maximum turns: 10.
- Scored list: first 10 valid unique catalog ASINs in each response.
- A score field on recommendations is accepted but ignored.
- A session stops on the first valid target hit.
- Intent Override cannot score before the replacement intent is revealed.
- Final customer messages use the same deterministic templates and response
  policy as the public evaluator; no hidden paraphrase set is introduced.
- Final evaluation is run against the frozen submitted commit with the official
  evaluator unchanged.
- Sessions execute sequentially. Immutable indexes may be shared, but mutable
  conversation state must be isolated.
- LLM use is optional. Offline preprocessing and catalog-derived indexes are
  explicitly allowed.

## Catalog audit

Audit command: a read-only Python scan of `data/catalog.jsonl` on 2026-09-01.

| Field | Non-empty rows | Coverage | Retrieval decision |
|---|---:|---:|---|
| `parent_asin` | 50,000 | 100.000% | Scored identifier |
| `categories` | 50,000 | 100.000% | Safe category route |
| `average_rating` | 50,000 | 100.000% | Late tie-break only |
| `rating_number` | 50,000 | 100.000% | Popularity tie-break only |
| `title` | 49,998 | 99.996% | Strong lexical field |
| `store` | 49,686 | 99.372% | Soft lexical/brand signal |
| `details` | 48,330 | 96.660% | Signature and lexical signal |
| `features` | 44,781 | 89.562% | Signature and lexical signal |
| `description` | 26,113 | 52.226% | Soft lexical signal; never required |
| `price` | 10,527 | 21.054% | Never unconditional hard filter |

Catalog invariants observed:

- 50,000 rows.
- 50,000 unique `parent_asin` values.
- All 200 public target ASINs are present in the catalog.

Decision: exact constraints may safely narrow only when their reverse-index
intersection remains non-empty. Sparse fields such as price and description are
ranking signals, not unconditional filters.

## Public-set audit

| Dimension | Count |
|---|---:|
| Buying | 80 |
| Browsing | 80 |
| Intent Override | 30 |
| Boundary | 10 |
| Easy | 80 |
| Medium | 90 |
| Hard | 30 |

All 200 public profiles report `3-4 prior purchases`, so that field has no
discriminative power on the public set. Profile tag frequencies are:

| Tag | Sessions |
|---|---:|
| fit | 163 |
| material | 154 |
| comfort | 144 |
| style | 101 |
| durability | 47 |
| performance | 26 |
| warmth | 18 |
| weather | 12 |
| general shopping | 1 |

Decision: profile tags may prioritize soft signals or future clarification, but
they must not override explicit current-session constraints.

## Popularity-prior validation

`rating_number` is strongly skewed toward the 200 public targets:

| Population | Mean | Median | P75 | P90 | Mean `log1p` |
|---|---:|---:|---:|---:|---:|
| Entire 50,000-item catalog | 241.409 | 12 | 59 | 260 | 2.9556 |
| 200 public targets | 16,179.415 | 6,846 | 18,076 | 40,492 | 8.2892 |

This is strong evidence that the leave-last-out target sampling favors popular
products. However, multiplying relevance by raw popularity would let irrelevant
popular products dominate. Decision: use `rating_number` only after exact
signature, category, and lexical relevance signals in a deterministic sorting
tuple. Re-run this comparison only if the official catalog changes.

## Architecture decisions

### Kept for the three-hour sprint

- Catalog-derived ordered intent signatures.
- Exact category and constraint reverse indexes.
- Existing SQLite FTS5 BM25 index as recall fallback.
- Per-session state with selective override erasure.
- `ask_attribute="other"` plus recommendations on ambiguous turns.
- Cross-turn unseen-ASIN coverage.
- Popularity as final tie-breaker.
- Standard-library-only runtime and zero model cost.

### Deferred

- External embedding APIs.
- Local transformer dependencies.
- Dense vector artifacts.
- LLM-based structured attribute extraction.
- Entropy optimization over specific attributes.
- UI or web service.
- Upstream review/co-purchase data.

These are deferred because they cannot be implemented, evaluated, documented,
and reproduced safely inside the current sprint. They remain future work, not
features to claim in the frozen submission.

## Independently verified Agent release candidate

Person A handed off commit
`00f1e413f4e984cc9dbfd7e44f2e8c7a051b566f` from
`origin/feat/agent`. Person B fetched and evaluated that exact commit in a clean
archive at 2026-09-01 in Asia/Singapore. The current delivery test file was copied
in for contract/state testing; `starter/agent.py` was neither edited nor patched.

### Change-scope audit

- Parent: `02f15cd6ba969c831169bc84f146098bad0c1a2f`.
- RC changes only `starter/agent.py` (`+372/-25`).
- `git diff --check 02f15cd..00f1e41` is clean.
- The evaluator, public set, catalog archive, checksum, and contract documents
  are unchanged by the RC.
- Static review found no public-set or ground-truth path, public target ASIN,
  HTTP client, external model SDK, or network endpoint in the Agent.
- Catalog archive SHA-256 remains
  `07fd142631fd6b03e2b4d09988c3eb7d53720e9d57010c79db48eeaada50a8f8`.
- Independent initialization indexed exactly 50,000 records and 50,000 unique
  `parent_asin` values with zero pre-existing sessions.

### Runtime requirement discovered during independent validation

The RC uses `@dataclass(frozen=True, slots=True)`, so Python 3.10 or later is an
actual minimum requirement rather than merely a recommendation. The machine's
Apple system Python 3.9.6 fails at import with:

```text
TypeError: dataclass() got an unexpected keyword argument 'slots'
```

No Agent change is warranted. The release documentation and `requirements.txt`
must state Python >=3.10, and the frozen evaluation environment must record the
exact interpreter. Independent validation used Homebrew Python 3.12.14 with
SQLite 3.53.4 and FTS5 available.

### Test result

Command, executed against the exact RC plus Person B's contract/state test file:

```bash
/opt/homebrew/bin/python3.12 -m unittest discover -s tests -v
```

Result after the updated workshop-coverage plan: **24/24 passed** in 0.053
seconds on the miniature fixture.

- Organizer evaluator tests: 3/3.
- Participant Agent tests: 21/21.
- Covered release gates: required reset, response schema, valid/unique ASINs,
  cross-turn dedupe, override eligibility-epoch reset, session isolation,
  Buying/Browsing mode detection, ordered accumulation, duplicate-constraint
  suppression, selective override, Boundary behavior, overload clarification
  cutoff, empty-query fallback, missing metadata, missing-catalog failure, safe
  profile copying, failed intersection, deterministic ranking, top-k handling,
  and no unnecessary turn-10 question.

### Official public evaluator reproduction

Command:

```bash
/usr/bin/time -p /opt/homebrew/bin/python3.12 \
  -m evaluator.local_evaluator \
  --output /tmp/intentcart-rc-00f1e41-results.json
```

Results on the 200-session public development set:

| Metric | Independently reproduced value |
|---|---:|
| Hit@10 | 1.000000 |
| MRR | 0.707655 |
| MTTC | 2.120000 |
| Efficiency | 0.888000 |
| TechnicalScore | 0.889896 |
| Prompt tokens | 0 |
| Completion tokens | 0 |
| Exceptions | 0 |

Scenario metrics (`Hit@10 / MRR / MTTC`):

| Scenario | Result |
|---|---|
| Buying | 1.000000 / 0.697480 / 1.600000 |
| Browsing | 1.000000 / 0.671657 / 1.962500 |
| Intent Override | 1.000000 / 0.811667 / 3.666667 |
| Boundary | 1.000000 / 0.765000 / 2.900000 |

The partner handoff reported approximately 13 seconds. Person B's cold,
end-to-end `/usr/bin/time` run on an Apple M4 MacBook Pro with 16 GB memory and
macOS 15.5 measured 20.85 seconds (`user 20.45`, `sys 0.25`). Both are retained;
public documentation uses the independently reproduced 20.85-second value and
labels hardware/environment.

A separate timing wrapper around the exact Agent reported:

| Measurement | Value |
|---|---:|
| Catalog load performed by evaluator | 0.673218 s |
| Agent initialization | 7.785188 s |
| Evaluation after setup | 12.309894 s |
| `respond()` calls | 424 |
| Mean response latency | 28.880855 ms |
| Median response latency | 26.362625 ms |
| P95 response latency | 62.312896 ms |
| Maximum response latency | 110.095083 ms |

Saved public-result file SHA-256:
`980e6288c63e2222cd85d21051df1a7238bacdbe294bdf5033a2059678a58829`.
This ignored evidence file remains local and must be copied into the team's
submission-evidence folder after integration.

Two additional full runs produced byte-identical JSON with the same SHA-256 and
the same aggregate and scenario metrics. The third run measured 21.56 seconds
and a maximum resident set size of 416,415,744 bytes (397.125 MiB). This remains
inside the plan's practical target of 500 MB. The repeated output is direct
determinism evidence for the selected RC.

### Demo traces selected from the verified result

- Browsing: `public_0007`, target rank 1 on turn 2.
- Intent Override: `public_0003`, target rank 1 on turn 3 after the official
  override gate. The target appears on turn 2 but is not score-eligible until the
  replacement intent arrives; the Agent correctly clears pre-override seen-ASIN
  evidence and reranks it on turn 3.

## Updated workshop-coverage audit

The requirements matrix in `CLAUDE.md` Section 11 was re-read in full after RC1
selection. The frozen sprint candidate intentionally remains the measured
offline implementation; W1–W5 are experiment tracks, not authorization to add
unmeasured components after the score gate.

| Requirement area | Release evidence | Honest boundary/deferred action |
|---|---|---|
| Buying/Browsing detection | Direct mode/category/constraint tests | Mode is explicit, but no claim of dense semantic routing |
| Buying precision | Exact-signature priority and failed-intersection tests | Exact constraint intersections are conditional on non-empty recall |
| Browsing recall | Category plus accumulated-context BM25 and trace `public_0007` | No dense vector or explicit family-diversity route |
| Multi-turn state | Accumulation, duplicate suppression, isolation tests | No persistent identity or cross-session memory |
| Override | Selective erasure test and eligibility-epoch test/trace | Pre-override results are not treated as scored misses |
| Boundary | Neutral no-preference fallback test | No negative slot is fabricated |
| Overload clarification | Ambiguous pool asks `other`; narrowed pool stops asking | No entropy model or tuned information-gain threshold |
| Supplied profile | Input is copied into isolated state and never logged | RC does **not** use profile fields for ranking; personalization is future work |
| Dynamic orchestration | Mode, candidate count, state, and remaining turn drive behavior | Policies share core ranking; no confidence/slot-decay layer |
| Vector retrieval | Not shipped | W3 remains future measured work; BM25 is never called dense retrieval |
| LLM semantic ranking | Not shipped | External runtime API/model/tokens/cost are none/none/0/$0 |
| Reproducibility | Three byte-identical official runs, checksum, Python/SQLite record | Integrated main must still be rerun and tied to its final hash |
| Generalization | Catalog-only Agent, no target imports/hardcoding | No untouched 40-session holdout was preserved before full-public iteration; this is disclosed and no private result is claimed |
| Security | `.env` ignored; Agent has no network/model client | Full Git-history secret scan remains a freeze gate |
| External delivery | README/Devpost/demo sources prepared | Names, final hash/tag, public repo/video/Devpost URLs require team/external action |

No optional vector, LLM, profile-ranking, family-diversity, or slot-decay feature
is described as implemented in the public artifacts.

## Resource-link verification

Read-only checks on 2026-09-01 returned HTTP 200 for:

- `https://github.com/TechJam2026/techjam-conversational-search`
- `https://github.com/TechJam2026/techjam-conversational-search/releases/tag/participant-kit`
- `https://amazon-reviews-2023.github.io/`

The organizer has not supplied a public/shareable URL for the 28 Aug webinar
recording in the repository. Do not upload, redistribute, or invent a link for
that recording.

## Measurement rules

- Every metric row in `ABLATION.md` must include a commit hash.
- Only the unmodified official evaluator may produce reported final metrics.
- Do not claim private-set performance before the private package is released.
- Prefer a measured simpler commit to an unmeasured later commit.
- Record per-scenario metrics, not only the overall composite.
- Record runtime, Python version, model/token usage, and estimated model cost.
- Public labels are used only by the evaluator, never by Agent logic.

## Open items before freeze

- [x] Select Agent RC `00f1e41`.
- [x] Record independently reproduced RC public metrics and runtime.
- [x] Confirm Agent uses no public labels or hardcoded public ASINs.
- [x] Confirm official and participant tests pass against the RC.
- [x] Confirm documented test/evaluator commands reproduce the RC result under
      Python 3.12.14.
- [x] Confirm `.env`, decompressed catalog, and results output are ignored.
- [x] Run the non-printing Git-history API-key-pattern scan; no match found.
- [x] Measure peak memory and repeated-run determinism.
- [x] Verify the three organizer/upstream resource URLs return HTTP 200.
- [ ] Cherry-pick the delivery commit onto `00f1e41` and rerun the freeze gate.
- [ ] Insert the final integrated commit hash and tag.
- [ ] Replace contribution placeholders with team member names.
- [ ] Record public repository, video, and Devpost URLs.
