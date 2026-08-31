# TechJam 2026 — Track 4: Shopping Copilot

Project instructions. Read this fully before writing code.

---

## 1. What we are building

An agent that plays a guessing game. Each session hides one target product in a
frozen 50,000-item Amazon clothing catalog. The agent receives an anonymized
preference profile and a short customer message. Each turn it may ask a
clarification question, return up to 10 ranked `parent_asin` values, or **both in
the same response**. The session ends when the target appears in the scored Top 10,
or after turn 10. Only exact `parent_asin` equality counts as a hit.

Session scenarios: **Buying** (hard constraints), **Browsing** (vague, semantic),
**Intent Override** (user contradicts themselves; stale slots must be cleared),
**Boundary** (too general / no good match). Metrics are reported per scenario.

We edit exactly one file: `starter/agent.py`.

### Interface

```python
class Agent:
    def reset(self, session_id: str, user_profile: dict) -> None: ...

    def respond(self, session_id: str, user_message: str,
                turn: int, top_k: int) -> dict:
        return {
            "message": "Do you have a material preference?",
            "ask_attribute": "material",          # or None
            "recommendations": [{"parent_asin": "B000..."}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        }
```

`ask_attribute` ∈ `category, material, color, size, style, brand, budget,
feature, use_case, other, null`.

### Scoring

```
TechnicalScore = 0.50 × HitRate@10 + 0.30 × MRR + 0.20 × Efficiency
Efficiency     = clip((11 − MTTC) / 10, 0, 1)
```

MTTC is the mean **first-hit turn**; a miss is assigned turn 11.

**Consequence:** at a low hit rate, most sessions contribute 11 to MTTC, so
Efficiency is largely re-measuring the miss rate. Retrieval quality drives ~70%
of the score. Optimising "conversational elegance" separately is a trap.

Baseline (weak BM25 starter): Hit Rate@10 `0.125`, MRR `0.068034`, MTTC `9.81`
→ TechnicalScore ≈ **0.107**. Target for this build: **> 0.30**.

---

## 2. Hard rules

- Never edit `evaluator/` or the public labels when reporting a score.
- Catalog is read-only. No mutations, no injected ASINs.
- Max 10 turns. Exceeding it scores zero for that session.
- Everything in-memory. No external vector DB service.
- No fine-tuning of foundation models. Text only.
- **Never commit an API key.** Key goes in `.env`, `.env` goes in `.gitignore`.
  Verify with `git log -p | grep -i "sk-"` before final push.
- 800 private sessions use *separate users and separate targets*. Anything
  tuned tightly to the public 200 may not transfer. Prefer few, well-motivated
  components over many tuned knobs.

---

## 3. Phase 0 — Recon (do this before writing any agent code)

Reproduce the baseline first:

```bash
python3 -m evaluator.local_evaluator
cat results.json          # must match docs/baseline_results.json
```

Then read `evaluator/local_evaluator.py` line by line and answer these four
questions in `NOTES.md`. **Do not assume answers — the architecture depends on them.**

| # | Question | Why it matters |
|---|---|---|
| Q1 | When `ask_attribute="color"`, does the simulator reply with the *target's actual colour*? | If yes, each question is a free hard filter and entropy-driven questioning is the core strategy. If no, skip questioning entirely and go all-in on retrieval. |
| Q2 | Is `turn` incremented on every response, or only when we ask? | Decides whether asking is genuinely free. |
| Q3 | What happens to a repeated `parent_asin` — filtered, penalised, ignored? | Decides whether cross-turn exploration works. |
| Q4 | Is MRR the rank within the hit turn's list, or global across turns? | Decides whether late hits are worth chasing. |

Then inspect the data shapes and attribute coverage:

```bash
head -1 data/catalog.jsonl | python3 -m json.tool
head -1 data/public_set.jsonl | python3 -m json.tool

python3 - <<'EOF'
import json, collections
keys, n = collections.Counter(), 0
for line in open('data/catalog.jsonl'):
    d = json.loads(line); n += 1
    for k, v in d.items():
        if v not in (None, '', [], {}): keys[k] += 1
print(f"{n} products\n")
for k, c in keys.most_common(): print(f"{c/n:6.1%}  {k}")
EOF
```

Record coverage per attribute in `NOTES.md`. **Only hard-filter on attributes
with high coverage.** Filtering on a field that is null for 60% of the catalog
deletes the target more often than it helps.

---

## 4. Build order

Each step is gated: run the evaluator, record the delta, commit. Do not proceed
on a step that did not move the number.

### Step 1 — Free wins (~1h, do first, no dependencies)
- Return 10 recommendations on **every** turn, including turns where we also ask.
- **Never re-show an asin** within a session. 10 turns × 10 slots = up to 100
  unique items exposed, which turns this from recall@10 into recall@100.

Expected: the single largest jump of the day.

### Step 2 — Hybrid retrieval (~4h)
- Embed catalog text (title + category + brand + features) once with
  `text-embedding-3-small`. Save as `.npy`. ~50k × 1536; cosine sim is a numpy
  matmul. Never re-embed at query time.
