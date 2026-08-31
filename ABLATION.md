# IntentCart Ablation Log

This log records which ideas were measured, which commit produced each result,
and why a change was kept or rejected. All reported competition metrics must come
from the unmodified official evaluator.

## Acceptance policy

- Baseline is the organizer's released BM25 Agent.
- A metric row without a commit hash is provisional and must not be quoted as a
  final result.
- Prefer changes that improve overall TechnicalScore without catastrophic
  regression in one scenario.
- With only 200 public sessions, small changes may be sampling noise. Do not add
  complexity for a marginal delta that cannot be explained.
- The frozen submission uses the highest-scoring fully measured commit that also
  passes all contract tests.

## Experiment table

Scenario cells use `Hit@10 / MRR / MTTC`.

| # | Commit | Change | Hit@10 | MRR | MTTC | Efficiency | TechnicalScore | Buying | Browsing | Override | Boundary | Runtime | Keep? |
|---:|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| 0 | Organizer baseline | Latest-message BM25, no state, no questions | 0.125000 | 0.068034 | 9.810000 | 0.119000 | 0.106710 | 0.237500 / 0.126508 / 8.625000 | 0.025000 / 0.004514 / 10.750000 | 0.133333 / 0.104167 / 10.066667 | 0.000000 / 0.000000 / 11.000000 | Record on submission machine | Reference |
| 1 | `00f1e41` | Ordered catalog signatures, session state, `other` clarification, unseen-ASIN coverage | 1.000000 | 0.707655 | 2.120000 | 0.888000 | 0.889896 | 1.000000 / 0.697480 / 1.600000 | 1.000000 / 0.671657 / 1.962500 | 1.000000 / 0.811667 / 3.666667 | 1.000000 / 0.765000 / 2.900000 | 20.85 s independently; ~13 s partner | **Keep** |
| 2 | — | No RC2: RC1 cleared the 0.75 target with 1.0 Hit@10 in every scenario; late tuning stopped | — | — | — | — | — | — | — | — | — | — | Not run |
| 3 | `PENDING_FINAL_SHA` | Frozen integrated submission | — | — | — | — | — | — | — | — | — | — | Pending |

## Supporting data experiments

### Catalog field coverage

Result: categories and rating fields have 100% coverage; title 99.996%; store
99.372%; details 96.660%; features 89.562%; description 52.226%; price 21.054%.

Decision: keep category/title/features/details/store in retrieval. Use
description and price only as optional signals. Never require a price match when
79% of products have no price.

### Popularity prior

Result: median `rating_number` is 12 across the catalog and 6,846 across the 200
public targets. Mean `log1p(rating_number)` is 2.9556 versus 8.2892.

Decision: keep popularity as a final relevance tie-breaker. Reject multiplying
raw relevance by raw `rating_number`, which could swamp exact intent evidence.

### Profile prior

Result: all 200 public sessions have the same purchase-frequency band. Tags are
generic and highly repeated.

Decision: do not use purchase frequency for ranking. Profile tags remain weak
soft context only.

## Rejected/deferred ideas

| Idea | Status | Reason |
|---|---|---|
| External text embeddings | Deferred | Dependency/API setup, preprocessing, artifact size, cost, and failure path cannot be validated inside the sprint |
| Local transformer embeddings | Deferred | NumPy, PyTorch, and SentenceTransformers are not installed; model download and 50,000-item encoding are outside the critical path |
| LLM attribute extraction | Deferred | Requires prompt cache, credentials, token accounting, latency measurement, and fallback validation |
| Specific-attribute entropy policy | Rejected for sprint | Published `other` policy reveals up to two constraints across all classes and is more direct |
| Hard price filter | Rejected | Price coverage is only 21.054% |
| Raw popularity multiplication | Rejected | Popularity distribution is extremely skewed and could outrank exact relevance |
| UI | Out of scope | Headless evaluator and traced sessions satisfy the track deliverable |

## RC1 verification record

Selected Agent commit:
`00f1e413f4e984cc9dbfd7e44f2e8c7a051b566f`.

- Commit changes only `starter/agent.py` relative to `02f15cd`.
- `git diff --check` passed.
- Organizer tests: 3/3 passed.
- Participant Agent tests: 21/21 passed.
- Official public evaluator: completed 200/200 sessions with zero exceptions.
- Token usage: 0 prompt, 0 completion.
- External model/API/network use: none.
- Environment: Python 3.12.14, SQLite 3.53.4 with FTS5, macOS 15.5,
  Apple M4, 16 GB memory.
- Independent cold wall time: 20.85 seconds.
- Agent initialization: 7.785188 seconds.
- Peak resident memory: 416,415,744 bytes (397.125 MiB).
- Mean/median/P95/max `respond()` latency:
  28.880855 / 26.362625 / 62.312896 / 110.095083 ms across 424 calls.
- Ignored result JSON SHA-256:
  `980e6288c63e2222cd85d21051df1a7238bacdbe294bdf5033a2059678a58829`.

The partner's handoff reported the same metrics and approximately 13 seconds.
Two additional Person B full runs reproduced byte-identical result JSON and the
same SHA-256. Runtime across the measured Person B runs was 20.85–21.56 seconds.
The difference from the handoff is treated as environment/cache variance; no
faster runtime is claimed in public documentation. RC1 exceeds the plan's 0.85
TechnicalScore target, 0.97 Hit@10 target, and MTTC <=2.5 target with no
scenario-specific Hit@10 regression, so W1–W5 and other late feature work are
stopped. Only delivery integration and freeze verification remain.

No untouched 40-session holdout was preserved before the team had evaluated the
full public set. This is a process limitation, so no holdout claim appears here.
The 800 separate final sessions remain the actual generalization check, and the
Agent must remain frozen before their release.

## How to record an experiment

1. Record the exact commit:

   ```bash
   git rev-parse HEAD
   ```

2. Run tests:

   ```bash
   python3 -m unittest discover -v
   ```

3. Run the unmodified evaluator and retain results:

   ```bash
   /usr/bin/time -p python3 -m evaluator.local_evaluator \
     --output /tmp/intentcart-public-results.json
   ```

4. Copy overall and all four scenario metrics into the table.
5. Record runtime and any exceptions.
6. Mark Keep only after comparing the complete row with the last kept commit.

## Freeze record

Complete this block immediately before submission:

```text
Frozen commit:
Frozen tag:
Evaluation date/time/timezone:
Python version:
Operating system/hardware:
Catalog SHA-256:
Official tests:
Participant tests:
Evaluator runtime:
External model/API:
Prompt tokens:
Completion tokens:
Estimated model cost:
Public repository URL:
Public video URL:
Devpost URL:
```
