# IntentCart Ablation Log

All competition metrics in this file come from the unmodified organizer
evaluator over the released 200-session public development set. Public labels
are never available to Agent logic. Commit `00f1e41` is the original control,
`submission-v1` is the immutable release control, and `submission-v2` is the
selected calibrated implementation. See [V2_COMPARISON.md](V2_COMPARISON.md)
for the full paired analysis.

## Acceptance policy

- Preserve valid, non-empty responses and 100% catalog-valid IDs.
- Do not trade a large scenario regression for a small aggregate gain.
- Keep exact hard-constraint evidence above all soft signals.
- Prefer deterministic, offline, reproducible behavior.
- Record Hit@10, MRR, MTTC, Efficiency, TechnicalScore, all scenario results,
  runtime, tokens, cost, and the code commit.
- Treat small public-set differences as directional, not statistically proven.
- Optional techniques close judged requirements only when their reliability and
  feasibility are defensible; they are not adopted for a diagram.

## Main experiment table

Scenario cells are `Hit@10 / MRR / MTTC`.

| # | Commit | Change | Hit@10 | MRR | MTTC | Efficiency | TechnicalScore | Buying | Browsing | Override | Boundary | Keep? |
|---:|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|
| 0 | `02f15cd` | Organizer latest-message BM25 baseline | 0.125000 | 0.068034 | 9.810000 | 0.119000 | 0.106710 | 0.237500 / 0.126508 / 8.625000 | 0.025000 / 0.004514 / 10.750000 | 0.133333 / 0.104167 / 10.066667 | 0.000000 / 0.000000 / 11.000000 | Reference |
| 1 | `00f1e41` | Ordered signatures, state, clarification, unseen coverage | 1.000000 | 0.707655 | 2.120000 | 0.888000 | 0.889896 | 1.000000 / 0.697480 / 1.600000 | 1.000000 / 0.671657 / 1.962500 | 1.000000 / 0.811667 / 3.666667 | 1.000000 / 0.765000 / 2.900000 | Control |
| 2 | `submission-v1` | Explicit routes, profile soft prior, slot metadata/truncation, conservative family coverage | 1.000000 | 0.709905 | 2.120000 | 0.888000 | 0.890571 | 1.000000 / 0.703730 / 1.612500 | 1.000000 / 0.671032 / 1.950000 | 1.000000 / 0.811667 / 3.666667 | 1.000000 / 0.765000 / 2.900000 | Release control |
| 3 | `submission-v2` | Constrained small-pool popularity calibration | 1.000000 | **0.856504** | **2.090000** | **0.891000** | **0.935151** | 1.000000 / 0.893889 / 1.550000 | 1.000000 / 0.791329 / 1.950000 | 1.000000 / 0.909444 / 3.633333 | 1.000000 / 0.920000 / 2.900000 | **Selected** |

V2 keeps Hit@10 at 1.0 for all four scenarios and improves every scenario's MRR.
Relative to v1, TechnicalScore rises by 0.044580. The paired public outcomes are
59 better, 132 equal, and 9 worse, and all five deterministic sample-ID hash
slices improve. Because the public set was fully observed, this is selection
evidence rather than a statistically proven generalization claim.

## V1 feature-off ablation

All rows use the code at `0ea7705` and differ only through its documented
constructor flags. Reproduce them with:

```bash
python3 ablate.py --disable-profile
python3 ablate.py --disable-diversity
python3 ablate.py --disable-profile --disable-diversity
python3 ablate.py
```

| Profile | Family diversity | Hit@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---|---:|---:|---:|---:|---:|
| Off | On | 1.000000 | 0.708571 | 2.120000 | 0.888000 | 0.890171 |
| On | Off | 1.000000 | 0.709488 | 2.120000 | 0.888000 | 0.890446 |
| Off | Off | 1.000000 | 0.710155 | 2.125000 | 0.887500 | 0.890547 |
| On | On | 1.000000 | 0.709905 | 2.120000 | 0.888000 | **0.890571** |

Profile and diversity interact, and their combined score delta over both-off is
only +0.000024. They remain enabled because they satisfy judged workshop
requirements with deterministic unit tests, preserve 100% scenario Hit@10, and
slightly improve turn efficiency. Exact constraints stay above both signals.

An earlier one-product-per-family worktree experiment was deliberately not
accepted or quoted as a release metric because it was too aggressive for
Browsing ranking and was not committed. The selected cap permits two products
per family and only runs before explicit constraints are known.

## V2 calibration ablation

V2 moves catalog popularity ahead of weaker lexical/profile tie-breaks only
after explicit customer evidence exists. Exact positional matches, any-position
constraint matches, and category equality remain protected.

| Candidate-pool rule | MRR | MTTC | TechnicalScore | Decision |
|---|---:|---:|---:|---|
| Disabled (v1 order) | 0.709905 | 2.120000 | 0.890571 | Control |
| Every constrained pool | — | — | 0.924915 | Rejected; one hash slice regressed |
| Pool ≤20 | 0.856504 | 2.090000 | **0.935151** | **Selected** |
| Pool ≤50 | 0.853629 | 2.005000 | 0.935989 | Rejected as more aggressive public tuning |
| Soft popularity bucket | — | — | 0.930674 | Rejected; lower score |