- Three routes fused with **Reciprocal Rank Fusion** (`1/(60+rank)` summed, no
  weight tuning): BM25 on message, dense on message, dense on profile.
- **Popularity prior:** sessions are sampled from the Clothing 5-core
  leave-last-out split, so targets skew popular. Multiply score by
  `log(1 + rating_number)`. *Validate first:* compare the `rating_number`
  distribution of the 200 known public targets against the whole catalog. Only
  ship this if the skew is real, and record the evidence.

### Step 3 — Dialogue state (~3h)
- Plain dict of slots, not a formal FSM. Accumulate by default.
- Intent Override: on a contradicting or new category, clear dependent slots.
- Hard filter for Buying intent, soft boost for Browsing.
- Only filter on attributes that passed the coverage check.

### Step 4 — Question selection (~3h) — **only if Q1 answered yes**
- Compute entropy of each candidate `ask_attribute` across the current candidate
  set; ask about the highest-entropy one.
- If Q1 is no: skip this step. Spend the time on family-level dedupe instead —
  cluster candidates by brand + title similarity and show one representative per
  cluster, so the 10 slots cover more of the distribution.

### Step 5 — LLM layer (~3h)
- **Build a disk cache keyed on prompt hash BEFORE the first API call.** Without
  it, a full eval run is ~15 minutes instead of seconds, and we get four
  experiments today instead of twenty. This is not optional.
- Use the LLM to predict the *target's attributes* as structured JSON (category,
  colour, material, style, price band, with confidence), then match numerically
  against extracted catalog attributes. Do **not** use it to rerank opaque title
  strings.
- Wrap in try/except with fallback to pure retrieval. Never return zero
  recommendations because a call failed.
- Populate the `usage` field honestly.

### Step 6 — Fix the worst scenario (~2h)
Read the per-scenario table. Spend remaining time on the weakest of the four,
not the strongest. A balanced table reads as a system; a lopsided one reads as
an overfit.

---

## 5. Measurement discipline

With 200 sessions, one standard error on Hit Rate at p≈0.35 is
`sqrt(0.35×0.65/200) ≈ 0.034`. **A change worth less than ~3.4 points of hit
rate is statistically invisible.** Do not adopt it. Do not spend an hour on it.

- Hold out 40 of the 200 sessions. Do not look at them until the end.
- Maintain `ABLATION.md`, one row per change, with per-scenario breakdown.
- **Log rejected ideas with their measured deltas.** That list is evidence of
  engineering judgment when a judge asks why the architecture looks like this.

### ABLATION.md template

| # | Change | Hit@10 | MRR | MTTC | Score | Buying | Browsing | Override | Boundary | Keep? |
|---|---|---|---|---|---|---|---|---|---|---|
| 0 | BM25 baseline | 0.125 | 0.068 | 9.81 | 0.107 | | | | | — |
| 1 | Always recommend + dedupe | | | | | | | | | |
| 2 | + hybrid RRF | | | | | | | | | |

---

## 6. Framing for the write-up

Do not describe this as "an AI shopping copilot with intent routing and
semantic reranking" — that is the problem statement read back, and every team
will write it.

Frame it as **sequential experiment design under a turn budget**: on each turn
we choose which 10 items to expose *and* which question to ask, to maximise hit
probability while minimising turns. Under that framing, entropy-based question
selection is not a bolted-on feature, it is the direct consequence of the
problem definition. Maintain an explicit **posterior** `P(item = target)` over
the catalog, updated multiplicatively by the profile prior, popularity prior,
message similarity, each answered clarification, and each set of 10 misses
zeroed out. Sorting the posterior gives the ranked list; its entropy gives the
question. One coherent object instead of a pile of heuristics.

---

## 7. Deliverables (protect the last 2 hours for these)

- **Devpost description:** approach, dev tools, APIs, libraries, datasets.
- **Public repo README:** overview, setup, reproduction steps, limitations and
  what we'd improve, team contributions.
- **Ablation table** with per-scenario breakdown.
- **Disclosure:** model choice, estimated cost, token usage, latency. (Token
  usage is a feasibility metric, not part of the technical score — report
  honestly, don't contort the design to minimise it.)
- **Demo video**, public on YouTube, linked from Devpost. No front end exists
  and UI is out of scope, so a walkthrough of the evaluator run plus two traced
  sessions is explicitly accepted.

**If behind schedule at hour 13, cut the LLM layer, not the write-up.** A clean
0.28 with a strong ablation table beats a scrambled 0.31 with no story.

---

## 8. Judging weights (for prioritisation)

| Criterion | Weight | What actually earns it |
|---|---|---|
| Technical Execution | 35% | The TechnicalScore on the private 800, plus code quality |
| Innovation & Problem Insight | 20% | The framing in §6, not the feature count |
| Impact & Relevance | 20% | Balanced per-scenario performance; generalisation argument |
| Feasibility & Practicality | 15% | Cost/latency disclosure, graceful fallback, in-memory |
| Presentation | 10% (finals) | Answering "why 60 in RRF?" from our own reasoning |

Finals Q&A tests whether we understand our own system. Every non-obvious
constant should have a recorded reason in `NOTES.md`.
