# End-to-End Inference Evidence

This is a captured, reproducible run of the committed IntentCart Agent against
the unmodified public simulator policy. It demonstrates catalog loading,
session reset, profile conditioning, multi-turn state updates, route selection,
clarification, ranked product predictions, intent override recovery, and the
scored outcome.

Reproduce from the repository root:

```bash
python3 demo.py public_0007 public_0003
```

The public target is consulted only after each Agent response to label the hit;
`starter/agent.py` never receives ground truth, intent cards, or scenario labels.

## Browsing inference — `public_0007`

```text
Turn 1 customer:
  I'm looking for Tees & Blouses Tunics, but I'm still exploring.

Agent policy:
  route=discovery
  candidate_count=519
  ask_attribute=other
  profile_terms_used=True

Top-10 predictions:
  B07YG84J18, B07TX4JMRP, B09Q8VNLML, B08T9MX1T5, B08K4JWMR6,
  B08DNVQJ47, B06XD7GC36, B08FYN6CBS, B07YS37B59, B07VXMFLD4

Turn 2 customer:
  For that, what matters is: polyester; 75% Polyester, 20% Rayon, 5% Spandex.

Agent policy:
  route=discovery
  candidate_count=1
  ask_attribute=None
  profile_terms_used=False

Top-10 predictions:
  B08PF98BV4, B0BD4QJ21V, B07J3C2Y2X, B07XFLB9WQ, B09D8BGTQX,
  B08DXV8WR3, B06XYWNYV3, B074YZPGJ8, B076JDDZ7S, B07XW8Z8QT

Outcome:
  target found at rank 1 on turn 2
```

The trace shows vague discovery, a simultaneous question plus recommendations,
constraint accumulation, candidate reduction from 519 to 1, dynamic truncation
of the weaker profile prior, and rank-1 conversion.

## Intent Override inference — `public_0003`

```text
Turn 1 customer:
  I'm looking for Watches Wrist Watches. Stainless Steel Band

Agent policy:
  route=fallback
  candidate_count=1034
  ask_attribute=other
  profile_terms_used=True

Turn 2 customer:
  For that, what matters is: Water Resistant; 3 Year Battery.

Agent policy:
  route=fallback
  candidate_count=1
  ask_attribute=None
  profile_terms_used=False

Top prediction:
  B09YMTWDXJ

Evaluator state:
  target is present at rank 1 but is not score-eligible before the override

Turn 3 customer:
  Actually, ignore my earlier preference. What I need is: Water Resistant.

Agent policy:
  route=override_recovery
  candidate_count=1
  ask_attribute=None
  profile_terms_used=False

Top-10 predictions:
  B09YMTWDXJ, B079Q78Q9N, B0BM4WGCJ6, B07GBT82S7, B0B6CYH17L,
  B09H3VT8JH, B0BQ6XY143, B079VQPVY1, B07YCH6HLZ, B08GRJ9963

Outcome:
  eligibility epoch increments; target found at rank 1 on turn 3
```

This trace proves selective stale-intent erasure and rescoring after an explicit
override. The Agent preserves valid evidence while allowing a pre-override item
to become eligible again.

## Aggregate evaluator evidence

Command:

```bash
python3 -m evaluator.local_evaluator --output results.json
```

Committed Agent `0ea7705` on all 200 public development sessions:

| Metric | Result |
|---|---:|
| Hit@10 | 1.000000 |
| MRR | 0.709905 |
| MTTC | 2.120000 |
| Efficiency | 0.888000 |
| TechnicalScore | 0.890571 |
| Prompt/completion tokens | 0 / 0 |
| Exceptions | 0 |

Two complete runs produced byte-identical result JSON with SHA-256
`0faad255af1b1ad12fca5923fd124e0192594e88f348bc3e3bd5caa5f3cdad71`.