Although the ≤50 aggregate is 0.000838 higher, ≤20 is twice the output capacity,
applies to fewer pools, and has a stronger worst-slice improvement. The choice
therefore favors a conservative interpretable boundary over the maximum public
decimal.

## Catalog investigations

### Field coverage

| Field | Coverage |
|---|---:|
| `parent_asin`, `categories`, ratings | 100% |
| title | 99.996% |
| store | 99.372% |
| details | 96.660% |
| features | 89.562% |
| description | 52.226% |
| price | 21.054% |

Decision: use title/category/features/details/store in retrieval, tolerate every
optional field, and never apply price as an unconditional filter.

### Popularity

Median `rating_number` is 12 over the catalog and 6,846 over public targets.
Mean `log1p(rating_number)` is 2.9556 versus 8.2892.

Decision: retain popularity only after intent evidence. Multiplying raw
relevance by raw popularity is rejected because the distribution is too skewed.

### Supplied profiles

All 200 public sessions use the same purchase-frequency band. Tags are generic:
fit, material, comfort, style, durability, performance, warmth, and weather.

Decision: use normalized tags/summary as a weak discovery prior only. Profile
evidence cannot filter, is session-isolated, and is dynamically dropped after
two explicit constraints. This is supplied-profile conditioning, not persistent
memory or learned identity.

## Optional semantic/vector decisions

| Idea | Decision | Evidence-based reason |
|---|---|---|
| External embedding API | Not adopted | Requires a reproducible 50k vector asset, numeric search dependency, generation/query cost, credential handling, and network fallback without a measured need |
| Local transformer embeddings | Not adopted | No licensed model/artifact or generation path is bundled; adding a large download would break the standard-library fresh-clone story |
| Custom hashed “dense” vectors | Rejected as a claim | Lexical hashing would not honestly constitute neural semantic retrieval and should not be mislabeled |
| LLM attribute/ranking API | Tested, not adopted | Final bounded trial: zero score delta, 25,020/953 tokens, US$0.006195, and two safe fallbacks over 11 calls |
| Specific-attribute entropy policy | Deferred | `other` reveals up to two constraint classes under the official simulator and v2 yields MTTC 2.09 |
| Full UI/dashboard | Out of scope | The official deliverable is headless; `demo.py` and `DEMO_OUTPUT.md` provide end-to-end inference evidence |

The selected default never calls an LLM, embedding provider, or vector service.
Its runtime model/API is none, prompt/completion tokens are 0/0, estimated model
cost is US$0, and no API key is required. The opt-in comparison path is isolated,
strictly validated, and fallback-safe. BM25 is not called dense retrieval.

## Selected-build verification

The exact implementation commit is the object resolved by `submission-v2`.

- Participant tests: 30/30.
- Organizer tests: 3/3.
- Total: 33/33.
- Official evaluator: 200/200 sessions, zero Agent exceptions.
- Two complete outputs are byte-identical.
- Result JSON SHA-256:
  `7b553ce517e7c3122a9df21261703027b07e03b0321c1720b60969173065d31e`.
- Catalog SHA-256:
  `da979b05a68af864cb0dcf9ee6a81c010c7e66a57978ad286c7a2e005fc69a67`.
- Archive SHA-256:
  `07fd142631fd6b03e2b4d09988c3eb7d53720e9d57010c79db48eeaada50a8f8`.
- Python 3.12.5; SQLite 3.45.3 FTS5; macOS 26.5.2 arm64;
  Apple M2 Pro MacBook Pro; 16 GB.
- Catalog load: 0.439159 seconds.
- Agent initialization: 4.936199 seconds.
- Evaluation after setup: 8.873643 seconds.
- Cold evaluator wall time: 14.49 seconds.
- Mean/median/P95/max response latency:
  21.130810 / 18.925041 / 40.664084 / 66.037625 ms over 418 calls.
- Maximum resident memory: 438,288,384 bytes (418.0 MiB).
- External network/model/tokens/cost: none / none / 0 / US$0.

## Generalization boundary

No untouched 40-session holdout was preserved before the team evaluated the
whole public set. No holdout or private performance is claimed. The separate 800
final sessions are the real generalization test, and only the immutable
`submission-v2` release may be run after those labels are released.

## Freeze procedure

```bash
git diff --check
python3 -m unittest discover -s tests -v
python3 -m evaluator.local_evaluator --output /tmp/intentcart-results.json
python3 demo.py
shasum -a 256 /tmp/intentcart-results.json data/catalog.jsonl catalog.jsonl.gz
git tag -a submission-v2 -m "IntentCart TechJam 2026 submission v2"
git push origin main --tags
```

The exact frozen commit is the object referenced by `submission-v2`, avoiding an
impossible self-referential hash inside the commit that records this document.
