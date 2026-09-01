# Submission v2 Comparison and Selection Record

This document compares the immutable `submission-v1` control with the selected
`submission-v2` implementation using the unmodified organizer evaluator. All
quality results are from the 200-session public development set, which was fully
observed during development. They are not holdout, private-set, or statistical
generalization claims.

## Decision

Select deterministic v2 and keep the LLM reranker disabled by default.

- V2 preserves 100% Hit@10 overall and in every published scenario.
- TechnicalScore increases from 0.890571 to 0.935151 (+0.044580).
- MRR increases from 0.709905 to 0.856504 and MTTC improves from 2.12 to 2.09.
- All five deterministic sample-ID hash slices improve.
- A bounded OpenAI reranking trial produces no score gain and adds latency,
  token cost, and two safely handled failures.

## What changed

`starter.agent.AgentV1` preserves v1 behavior. The production
`starter.agent.Agent` adds one target-blind ranking calibration: after explicit
customer constraints exist and the exact candidate pool contains at most 20
products, catalog `rating_number` moves ahead of weaker BM25/profile tie-breaks.
Exact positional matches, any-position constraint matches, and category equality
remain protected. Broad or unconstrained pools retain v1 ordering.

The threshold is twice the official top-10 output capacity. It limits the change
to cases where structured evidence has already reduced ambiguity and prevents a
popular but weakly related product from dominating broad discovery.

## Full public comparison

Run:

```bash
python3 compare_versions.py
```

| Metric | v1 | selected v2 | Delta |
|---|---:|---:|---:|
| Hit@10 | 1.000000 | 1.000000 | 0.000000 |
| MRR | 0.709905 | 0.856504 | +0.146599 |
| MTTC | 2.120000 | 2.090000 | -0.030000 |
| Efficiency | 0.888000 | 0.891000 | +0.003000 |
| TechnicalScore | 0.890571 | **0.935151** | **+0.044580** |

Scenario cells are `Hit@10 / MRR / MTTC`:

| Scenario | v1 | selected v2 |
|---|---|---|
| Buying | 1.000000 / 0.703730 / 1.612500 | **1.000000 / 0.893889 / 1.550000** |
| Browsing | 1.000000 / 0.671032 / 1.950000 | **1.000000 / 0.791329 / 1.950000** |
| Intent Override | 1.000000 / 0.811667 / 3.666667 | **1.000000 / 0.909444 / 3.633333** |
| Boundary | 1.000000 / 0.765000 / 2.900000 | **1.000000 / 0.920000 / 2.900000** |

Paired session outcomes are 59 better, 132 equal, and 9 worse. Aggregate gains
do not hide those nine regressions.

## Deterministic stability slices

Samples are assigned to one of five folds using the first four bytes of
`SHA-256(sample_id) mod 5`. This is a stability diagnostic, not a held-out
experiment, because the entire public set was already observed.

| Fold | Sessions | v1 score | v2 score | Delta |
|---:|---:|---:|---:|---:|
| 0 | 40 | 0.921208 | 0.950708 | +0.029500 |
| 1 | 46 | 0.841752 | 0.928929 | +0.087177 |
| 2 | 33 | 0.889178 | 0.932056 | +0.042878 |
| 3 | 27 | 0.903333 | 0.937037 | +0.033704 |
| 4 | 54 | 0.903935 | 0.929876 | +0.025941 |

## Calibration threshold sweep

All variants are deterministic and use no targets or scenario labels at
inference time.

| Variant | MRR | MTTC | TechnicalScore | Decision |
|---|---:|---:|---:|---|
| Disabled (v1 order) | 0.709905 | 2.120000 | 0.890571 | Control |
| Popularity on every constrained pool | — | — | 0.924915 | Rejected; one hash slice regressed |
| Candidate pool ≤20 | 0.856504 | 2.090000 | **0.935151** | **Selected** |
| Candidate pool ≤50 | 0.853629 | 2.005000 | 0.935989 | Rejected as more aggressive public tuning |
| Soft popularity bucket | — | — | 0.930674 | Rejected; lower score |

The ≤50 aggregate is 0.000838 higher, but the ≤20 rule has a stronger
worst-slice improvement, applies to fewer cases, and has a simple relationship
to the official top-10 capacity. The selection prioritizes a conservative
behavioral boundary over the last public-set decimal.

## Optional LLM reranking experiment

The opt-in path uses the OpenAI Responses API with the pinned
`gpt-5.4-nano-2026-03-17` snapshot. It receives only the deterministic top-10
candidate records, requests a strict JSON-schema permutation, sets `store=false`,
records token usage, and returns the deterministic order on any failure. It
cannot invent or retrieve a new ASIN.

The final bounded check used the first 10 public sessions:

```bash
python3 compare_versions.py --with-llm --env-file .env --limit 10
```

| Measurement | Result |
|---|---:|
| Deterministic v2 TechnicalScore | 0.952286 |
| Hybrid v2 TechnicalScore | 0.952286 |
| Score delta | 0.000000 |
| API attempts / applied | 11 / 9 |
| Safe fallbacks | 2 |
| Input / output tokens | 25,020 / 953 |
| Estimated model cost | US$0.006195 |

The two fallbacks were one invalid candidate permutation and one timeout. Both
preserved valid deterministic recommendations. A separate preliminary
`gpt-5.4-mini` trial also failed to justify adoption. A full 200-session paid
run was not performed after the bounded selected-configuration trial showed no
gain.

The implementation follows the official
[Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
and [GPT-5.4 nano](https://developers.openai.com/api/docs/models/gpt-5.4-nano)
documentation. Pricing was checked on 2026-09-01 at US$0.20 per million input
tokens and US$1.25 per million output tokens.

## Reproducibility and security

- `python3 compare_versions.py` needs no key and evaluates both versions.
- The selected `Agent` default never enables or calls the API, even if a key is
  present in the process environment.
- The optional CLI loads only `OPENAI_API_KEY` and `INTENTCART_LLM_MODEL` from
  an ignored `.env` file; it never executes shell syntax or prints the key.
- `.env.example` contains placeholders only. A real key must remain in `.env`.
- Two full v2 evaluator outputs are byte-identical with SHA-256
  `7b553ce517e7c3122a9df21261703027b07e03b0321c1720b60969173065d31e`.
- The v1 result embedded by the comparison harness exactly matches the frozen
  v1 evidence, including SHA-256
  `0faad255af1b1ad12fca5923fd124e0192594e88f348bc3e3bd5caa5f3cdad71`.

Because an API key was previously exposed outside Git during development, it
must be revoked and replaced before any optional future experiment.
