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

---

## 9. Three-hour concurrent sprint plan (current execution mode)

This section is the operating plan when only two people and three hours are
available. It **supersedes the time estimates and build sequence in Sections 3–7**
for this sprint. The earlier sections remain useful as rationale and future work,
but attempting the embedding, entropy, and LLM phases now would prevent a complete
submission.

The fast path is designed to finish in **2 hours 40 minutes**, leaving a
20-minute emergency buffer. The goal is not maximum feature count. The goal is a
measured, reproducible agent plus every required submission artifact.

### 9.1 Decisions already made

Do not reopen these decisions during the sprint unless an official rule directly
contradicts them:

1. **One production-code owner.** Only Person A edits `starter/agent.py`.
2. **One delivery owner.** Person B owns tests, measurement, README, Devpost, and
   video assets. This is the only conflict-free split while production code is
   intentionally kept in one file.
3. **No external LLM or embedding API.** The environment currently has no NumPy,
   scikit-learn, PyTorch, or SentenceTransformers. Installing a stack, generating
   50,000 embeddings, adding a cache, and validating failure behavior is outside
   the critical path.
4. **No upstream Amazon data download.** The official frozen catalog contains
   every field used to derive the simulator's intent cards.
5. **No UI.** A headless evaluator and traced conversations satisfy the track's
   demo requirement.
6. **No public-label tuning or hardcoded ASINs.** All derived signatures must be
   computed from the frozen catalog and published simulator rules.
7. **No unmeasured feature enters the frozen commit.** The best measured commit
   wins, even if a later commit looks more sophisticated.
8. **Documentation starts immediately.** Person B does not wait for final metrics
   before writing everything else.

The default role assignment is:

- **Person A — Agent and integration lead:** owns agent behavior, scoring runs,
  merges, release commit, and Git tag.
- **Person B — Evaluation and delivery lead:** owns tests, evidence, README,
  Devpost, video, and the submission checklist.

The pair may swap the roles once before starting. Do not swap halfway through.

### 9.2 Verified evaluator behavior

These are answers to the four recon questions in Section 3 and should be copied
to `NOTES.md`. There is no need to spend sprint time rediscovering them.

#### Q1 — What a clarification returns

The simulator uses the structured `ask_attribute`; it does not infer the
attribute from the natural-language question.

- A specific attribute such as `color` returns an undisclosed target constraint
  only when that constraint is classified as `color`.
- If no undisclosed target constraint has that classification, the simulator
  returns that it has no additional preference.
- `ask_attribute="other"` matches any undisclosed constraint and returns up to
  two of them.
- On a Boundary session, the first non-null question deliberately receives one
  "no preference" response. Later questions work normally.

**Sprint consequence:** use a natural multi-facet question with
`ask_attribute="other"`. Entropy optimization over individual attributes is
inferior to the published `other` behavior for this evaluator and costs time to
implement.

#### Q2 — Turn increments

Every call to `respond()` consumes a turn, regardless of whether the agent asks
a question. Asking is therefore not free. However, the response may include both
ten recommendations and a clarification question.

**Sprint consequence:** always recommend on every turn, including clarification
turns.

#### Q3 — Repeated ASINs

The evaluator removes invalid and duplicate ASINs within the current response.
It does not impose a separate cross-turn penalty or cross-turn deduplication.

If an ASIN was shown earlier and the session continued, that ASIN was not the
target **except before an Intent Override becomes score-eligible**. The evaluator
ignores target recommendations before the configured override turn, so a
pre-override recommendation is not evidence of a miss.

**Sprint consequence:** maintain a per-session `seen_asins` set and fill each
ordinary turn with unseen candidates. When the explicit override arrives, clear
or separately scope the pre-override seen set so an early ignored target remains
eligible after the evaluator begins scoring that session.

#### Q4 — MRR

MRR is calculated from the target's rank within the successful turn's list. It
is not a global rank across all turns. A late hit is penalized through MTTC.

**Sprint consequence:** order the strongest candidate first on every turn, but
use the remaining slots to cover unseen candidates.

Additional evaluator facts that drive the sprint design:

- The final evaluator uses the same deterministic customer templates and
  `ask_attribute` response policy as the public evaluator.
- Intent cards are derived from the same frozen catalog metadata available to
  the Agent.
- A Buying opening exposes the target's coarse category and first hard
  constraint.
- A Browsing opening exposes only the coarse category.
- An Intent Override opening includes an old soft preference; the new hard
  preference is revealed on turn 3 or 4, and a hit cannot count before then.
- The official evaluator processes sessions sequentially, so shared immutable
  indexes are safe while conversation state must remain isolated.

### 9.3 Sprint architecture

The sprint implementation is a two-route, contract-aware agent written entirely
inside `starter/agent.py` with Python's standard library.

#### Route A — Catalog-signature precision route

At startup, reproduce the published catalog-derived intent signature for every
product:

1. Build searchable text from `title`, `features`, `details`, `description`,
   `categories`, and `store`.
2. Derive the coarse category using the same category cleanup rules as the
   evaluator.
3. Build the ordered candidate constraints from features followed by details.
4. Insert the first recognized material and color at the front, matching the
   published intent-card construction.
5. Append price as `budget around $...` when price exists.
6. Retain the first two values as hard constraints and the next two as soft
   constraints.

Precompute:

- `product_rows`: ASIN, coarse category, ordered constraint list, rating, rating
  count, and searchable fields.
- `by_category`: normalized coarse category to product indices.
- `by_constraint`: normalized exact constraint to product indices.

This route is strongest when customer messages contain exact catalog-derived
constraints. It is legitimate catalog preprocessing and must not access
`ground_truth` or `public_set.jsonl`.

#### Route B — Accumulated-context BM25 fallback

Retain the starter SQLite FTS5 index. Instead of searching only the latest
message, build a query from the active session category and active constraints.
Use this route when:

- no exact constraint index entry exists;
- exact constraint intersections become empty;
- multiple products have indistinguishable intent signatures; or
- an unexpected message template appears.

Do not hard-filter on price, description, or sparsely populated detail fields.
Use exact catalog signatures for precision and BM25 for recall.

#### Session state

Each `reset()` creates a state dictionary with at least:

```python
{
    "profile": user_profile,
    "mode": None,
    "category": None,
    "constraints": [],
    "old_preference": None,
    "seen_asins": set(),
    "asked_attributes": [],
    "last_candidate_count": 0,
}
```

Rules:

- Accumulate new constraints by default.
- Deduplicate normalized constraints while preserving order.
- Treat "I don't have a preference" as no new evidence; do not exclude products.
- On override, remove only `old_preference`, add the new requirement, and retain
  independently disclosed hard constraints.
- Treat recommendations made before an explicit Intent Override as a separate
  eligibility epoch; reset their seen status when the override arrives.
- Never share mutable state between session IDs.
- Validate that `reset()` was called before `respond()`.

#### Message parsing

Support the published templates first, with a safe generic fallback:

- `I'm looking for <category>. A key requirement is: <value>.` → Buying.
- `I'm looking for <category>, but I'm still exploring.` → Browsing.
- `I'm looking for <category>. <old preference>` → probable Intent Override.
- `For that, what matters is: <value>; <value>.` → append constraints.
- `Actually, ignore my earlier preference. What I need is: <value>.` → override.
- `I don't have a preference for <attribute>; ...` → Boundary/no evidence.

Normalize whitespace and trailing punctuation before lookup. Preserve the raw
text only for BM25 fallback.

#### Ranking without fragile tuned weights

Prefer a lexicographic ranking tuple over a large collection of tuned constants:

1. Number of observed values matching the correct signature position.
2. Number of observed values matching anywhere in the product signature.
3. Exact coarse-category match.
4. BM25 reciprocal rank or BM25 score.
5. `rating_number` as a final popularity tie-breaker.
6. Stable ASIN ordering for deterministic output.

When an exact constraint index produces a non-empty intersection, use it to
narrow the candidate set. If an intersection would become empty, keep the prior
candidate set and treat the constraint as a soft ranking signal instead. This
prevents a missing or ambiguous field from deleting the target.

After ranking:

1. Remove ASINs already in the current eligibility epoch's `seen_asins`.
2. Take up to `top_k` candidates.
3. If fewer than `top_k` remain, fill from unseen BM25/category/global fallback
   candidates.
4. Add returned ASINs to `seen_asins`.
5. Return valid unique ASIN dictionaries in exact rank order.

#### Clarification policy

Until the candidate set is confidently small or the turn limit is near, return:

```python
"message": (
    "What other requirement matters most—material, color, fit, style, "
    "budget, or intended use?"
),
"ask_attribute": "other",
```

Recommendations must still be present. If no useful question remains, set
`ask_attribute` to `None` and provide a concise customer-facing message.

#### Failure fallback

Wrap parsing and retrieval so an unexpected value cannot produce an empty or
invalid response. On internal failure:

- fall back to latest-message BM25;
- return up to ten valid unseen ASINs;
- return a string message;
- use `ask_attribute="other"` when another turn is available; and
- report zero token usage because the sprint path uses no LLM.

### 9.4 File and branch ownership

The statement "edit exactly one file" means **one production Agent file**.
Supporting tests and submission documents may be created without changing the
official evaluator.

| Owner | Branch | Exclusive files |
|---|---|---|
| Person A | `feat/agent` | `starter/agent.py` |
| Person B | `feat/delivery` | `tests/test_agent.py`, `README.md`, `NOTES.md`, `ABLATION.md`, `DEVPOST.md`, `DEMO_SCRIPT.md`, `requirements.txt` |

Rules:

- Person A never edits delivery files before integration.
- Person B never edits `starter/agent.py`.
- Neither person edits `evaluator/`, `data/public_set.jsonl`, or the catalog.
- Communicate facts and metrics through chat, not by temporarily editing the
  other owner's files.
- Push small named checkpoint commits; do not pass uncommitted patches between
  machines.
- Person A is the only person who merges to `main` during the sprint.

Separate machines:

```bash
# Person A
git switch main
git pull --ff-only
git switch -c feat/agent

# Person B, in a separate clone
git switch main
git pull --ff-only
git switch -c feat/delivery
```

One machine with two working directories:

```bash
git branch feat/delivery
git switch -c feat/agent
git worktree add ../shopping-copilot-delivery feat/delivery
```

### 9.5 Exact three-hour timeline

Use a timer. Each gate is a hard stop. When a gate arrives, choose the best
working commit rather than extending the phase.

#### 00:00–00:10 — Shared kickoff

Person A:

```bash
test -f data/catalog.jsonl || gzip -dc catalog.jsonl.gz > data/catalog.jsonl
python3 -m unittest discover -v
git switch -c feat/agent
```

The catalog is ignored by Git and remains read-only.

Person B:

```bash
git switch -c feat/delivery
```

Then immediately create:

- `NOTES.md` containing the verified evaluator answers in Section 9.2;
- `ABLATION.md` with the official baseline row;
- README headings from Section 9.9;
- `DEVPOST.md` headings from Section 9.10; and
- `DEMO_SCRIPT.md` containing the timeline from Section 9.11.

Shared gate at minute 10:

- both branches exist;
- both people can load the repository;
- the catalog exists locally;
- `.env` and `data/catalog.jsonl` are ignored; and
- no one is waiting for the other to begin.

#### 00:10–00:55 — Parallel build A

Person A implements the complete minimum Agent in this order:

1. Normalization and catalog-signature helpers.
2. Shared immutable catalog and reverse indexes.
3. Session initialization.
4. Published-template parsing.
5. Ordered signature ranking.
6. BM25 fallback.
7. Cross-turn deduplication.
8. Clarification plus recommendations in the same response.
9. Output validation and failure fallback.

Do not optimize or refactor before the first full run works.

Person B works concurrently:

1. Add small contract/state tests using a temporary miniature catalog.
2. Verify response fields, valid attributes, unique recommendations, and session
   isolation.
3. Add an override test proving that the old preference is removed while other
   constraints remain.
4. Write README and Devpost text that does not depend on final metrics.
5. Prepare a baseline-versus-final table with final cells left blank.
6. Prepare the video narration and terminal commands.

At minute 45, Person A stops feature work long enough to run a smoke evaluation.
At minute 55, Person A must push a runnable checkpoint:

```bash
git add starter/agent.py
git commit -m "Implement stateful catalog-signature agent"
git push -u origin feat/agent
```

This checkpoint is **RC1**. Person B can now evaluate RC1 while Person A continues
on RC2. A stable checkpoint prevents evaluation from blocking ongoing tuning.

#### 00:55–01:20 — Parallel build B

Person B fetches RC1 and runs:

```bash
git fetch origin feat/agent
SHOPPING_CATALOG="$(pwd)/data/catalog.jsonl"
git worktree add --detach ../shopping-copilot-rc1 origin/feat/agent
cd ../shopping-copilot-rc1
python3 -m unittest discover -v
python3 -m evaluator.local_evaluator \
  --catalog "$SHOPPING_CATALOG" \
  --output /tmp/shopping-copilot-rc1-results.json
```

This detached worktree evaluates the exact pushed commit without overwriting
delivery-branch work. Return to the delivery worktree after the run. Record:

- overall Hit Rate, MRR, MTTC, Efficiency, and TechnicalScore;
- all four scenario tables;
- runtime;
- exceptions or empty outputs; and
- the RC1 commit hash.

Person A uses the same interval to fix only high-value problems:

- parser failure;
- empty candidate lists;
- incorrect override erasure;
- duplicate recommendations;
- target tied below rank 10 because positional signature information was lost;
  or
- the weakest scenario from RC1.

Do not start dense retrieval, an LLM, or entropy selection.

Score gates at minute 80:

- **Floor:** TechnicalScore ≥ 0.75.
- **Target:** TechnicalScore ≥ 0.85.
- **Coverage target:** Hit Rate@10 ≥ 0.97.
- **Efficiency target:** MTTC ≤ 2.5.

If RC1 clears the target, stop architectural changes. Only correctness fixes are
allowed. If it clears the floor but not the target, use one measured tuning pass.
If it misses the floor, simplify to exact ordered signature matching plus
`ask_attribute="other"`; do not add components.

#### 01:20–01:35 — Select the release candidate

Person A pushes RC2 if and only if it is runnable:

```bash
git add starter/agent.py
git commit -m "Improve ranking and scenario handling"
git push origin feat/agent
```

Person B compares RC1 and RC2 using the official evaluator. Choose the candidate
with the higher measured TechnicalScore, subject to:

- no scenario-regression catastrophe;
- no exceptions;
- deterministic repeated runs; and
- all contract tests passing.

An unmeasured RC2 automatically loses to a measured RC1.

#### 01:35–01:50 — Integrate conflict-free branches

Person B commits and pushes delivery work:

```bash
git add README.md NOTES.md ABLATION.md DEVPOST.md DEMO_SCRIPT.md requirements.txt tests/test_agent.py
git commit -m "Add evaluation evidence and submission documentation"
git push -u origin feat/delivery
```

Person A integrates:

```bash
git fetch origin
git switch main
git pull --ff-only
git merge --no-ff origin/feat/agent
git merge --no-ff origin/feat/delivery
```

Because file ownership is disjoint, these merges should be automatic. If a
conflict appears, preserve the selected Agent commit and the newest delivery
document; do not manually combine competing Agent implementations.

Run on integrated `main`:

```bash
python3 -m unittest discover -v
python3 -m evaluator.local_evaluator
```

Person B copies the integrated metrics into `ABLATION.md`, README, and Devpost.

#### 01:50–02:00 — Hard code freeze

No new features after minute 110.

Audit:

```bash
git status --short
git diff 1d9b9c2 -- evaluator data/public_set.jsonl
git ls-files .env data/catalog.jsonl results.json
```

Expected:

- clean working tree after final documentation commit;
- no evaluator or public-label changes;
- `.env`, decompressed catalog, and `results.json` are not tracked;
- no public target ASINs are embedded in Agent code; and
- the documented command reproduces the score.

Check history for likely API-key patterns without printing matched secret values:

```bash
if git log -p --all | grep -qiE 'sk-[A-Za-z0-9_-]{12,}'; then
  echo "Possible API key found in Git history" >&2
  exit 1
fi
```

Freeze:

```bash
git add README.md NOTES.md ABLATION.md DEVPOST.md DEMO_SCRIPT.md requirements.txt tests/test_agent.py
git commit -m "Prepare TechJam Shopping Copilot submission" || true
git tag submission-v1
git push origin main --tags
```

Record the exact frozen commit hash:

```bash
git rev-parse HEAD
```

#### 02:00–02:40 — Parallel delivery

Person A:

1. Verify the GitHub repository is public in a signed-out browser.
2. Check that the README renders correctly.
3. Run the README setup/evaluation commands from a clean clone or clean
   temporary directory when feasible.
4. Capture terminal output for one Browsing session, one Intent Override
   session, and aggregate metrics.
5. Send Person B the frozen commit hash, exact metric table, runtime, Python
   version, and hardware description.
6. Fix only broken links, incorrect commands, or release-blocking documentation.
   Do not modify Agent behavior after the tag.

Person B:

1. Record the three-minute video using the frozen build.
2. Use one clean screen capture; avoid animation and time-consuming editing.
3. Upload it to YouTube as Public.
4. Fill Devpost from `DEVPOST.md`.
5. Add the public repository and video links.
6. Fill individual team contributions explicitly.
7. Save the Devpost draft early rather than waiting for the upload to finish.

#### 02:40–02:55 — Joint submission audit

Both people independently open and verify:

- public repository without authentication;
- public YouTube video;
- Devpost repository link;
- Devpost video link;
- written project description;
- technology, API, library, and dataset disclosure;
- README setup and reproduction command;
- final metrics and baseline comparison;
- limitations and future improvements;
- team contributions; and
- the frozen commit hash.

Submit no later than minute 175. Capture the confirmation screen and URL.

#### 02:55–03:00 — Reserved buffer

Use only for upload, authentication, or Devpost failures. Do not reopen code.

### 9.6 Person A implementation checklist

Person A should check these off in order:

- [ ] `Agent` initializes once and indexes exactly 50,000 catalog products.
- [ ] Index construction does not mutate catalog objects or write catalog data.
- [ ] Catalog signature logic is independent of public labels.
- [ ] `reset()` stores an isolated state for every session ID.
- [ ] Buying opening is parsed.
- [ ] Browsing opening is parsed.
- [ ] Clarification constraints are appended in order.
- [ ] Intent Override removes only the stale preference.
- [ ] Boundary/no-preference message does not eliminate candidates.
- [ ] Exact signature route works when constraints match.
- [ ] BM25 route works when signatures do not match.
- [ ] Previous ASINs are not recommended again within the same scoring epoch;
      the explicit override transition resets pre-eligibility history.
- [ ] Every turn returns up to ten recommendations.
- [ ] Clarification turns also contain recommendations.
- [ ] `message` is always a string.
- [ ] `ask_attribute` is allowed or `None`.
- [ ] `recommendations` are valid, unique, and ordered.
- [ ] `usage` contains non-negative integers.
- [ ] Exceptions fall back to valid BM25 output.
- [ ] Two full evaluator runs are deterministic.

### 9.7 Person B evaluation checklist

Person B owns evidence, not just prose:

- [ ] Record baseline commit and baseline metrics.
- [ ] Record RC1 commit and metrics.
- [ ] Record RC2 only if it receives a complete evaluator run.
- [ ] Record per-scenario metrics for the selected release.
- [ ] Record accepted and rejected changes in `ABLATION.md`.
- [ ] Add contract tests without changing the official evaluator.
- [ ] Test session isolation with two interleaved session IDs.
- [ ] Test cross-turn recommendation deduplication.
- [ ] Test Intent Override erasure.
- [ ] Test Boundary/no-preference handling.
- [ ] Measure evaluator wall-clock runtime.
- [ ] Record Python version and hardware.
- [ ] Report model cost and token use as zero for the offline path.
- [ ] Confirm all documented metrics come from the unmodified evaluator.

### 9.8 Release gates and fallback ladder

Use this ladder rather than improvising under time pressure.

#### Gate A — Agent fails to initialize

1. Remove optional preprocessing.
2. Verify the catalog path.
3. Restore the original SQLite FTS build.
4. Run a single-session smoke test before the full evaluator.

#### Gate B — Score below 0.75

1. Verify that the ordered intent signature matches evaluator construction.
2. Verify that `ask_attribute="other"` is being sent every ambiguous turn.
3. Verify that all returned constraints are accumulated.
4. Verify that seen ASINs are excluded.
5. Verify that positional matches outrank popularity.
6. Remove any hard filter that empties the candidate pool.

Do not respond by adding an embedding model.

#### Gate C — Buying strong, Browsing weak

1. Ensure the first clarification is asked on turn 1.
2. Ensure turn 1 still returns recommendations.
3. On turn 2, use both revealed constraints in order.
4. Use BM25/category diversity for unresolved ties.

#### Gate D — Override weak

1. Do not clear category or independently disclosed hard constraints.
2. Remove only the initial old soft preference.
3. Preserve constraints learned before the override turn.
4. Place the newly revealed hard constraint at high priority.

#### Gate E — Boundary weak

1. Treat the first no-preference reply as neutral evidence.
2. Ask `other` again on the next turn.
3. Continue returning unseen candidates on both turns.

#### Gate F — Video or upload behind schedule

1. Stop editing code and documentation.
2. Record one uninterrupted terminal walkthrough.
3. Export/upload at 720p if necessary.
4. Use the prepared script without voice retakes unless audio is unintelligible.
5. Save and submit the Devpost draft before polishing optional fields.

#### Gate G — Integration regression

1. Identify whether RC1 or RC2 was the last fully measured commit.
2. Revert the Agent file to that exact commit.
3. Keep delivery documents and correct their metrics.
4. Re-run tests once, tag, and freeze.

### 9.9 Required README structure

Person B should replace the organizer-generic README with this structure while
preserving attribution and official setup facts:

1. **Project name and tagline**
   - One sentence explaining the customer outcome.
2. **Problem**
   - Keyword search fails when intent is vague or changes mid-session.
3. **Key insight**
   - Shopping is sequential experiment design under a turn budget.
4. **Architecture**
   - Catalog-signature precision route.
   - Accumulated-context BM25 fallback.
   - Session state and selective override erasure.
   - Clarification plus recommendation policy.
5. **Why this generalizes**
   - No public target hardcoding.
   - All preprocessing comes from the frozen catalog.
   - Few fixed components and deterministic behavior.
6. **Repository layout**
7. **Requirements**
   - Exact/recommended Python version.
   - Standard-library-only dependency disclosure if retained.
8. **Setup**
   - Clone.
   - Verify checksum.
   - Decompress catalog.
9. **Run**
   - One official evaluator command.
10. **Results**
    - Baseline and final overall metrics.
    - Per-scenario table.
    - Runtime, tokens, and cost.
11. **Ablations**
    - Link to `ABLATION.md`.
12. **Example conversation**
    - At least one complete multi-turn trace.
13. **Limitations**
14. **Future work**
15. **Data attribution**
16. **Team contributions**
17. **Reproduction/frozen commit**

Do not claim private-set performance. Label every metric as a public development
result.

### 9.10 Devpost description template

`DEVPOST.md` should contain copy-ready text that follows the supplied deliverable
exactly rather than adding generic hackathon sections. It must cover:

1. how the solution addresses the supplied problem statement;
2. what the Agent does and how it is built;
3. an end-to-end inference demonstration and measured public-development result;
4. development tools used;
5. APIs used, accurately stated as none for the frozen runtime;
6. libraries and frameworks used;
7. datasets and assets used;
8. relevant impact, feasibility, limitations, and team contributions; and
9. the public repository and YouTube links.

Do not add a project-inspiration section. List only tools actually used, do not
claim a model/vector stage that is absent from the frozen build, and label every
200-session metric as public development rather than final/private performance.

#### Team contributions

Use explicit individual bullets, matching the actual split in Section 9.4.

### 9.11 Three-minute demo script

Keep the uploaded video at or below three minutes.

| Time | Visual | Narration goal |
|---|---|---|
| 0:00–0:20 | Title plus baseline score | Define the search problem and ten-turn constraint. |
| 0:20–0:45 | One architecture diagram or README diagram | Explain precision route, BM25 fallback, state, and question policy. |
| 0:45–1:25 | Complete Browsing trace | Show vague opening, broad clarification, revealed constraints, and narrowing. |
| 1:25–2:00 | Complete Intent Override trace | Show stale preference removal without losing valid constraints. |
| 2:00–2:30 | Evaluator metric table | Compare official baseline and frozen public result, including scenario balance. |
| 2:30–2:48 | Cost/latency/reproducibility | State in-memory execution, model cost, token use, and evaluator command. |
| 2:48–3:00 | Public repository and team | Show README/repository and close with the value proposition. |

One person narrates. The other operates the terminal. Do not alternate speakers
unless rehearsed; handoffs consume time and create audio inconsistencies.

### 9.12 Final definition of done

The sprint is complete only when every item below is true:

- [ ] `starter/agent.py` exports the required `Agent` interface.
- [ ] Official unit tests pass.
- [ ] Added Agent tests pass.
- [ ] Official evaluator completes without exceptions.
- [ ] Final metrics are recorded from the unmodified evaluator.
- [ ] No public labels or target ASINs are used by the Agent.
- [ ] Catalog remains read-only.
- [ ] `.env` is ignored and no API key appears in tracked history.
- [ ] Repository is public and accessible while signed out.
- [ ] README contains setup, reproduction, results, limitations, future work,
      attribution, and team contributions.
- [ ] Devpost contains approach, tools, APIs, libraries, datasets, and links.
- [ ] Video is public on YouTube and no longer than three minutes.
- [ ] Devpost links open correctly.
- [ ] Frozen commit hash and tag are recorded.
- [ ] Submission confirmation is captured.

### 9.13 After the submission deadline

The 800 final sessions are released only after the Devpost deadline. When they
arrive:

1. Check out the frozen submission tag in a clean directory.
2. Do not modify Agent code, prompts, indexes, model configuration, or evaluator.
3. Place the released final data where documented by the organizer.
4. Run the unmodified official evaluator.
5. Retain `results.json`, the frozen commit hash, Python version, hardware,
   runtime, and any relevant logs.
6. Do not retrospectively update the submitted implementation based on private
   results.

This post-deadline run is evidence for the organizer, not another development
round.

---

## 10. Detailed final-product specification

This section defines exactly what the pair is expected to deliver at the end of
the sprint. It is a reference contract, not an additional feature backlog. If a
detail here conflicts with the time gates in Section 9.5, preserve correctness,
reproducibility, and submission completeness before optional polish.

### 10.1 Product identity and promise

Working product name:

> **IntentCart — a stateful shopping copilot that treats each conversation turn
> as a retrieval-and-information decision.**

One-sentence product promise:

> IntentCart converts vague, evolving shopping requests into ranked product
> recommendations by combining catalog-derived intent signatures, lexical
> retrieval, selective memory, and proactive clarification—all in memory and
> without external model cost.

The final product is not a website. It is a Python Agent that:

1. Loads the frozen 50,000-product catalog once.
2. Receives a safe user profile at session reset.
3. Receives one customer message per turn.
4. Updates an isolated session state.
5. Returns a natural message, one allowed `ask_attribute` or `None`, up to ten
   ranked catalog ASINs, and usage information.
6. Finds the hidden target as early and as highly ranked as possible.
7. Produces deterministic results for identical catalog, session, and messages.

The product succeeds only when the **code, evidence, documentation, video, and
submission** all describe the same frozen implementation.

### 10.2 Public interface contract

The final `starter/agent.py` must continue to export:

```python
class Agent:
    def __init__(self, catalog_path: str | Path = "data/catalog.jsonl") -> None:
        ...

    def reset(self, session_id: str, user_profile: dict) -> None:
        ...

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        ...
```

Required response shape:

```python
{
    "message": str,
    "ask_attribute": (
        "category" | "material" | "color" | "size" | "style" |
        "brand" | "budget" | "feature" | "use_case" | "other" | None
    ),
    "recommendations": [
        {"parent_asin": str},
        # at most top_k entries, ranked best to worst
    ],
    "usage": {
        "prompt_tokens": int,      # 0 for the offline sprint build
        "completion_tokens": int,  # 0 for the offline sprint build
    },
}
```

Interface invariants:

- `session_id` must already have been passed to `reset()`.
- `turn` is between 1 and 10.
- `top_k` is expected to be 10, but code should respect the provided positive
  value up to the evaluator limit.
- `message` is never `None`.
- `recommendations` is always a list, even on failure.
- Every returned ASIN exists in the loaded catalog.
- No ASIN occurs twice in one response.
- Recommendation order is the actual rank order; the optional numeric `score`
  field is unnecessary because the evaluator ignores it.
- Usage integers are non-negative and honest.

### 10.3 Internal data model

The implementation may use dictionaries or small tuples rather than dataclasses
to stay inside one file, but the conceptual types and invariants should match the
following definitions.

#### ProductRecord

```text
ProductRecord
  asin: str                         unique catalog parent_asin
  title: str
  coarse_category: str             normalized last useful category levels
  ordered_constraints: tuple[str]  hard constraints followed by soft constraints
  searchable_text: str             flattened catalog fields
  rating: float
  rating_number: int
```

Invariants:

- One record per catalog ASIN.
- Normalized strings are stable and lowercase/case-folded.
- Missing values become empty strings or empty tuples, never the string `None`.
- Product records are immutable after Agent initialization.

#### SessionState

```text
SessionState
  profile: dict
  mode: buying | browsing | override | boundary | unknown
  category: str | None
  constraints: list[str]
  old_preference: str | None
  seen_asins: set[str]
  asked_attributes: list[str]
  last_candidate_count: int
  last_message: str                     optional when respond passes it directly
```

Invariants:

- Exactly one state object per active session ID.
- Constraints are normalized, unique, and ordered by disclosure time.
- `old_preference` is removed on override but other constraints remain.
- `seen_asins` contains the valid ASINs returned in the current scoring epoch.
  The explicit Intent Override transition starts a new epoch because the
  evaluator ignores otherwise-correct recommendations before that turn.
- No mutable collection is shared across sessions.

#### ParsedTurn

```text
ParsedTurn
  detected_mode: str | None
  category: str | None
  additions: list[str]
  replacement: str | None
  is_no_preference: bool
```

The parser converts a raw customer message into state operations. Parsing should
not retrieve or rank products directly.

#### RankedCandidate

```text
RankedCandidate
  product_index: int
  positional_matches: int
  exact_matches: int
  category_match: int
  bm25_rank: int | sentinel
  rating_number: int
```

The final implementation does not need to instantiate this type; it may build a
sorting tuple directly.

### 10.4 Recommended function layout inside `starter/agent.py`

Keep functions short enough that Person B can review behavior against this
contract. Recommended layout:

```text
Imports and constants
  TOKEN_RE, STOPWORDS, MATERIAL_RE, COLOR_RE, allowed attributes

Pure normalization helpers
  _text(value)
  _normalize(value)
  _terms(text)
  _flatten_values(value)
  _clean_constraint(value, limit)

Catalog-derived signature helpers
  _searchable_text(product)
  _coarse_category(categories)
  _intent_signature(product)

Message parser
  _parse_turn(user_message)

Agent
  __init__
  _build_indexes
  reset
  _update_state
  _signature_candidates
  _bm25_candidates
  _rank_candidates
  _fill_unseen
  _question
  respond
```

Responsibilities:

- `_text`: flatten scalar/list/dict catalog values without losing keys.
- `_normalize`: collapse whitespace, case-fold, and strip template punctuation.
- `_terms`: tokenize safe lexical query terms and remove stopwords.
- `_intent_signature`: derive no more than the published hard/soft constraint
  count; never inspect session labels.
- `_parse_turn`: recognize known templates, then return a neutral unknown result
  for unfamiliar text.
- `_update_state`: apply parsed operations in one place so override behavior is
  testable.
- `_signature_candidates`: perform exact reverse-index lookup without throwing
  away the current pool when a lookup misses.
- `_bm25_candidates`: return a sufficiently large ranked fallback list, such as
  100–250 candidates, rather than only the final ten.
- `_rank_candidates`: create deterministic tuples and sort once.
- `_fill_unseen`: enforce validity, response uniqueness, cross-turn coverage,
  and `top_k`.
- `_question`: decide only message/attribute; it must not mutate retrieval state.
- `respond`: orchestrate the helpers and build the public response.

### 10.5 Initialization lifecycle

`Agent.__init__` should execute this sequence once:

1. Resolve and validate the catalog path.
2. Create the in-memory SQLite connection and FTS table.
3. Initialize product arrays and reverse-index dictionaries.
4. Stream the catalog line by line; do not load raw JSON lines into a second
   unnecessary copy.
5. For each product:
   - validate/convert the ASIN;
   - flatten searchable fields;
   - derive coarse category;
   - derive the ordered signature;
   - append the compact product record;
   - update category and constraint reverse indexes; and
   - add one FTS row to the current batch.
6. Commit the SQLite batch inserts.
7. Store the catalog ASIN set for output validation.
8. Initialize the empty session dictionary.
9. Optionally record initialization duration for README evidence without printing
   per-product logs.

Initialization acceptance criteria:

- Exactly 50,000 unique ASINs are indexed.
- A missing or malformed optional field does not abort startup.
- A missing catalog file raises a clear actionable error.
- No network connection occurs.
- No derived artifact is written into the repository.

### 10.6 State-transition contract

| Current state | Incoming message | Required state operation | Next state |
|---|---|---|---|
| New | Buying opening | Set category; add first hard constraint | Buying |
| New | Browsing opening | Set category; no constraint yet | Browsing |
| New | Category plus old preference | Set category and `old_preference`; do not rank by the explicitly stale value | Override candidate |
| Any | Constraint reply | Append each new normalized value in message order | Same mode |
| Override candidate | Explicit override | Remove old preference only; add replacement | Override |
| Any | No-preference reply | Add no constraint; keep pool; mark boundary when appropriate | Boundary/current |
| Any | Unknown message | Preserve existing state; make raw text available to BM25 | Current/unknown |

Additional rules:

- A repeated constraint is ignored rather than appended twice.
- A category change clears category-dependent soft preferences only if the message
  explicitly indicates replacement. Do not erase state based on a weak lexical
  guess.
- An override replacement already present in constraints remains once.
- The explicit override starts a new recommendation-eligibility epoch; clear or
  re-scope pre-override seen ASINs so an ignored early target can be shown again.
- A no-preference response is not negative evidence against products that contain
  the named attribute.
- State updates occur before retrieval on each turn.

### 10.7 Candidate generation contract

Candidate generation is a union of precision and recall routes.

#### Precision pool

1. Begin with the exact category bucket when present; otherwise begin with all
   products.
2. For each active constraint, retrieve its exact reverse-index set.
3. Intersect only when the result remains non-empty.
4. Keep the previous pool when the reverse lookup is missing or the intersection
   empties the pool.
5. Retain the constraint as a later soft scoring feature even when it cannot
   safely narrow.

#### BM25 pool

Build a lexical query from:

```text
active category + active constraints + latest non-template message terms
```

Use quoted sanitized tokens joined with `OR`, as in the starter. Retrieve more
than ten candidates so fusion can recover products outside the exact pool.

#### Combined pool

Use the union of:

- narrowed precision candidates;
- top BM25 candidates; and
- category candidates when the other routes return too few.

If the union is still empty, fall back to deterministic global catalog order or
rating-count order. An empty response should be practically impossible.

### 10.8 Exact ranking contract

For every candidate, calculate:

```text
positional_matches = count of constraints whose observed order matches the
                     corresponding product-signature position

exact_matches      = count of active constraints appearing anywhere in the
                     product signature

category_match     = 1 when coarse category matches, else 0

bm25_rank          = zero-based rank from the BM25 pool, or a large sentinel

popularity         = rating_number, used only after relevance signals
```

Recommended ascending Python sort key:

```python
(
    -positional_matches,
    -exact_matches,
    -category_match,
    bm25_rank,
    -rating_number,
    parent_asin,
)
```

Do not multiply relevance by popularity. A highly popular but irrelevant item
must not outrank an exact signature match. This tie-break approach also avoids
inventing an RRF constant that cannot be validated during the sprint.

Scenario-specific interpretation:

- **Buying:** the first observed constraint is expected at the first hard
  position; positional matching should dominate immediately.
- **Browsing:** after the first `other` reply, the first two observed constraints
  correspond to the beginning of the ordered signature.
- **Override:** the old preference may match a soft signature position; after
  replacement, it must stop contributing while the new hard constraint becomes
  dominant.
- **Boundary:** the first no-preference reply contributes nothing; ranking should
  continue using category, previous evidence, and unseen coverage.

### 10.9 Question and response contract

Default ambiguous-turn response:

```python
{
    "message": (
        "What other requirement matters most—material, color, fit, style, "
        "budget, or intended use?"
    ),
    "ask_attribute": "other",
    "recommendations": ranked_unseen_top_k,
    "usage": {"prompt_tokens": 0, "completion_tokens": 0},
}
```

Use `ask_attribute=None` when:

- `turn >= 10`;
- the question policy has exhausted meaningful clarifications; or
- the implementation intentionally avoids a redundant question after receiving
  all available signature constraints.

Possible no-question message:

```text
Here are the strongest matches based on your current requirements.
```

The natural message need not enumerate recommendations. The evaluator scores the
structured recommendation list.

### 10.10 Worked behavior examples

These examples use placeholders and must not introduce real public target ASINs.

#### Browsing flow

```text
Turn 1 customer:
  I'm looking for Shirts Casual Button-Down Shirts, but I'm still exploring.

Agent state after parsing:
  mode=browsing
  category="shirts casual button-down shirts"
  constraints=[]

Agent response:
  asks `other`
  returns 10 category/BM25 candidates

Turn 2 customer:
  For that, what matters is: cotton; Button closure.

Agent state after parsing:
  constraints=["cotton", "button closure"]

Agent response:
  exact ordered signature matches first
  unseen BM25 matches fill remaining positions
  asks `other` again only if ambiguity remains
```

#### Intent Override flow

```text
Turn 1 customer:
  I'm looking for Jackets. lightweight casual style

Agent state:
  mode=override candidate
  old_preference="lightweight casual style"

Later customer:
  Actually, ignore my earlier preference. What I need is: leather.

Correct update:
  remove "lightweight casual style"
  preserve any separately disclosed color/fit/category constraints
  add "leather"

Incorrect update:
  clear the entire conversation state
```

#### Boundary flow

```text
Customer:
  I don't have a preference for other; please use your judgment.

Correct update:
  do not add a constraint
  do not remove products that have an "other" attribute
  return unseen candidates
  ask again on the next eligible turn
```

### 10.11 Detailed test matrix

Person B should prioritize the P0 tests. P1 tests are added only if P0 is green
before the integration gate.

| Priority | Test | Setup | Expected result |
|---|---|---|---|
| P0 | Reset required | Call `respond` before `reset` | Clear `RuntimeError` or documented failure |
| P0 | Response schema | One valid reset/respond | All required keys and correct types |
| P0 | Valid ASINs | Mini catalog plus response | Every result belongs to mini catalog |
| P0 | Unique current list | Candidate ties | No repeated ASIN in response |
| P0 | Cross-turn dedupe | Two turns in one scoring epoch, no target stop | Second list excludes first list |
| P0 | Override eligibility epoch | Recommend candidates before and after explicit override | Post-override results may revisit pre-eligibility candidates but remain unique afterward |
| P0 | Session isolation | Interleave two session IDs | Constraints/seen sets never cross |
| P0 | Buying parse | Buying opening | Category and hard constraint stored |
| P0 | Browsing parse | Browsing opening | Category stored, no fabricated constraint |
| P0 | Constraint accumulation | Two clarification replies | Ordered unique constraints retained |
| P0 | Override | Old plus independent constraints, then replacement | Only old value removed |
| P0 | Boundary | No-preference reply | Pool remains valid; no false negative slot |
| P0 | Empty lexical query | Empty/stopword message | Valid fallback recommendations |
| P0 | Missing metadata | Products with null fields | Initialization and response succeed |
| P0 | Determinism | Two identical Agent runs | Same recommendation order |
| P1 | Exact signature priority | One exact and several popular near matches | Exact product ranks first |
| P1 | Failed intersection | Unknown constraint | Prior candidate pool retained |
| P1 | Top-k respect | `top_k` below catalog size | Exact requested count when available |
| P1 | Turn 10 | Final turn | Valid recommendations; no unnecessary question |
| P1 | Full public evaluator | Official catalog/public set | Completes with no exceptions |

Mini-catalog tests should use `tempfile.TemporaryDirectory()` and generated JSONL
fixtures. Do not depend on the 58 MB decompressed catalog for every unit test.

### 10.12 Performance and operational requirements

These are practical targets, not competition-enforced limits:

- Initialization: under 60 seconds on the submission machine.
- Average `respond()` latency: under 250 ms for the offline route.
- Memory: comfortably below 500 MB.
- Network calls: zero during initialization and response.
- Model tokens: zero.
- Model/API cost: zero.
- Disk writes during evaluation: only the evaluator's documented results output.
- Logging: concise; do not print 50,000 product rows or customer profiles.

If measurements exceed these targets but the evaluator completes reliably, report
the actual numbers honestly rather than performing risky late optimization.

### 10.13 Final repository shape

The frozen repository should look approximately like:

```text
shopping-copilot/
  CLAUDE.md                       team execution and technical decisions
  README.md                       public project documentation
  NOTES.md                        evaluator facts and design rationale
  ABLATION.md                     measured experiments
  DEVPOST.md                      copy-ready submission text
  DEMO_SCRIPT.md                  timed three-minute narration
  DATA_ATTRIBUTION.md             organizer/upstream attribution
  SHA256SUMS                      frozen-asset checksums
  catalog.jsonl.gz                official catalog archive
  requirements.txt                actual dependencies only
  starter/
    __init__.py
    agent.py                      only production Agent implementation
  evaluator/                      unmodified official evaluator
  data/
    README.md
    public_set.jsonl              unmodified public development labels
    catalog.jsonl                 local ignored decompressed catalog
  docs/                           organizer contracts and rules
  tests/
    test_evaluator.py             organizer tests
    test_agent.py                 participant contract/state tests
```

Do not track:

```text
.env
data/catalog.jsonl
results.json
API response caches containing sensitive prompts
private/final evaluation data
```

### 10.14 Collaboration handoff protocol

Long explanations in chat slow the sprint. Use this compact status format at
minutes 45, 55, 80, 95, 110, and 160:

```text
[ROLE] [MINUTE] [COMMIT]
DONE: <one sentence>
RESULT: <score/test/video status>
BLOCKER: <none or one concrete blocker>
NEXT: <one next action before next gate>
NEED FROM PARTNER: <none or one concrete item>
```

Example:

```text
AGENT 55 a1b2c3d
DONE: Signature route, state, dedupe, and BM25 fallback run end-to-end.
RESULT: Smoke test green; full evaluator not yet complete.
BLOCKER: None.
NEXT: Tune override positional ranking while RC1 is evaluated.
NEED FROM PARTNER: Overall and per-scenario RC1 metrics.
```

Metric handoff format:

```text
COMMIT: <sha>
HIT@10: <value>
MRR: <value>
MTTC: <value>
EFFICIENCY: <value>
TECHNICAL SCORE: <value>
BUYING HIT/MRR/MTTC: ...
BROWSING HIT/MRR/MTTC: ...
OVERRIDE HIT/MRR/MTTC: ...
BOUNDARY HIT/MRR/MTTC: ...
RUNTIME: ...
EXCEPTIONS: 0
```

Handoff rules:

- A commit hash is required for every metric claim.
- "It works on my machine" without a command and commit is not evidence.
- A blocker message includes the failed command and first actionable error, not a
  full terminal dump.
- The partner receiving a blocker acknowledges it, but does not abandon their
  own exclusive work unless it threatens the next hard gate.
- Feature ideas discovered after code freeze go into README future work, not the
  Agent.

### 10.15 Detailed submission evidence package

Retain the following locally even when ignored by Git:

```text
submission-evidence/
  frozen_commit.txt
  public_results.json
  public_metrics_summary.txt
  test_output.txt
  environment.txt
  runtime.txt
  video_url.txt
  devpost_url.txt
  submission_confirmation.png
```

Suggested environment record:

```text
Date/time and timezone
Frozen Git commit and tag
Operating system
Python version
CPU and memory
Dependency versions
Catalog SHA-256
Evaluator command
Total evaluator runtime
External network dependency: none
Model/API: none
Prompt/completion tokens: 0/0
Estimated model cost: $0
```

The evidence directory need not be committed. It exists so either teammate can
answer organizer questions without reconstructing facts from memory.

### 10.16 Final quality rubric

Before calling the final product complete, score it internally:

| Area | Pass condition |
|---|---|
| Correctness | Required Agent interface; valid unique ASINs; isolated state; no exceptions |
| Retrieval | Measured improvement over baseline; exact signatures prioritized; fallback never empty |
| Dialogue | Buying/Browsing/Override/Boundary handled; recommendations and questions coexist |
| Rules | Catalog/public labels/evaluator unchanged; no secret or private data committed |
| Reproducibility | Fresh setup follows README and reproduces evaluator run |
| Evidence | Metrics tied to frozen commit; ablations and environment recorded |
| Communication | Architecture, decisions, limitations, and contributions are understandable |
| Demo | Public, ≤3 minutes, complete trace, metrics, and reproduction shown |
| Submission | Public repo and video links work; Devpost confirmation captured |

Any failure in Correctness, Rules, Reproducibility, Demo, or Submission blocks
completion. A lower-than-target score does not block submission if the system is
valid, measured, and clearly explained.

### 10.17 Explicit non-goals for the frozen sprint product

Do not accidentally expand the final product into any of the following:

- production web service or frontend;
- user authentication or persistent cross-user profiles;
- external vector database;
- live catalog updates;
- real Amazon purchasing or product-page integration;
- multimodal image understanding;
- spelling correction or ASR cleanup;
- model training or fine-tuning;
- asynchronous/multi-user load handling;
- private-label inference;
- generation of new ASINs;
- claims of private-set performance before that set is released.

These are appropriate future-work items only when they support the product story.

---

## 11. Workshop-brief traceability and complete coverage plan

This section maps the complete “Shopping Copilot: AI Conversational Search and
Recommendations” workshop brief to the implementation, evidence, and submission.
It exists to prevent two opposite mistakes:

1. omitting a judged requirement because it was not directly scored by the local
   evaluator; or
2. claiming an aspirational component such as dense retrieval or an LLM when the
   frozen build does not actually contain it.

### 11.1 Source precedence and requirement interpretation

Use this precedence whenever sources appear inconsistent:

1. `docs/submission_rules.md`, `docs/competition_specification.md`,
   `docs/final_evaluation_faq.md`, and `docs/agent_api_contract.json`;
2. the unmodified official evaluator and evaluation configuration;
3. the published workshop brief and any recorded Q&A clarification; and
4. this implementation plan.

The official kit explicitly allows keyword, rule-based, dense, hybrid, local
model, and external API approaches; an external LLM is optional. Therefore,
workshop phrases such as “LLM semantic ranking” describe a desired architecture
direction, not permission to misrepresent the actual offline solution. Likewise,
items listed as “in scope” are allowed techniques, not a requirement to include
every technique regardless of measured value.

If the webinar Q&A contains a clarification not present in the written brief,
record the exact timestamp and a short paraphrase in `NOTES.md`. Do not copy
video, slides, or long transcripts into the repository, and do not allow a
paraphrased Q&A answer to override the official written rules without organizer
confirmation.

Requirement classifications used below:

- **H — hard:** interface, data, scope, security, or submission rule; blocks
  release when unmet.
- **J — judged:** affects judging quality and must have honest evidence, but does
  not prescribe one implementation.
- **A — allowed/aspirational:** an endorsed method to evaluate experimentally;
  adopt only when measured and reproducible.
- **D — deliverable:** required written, repository, video, or link artifact.

### 11.2 Complete requirements coverage matrix

| ID | Workshop requirement | Class | Current coverage | Required evidence or remaining action |
|---|---|---:|---|---|
| B1 | Explain why static keyword search fails for evolving, ambiguous intent | J | Covered in Sections 6, 9.9, and 9.10 | README, Devpost, and demo must connect ambiguity and intent change to user/business value rather than only describing code. |
| B2 | Build a next-generation shopping agent with cognitive understanding, runtime agility, and commercial efficiency | J | Partly covered | Demonstrate state updates, route choices, early conversion, deterministic runtime, and cost; avoid unsupported claims of cognition. |
| A1 | Detect Buying versus Browsing intent | J | Implemented in `0ea7705`: distinct `precision` and `discovery` policies with safe route traces | Direct tests prove mode and route selection; `demo.py` shows discovery and override recovery. |
| A2 | Buying route locks hard constraints for high precision | J | Catalog-signature intersection implements precision | Add explicit route-policy test and trace showing hard constraints narrow only when the intersection stays non-empty. |
| A3 | Browsing route preserves diversity and supports semantic/cross-category discovery | J/A | Implemented category/BM25/profile recall plus conservative store/title-family coverage; neural semantics absent | Direct family-coverage test and ablation pass. README explicitly limits semantic/cross-category claims. |
| A4 | Multi-route keyword, category, and vector retrieval | A | Exact, category, keyword/BM25, profile, and fallback routes shipped; dense vector route not adopted | Officially optional. `ABLATION.md` records feasibility blockers and never calls BM25 dense retrieval. |
| A5 | LLM semantic ranking after retrieval | A | Not adopted in the frozen offline build | Officially optional. Runtime API/model/tokens/cost/network are none/none/0/$0/none and are disclosed consistently. |
| D1 | Accumulate information across turns | J | Implemented with ordered normalized constraints | Unit test two or more clarification replies and a trace in the demo. |
| D2 | Erase/rewrite stale slots on Intent Override | J | Implemented, including override eligibility epoch | Test that only the stale value is removed, valid constraints remain, and pre-override ignored targets may be reconsidered. |
| D3 | Detect candidate-pool overload and proactively clarify | J | Candidate count drives `ask_attribute="other"` | Record candidate-count behavior and show a Browsing or Boundary trace where clarification reduces ambiguity. |
| D4 | Stay within ten turns and reduce unnecessary conversational load | H/J | Implemented; evaluator enforces ten turns | Turn-10 contract test plus MTTC and Efficiency evidence. Never return an eleventh response. |
| C1 | Distill accumulated dialog into short-term session context | J | Implemented as category, constraints, mode, seen items, and question history | Document the state schema and show before/after state in one trace without exposing private profile data. |
| C2 | Use the supplied user profile for personalization | J/A | Implemented in `0ea7705` as deep-copied, normalized, low-confidence discovery evidence | Profile immutability, isolation, on/off ranking test, public ablation, and dynamic truncation are recorded. |
| C3 | Maintain “long-term” preference context | J/A | Input profile represents prior preferences; cross-session persistence is intentionally absent | Use only the anonymized supplied profile as a soft prior. Do not invent user identity, persist data across isolated sessions, or claim learned long-term memory. |
| C4 | Runtime workflow re-orchestration and strategy alignment | J/A | Four explicit deterministic policies use mode, evidence, pool size, and turn | Safe route histories are asserted in tests and printed only by the demo helper. |
| C5 | Dynamic truncation, weighting, or slot decay | A | Slot source/confidence/turn/hard metadata and profile truncation are implemented | Hard customer constraints never decay; profile evidence is removed after two explicit constraints and directly tested. |
| E1 | Hit Rate@K / catalog coverage | H/J | Official evaluator result recorded | Report Hit Rate@10 overall and for all four scenarios, tied to the exact commit. |
| E2 | MRR / ranking precision | H/J | Official evaluator result recorded | Report MRR and explain ordering signals; do not claim optional numeric recommendation scores affect evaluation. |
| E3 | MTTC / turns to conversion | H/J | Official evaluator result recorded | Report MTTC and Efficiency; demo must show simultaneous question plus recommendations. |
| E4 | Combined TechnicalScore | H/J | Formula and baseline covered | Reproduce with the unmodified evaluator and retain the full `results.json` locally. |
| S1 | Backend/headless only; UI is out of scope | H | Covered | Do not spend sprint time on a UI. Demo the evaluator, API contract, traces, and results. |
| S2 | No full-parameter foundation-model fine-tuning | H | Covered | Dependency/model audit must show no training or fine-tuned artifact. Prompt iteration or local scoring is allowed when disclosed. |
| S3 | No heavy external vector database; execution is in memory | H | Covered | Inspect dependencies and network calls; any vector experiment must use an in-memory local index. |
| S4 | Text and structured metadata only; no multimodal processing | H | Covered | Demo and code must not depend on product images, audio, OCR, or vision models. |
| S5 | Catalog is immutable; no mock/injected ASINs | H | Covered and hash-audited | Verify catalog checksum before and after evaluation; validate every output against loaded catalog IDs. |
| S6 | Inputs may be treated as pre-cleaned; no typo/ASR pipeline required | A | Covered as an explicit non-goal | Do not claim spelling/voice robustness. Unknown text still receives a safe lexical fallback. |
| S7 | Static catalog/pricing/category tree during the event | H | Architecture assumes one initialization | Load and index once; do not implement live refresh or mutate product records. |
| S8 | Isolated single-user sessions; concurrency stress not required | H | Per-session isolated state implemented | Interleaved-session test proves no shared mutable state; do not claim production concurrency guarantees. |
| R1 | Frozen 50,000-item Amazon Reviews 2023-derived catalog | H | Indexed and checksum-verified | README must identify the exact category, checksum procedure, source attribution, and decompression command. |
| R2 | 200 public development sessions | H | Used through official evaluator | Metrics must be labeled “public development”; do not embed labels or target ASINs in Agent code. |
| R3 | 800 private final sessions with separate users and targets | H | Generalization constraint covered | Never claim private performance early; after release, evaluate the frozen commit without changes and retain evidence. |
| R4 | Weak BM25 starter and deterministic evaluator | H | Baseline reproduced | Record baseline commit, exact command, baseline metrics, and final delta in `ABLATION.md`. |
| R5 | Published Agent interface and machine-readable contract | H | Implemented | Contract tests cover required fields, types, allowed attributes, valid unique ASINs, `top_k`, and honest usage. |
| R6 | Checksum, data docs, evaluation config, and submission rules | H | Present | Clean-clone audit verifies every documented path and checksum before submission. |
| R7 | No organizer-provided API key, model access, or credits | H | Offline RC has zero API dependency | If W3/W4 uses a service, use environment variables, separate spend limits, honest cost, and a tested offline fallback. |
| R8 | Full upstream Amazon dataset reconstruction is unnecessary | A | Covered | Use only the frozen kit unless a legally accessible upstream experiment has a documented need and license. |
| P1 | Devpost explains approach and problem fit | D | Complete copy in `DEVPOST.md` | External Devpost publication/preview requires the team account. |
| P2 | Devpost lists development tools | D | VS Code, Git/GitHub, Python, SQLite, Codex, and terminal tooling listed | Only tools actually used are named. |
| P3 | Devpost lists APIs | D | Runtime APIs accurately listed as none | OpenAI is not presented as a runtime API. |
| P4 | Devpost lists libraries/frameworks | D | Python standard library, SQLite FTS5, and unittest listed | No optional dependency is claimed. |
| P5 | Devpost lists datasets/assets | D | Frozen catalog/public set, Amazon Reviews 2023 attribution, and original demo assets listed | No unlicensed product/media asset is included. |
| P6 | Public repository with well-structured, commented code | D | Agent is structured and the repository was verified public while signed out | Preserve public access through judging. |
| P7 | README overview, setup, installation, reproduction, limitations, improvements, contributions | D | Complete outline exists | Fresh-clone reproduction and two-person content audit are release blockers. |
| V1 | Short end-to-end demo video | D | A verified 2:53.8 upload-ready 1080p video, captions, thumbnail, executable `demo.py`, and captured `DEMO_OUTPUT.md` are ready | YouTube upload remains an external-account action. |
| V2 | Video is public on YouTube and linked from Devpost | D | Pending external action | Verify while signed out and capture URL/confirmation. |
| V3 | Backend walkthrough is acceptable without a UI | D | Covered | Use terminal/API/evaluator evidence; do not add a cosmetic UI. |
| V4 | No unauthorized trademarks or copyrighted content | H/D | Completed in the prepared video using original rendered evidence and narration | No Amazon logos/pages, webinar clips, product images, copyrighted music, or third-party footage; dataset names appear only for factual attribution. |
| J1 | Technical Execution, 35% | J | Strong measured RC | Evidence: architecture, code review, tests, deterministic evaluator, fallback, runtime, and commit-linked metrics. |
| J2 | Innovation & Problem Insight, 20% | J | Sequential-experiment framing covered | Explain why each turn jointly selects products and information; distinguish this insight from generic “AI search.” |
| J3 | Impact & Relevance, 20% | J | Completed with an evidence-bounded user/business narrative | Fewer turns and better rank are tied to reduced search friction, discovery, conversion opportunity, and applicability beyond one dataset without inventing business results. |
| J4 | Feasibility & Practicality, 15% | J | Offline RC is strong | Report CPU, memory, initialization, response/evaluation latency, dependencies, network reliance, token use, cost, and fallback. |
| J5 | Presentation & Communication, 10% at finals | J | Script exists | Prepare coherent problem→insight→architecture→demo→evidence→impact story and Q&A answers for every non-obvious design choice. |

No row may be changed to “covered” because it appears in prose alone. Coverage
requires code, test output, measured evidence, or a completed external artifact.

### 11.3 Preserve the measured offline release candidate

The current measured Person A candidate is:

```text
branch: feat/agent
commit: 00f1e41
runtime dependencies: Python standard library and SQLite FTS5
external model/API at runtime: none
public Hit Rate@10: 1.000000
public MRR: 0.707655
public MTTC: 2.120000
public TechnicalScore: 0.889896
```

This commit remains the control for every enhancement. Do not rewrite or force
move it. Run workshop-alignment experiments on named branches from this commit,
and retain the offline candidate when an experiment fails its adoption gate.
The very high public result makes private-set generalization, honesty, and
reproducibility more important—not less. It must never be described as private
performance or as proof that optional dense/LLM components are unnecessary in
real-world commerce.

### 11.4 Workshop-alignment enhancement tracks

These tracks close the genuine gaps in the coverage matrix. They are ordered by
benefit-to-risk. Each track is independently measured; do not bundle them into
one unexplainable score change.

#### W1 — Make intent routing behaviorally explicit

Goal: move from “mode is parsed” to distinct route policies.

Buying route:

- treat disclosed hard constraints as safe intersections when non-empty;
- prioritize exact signature position and category;
- ask only while ambiguity remains; and
- retain BM25 as a recall fallback.

Browsing route:

- treat inferred/profile preferences as soft signals, never destructive filters;
- use broader category and lexical pools;
- diversify the ten returned products by normalized brand/title family when
  doing so does not remove the target from the measured Top 10; and
- ask an early structured clarification when the pool exceeds the validated
  ambiguity cutoff.

Override and Boundary routes:

- recompute after selective stale-slot erasure;
- start a new recommendation-eligibility epoch on explicit override;
- treat “no preference” as neutral evidence; and
- prefer unseen coverage while clarification remains useful.

Acceptance evidence:

- unit tests show each opening selects the expected route;
- a route trace records mode, candidate count, chosen route, and question policy;
- no empty recommendation lists or contract regressions;
- overall and per-scenario evaluator results beat or tie the control within the
  predeclared noise/regression tolerance.

#### W2 — Personalized context distillation without unsafe persistence

Goal: use the supplied anonymized profile honestly.

At `reset()` derive a compact, immutable profile context from only documented
fields such as `preference_tags` and `summary`. Normalize it once, keep it inside
that session, and use it as a soft lexical/dense prior. Do not:

- infer identity or demographics;
- persist state across evaluator sessions;
- treat generic tags such as “comfort” as hard filters;
- log full profiles in normal output; or
- claim the Agent learns a durable user model when it only consumes the supplied
  prior profile.

Acceptance evidence:

- profile input remains unmodified;
- interleaved sessions do not leak tags;
- an ablation compares profile-off versus profile-on;
- the 40-session holdout and weakest scenario do not regress materially; and
- README describes this as supplied-profile conditioning, not persistent memory.

#### W3 — Reproducible in-memory vector retrieval

Goal: test the workshop’s dense/vector route without an external vector DB.

Before implementation, decide and document:

1. embedding model/provider and license;
2. exact catalog text composition;
3. vector dimension and numeric dtype;
4. artifact size, checksum, storage/distribution method, and regeneration path;
5. query-time network requirement;
6. dependency versions and supported Python version;
7. one-time generation cost and expected per-evaluation cost; and
8. behavior when the vector asset or service is unavailable.

Implementation contract:

- generate catalog vectors once, never once per session;
- keep the search index in process memory;
- normalize vectors and use deterministic cosine similarity;
- create distinct dense-message and dense-profile candidate lists;
- fuse exact signature, BM25, category, dense-message, and dense-profile ranks
  with a documented method such as RRF;
- keep exact hard constraints above semantic similarity for Buying; and
- fall back to the measured offline Agent on any load, network, or model failure.

Do not commit an oversized or unlicensed artifact merely to satisfy a diagram.
If the asset cannot be reproduced from the public bundle or distributed within
the submission rules, W3 fails feasibility even if its local score is higher.

Acceptance evidence:

- retrieval ablation by route;
- deterministic catalog-vector checksum;
- measured initialization, response latency, memory, tokens, and cost;
- fresh-machine setup test; and
- measurable holdout or Browsing improvement without a damaging Buying,
  Override, or Boundary regression.

#### W4 — Structured LLM semantic ranking

Goal: evaluate the optional semantic-ranking stage without allowing it to make
the system brittle or unreproducible.

Design:

1. retrieve a bounded candidate set locally first;
2. ask the model for structured query/target attributes and confidences, or for
   a schema-constrained comparison over the bounded candidates;
3. validate every returned field and ignore unknown product IDs;
4. combine semantic evidence with exact constraints—never let a fluent output
   override an explicit hard mismatch;
5. cache deterministic development calls by prompt/model/schema hash;
6. report actual prompt/completion tokens, latency, retries, and estimated cost;
7. enforce a request/cost ceiling and a timeout; and
8. fall back to the offline ordering on exception, timeout, invalid JSON, quota,
   or missing credentials.

Security and reproducibility:

- credential name is documented, value is never committed;
- `.env` and caches containing sensitive prompts remain ignored;
- model identifier and parameters are frozen;
- prompts are versioned in source or a small declared config;
- the demo and Devpost name the API only if the frozen runtime uses it; and
- the evaluator still receives honest `usage` values.

Acceptance evidence:

- cache-hit and cache-miss tests;
- invalid-response and no-key fallback tests;
- an LLM-off versus LLM-on ablation;
- no lower reliability than the control; and
- a score/quality gain large enough to justify added latency, cost, network, and
  setup risk.

#### W5 — Adaptive orchestration, dynamic truncation, and slot confidence

Goal: make runtime adaptation explicit and testable rather than an unbounded
collection of heuristics.

Extend each non-hard slot conceptually with:

```text
value
source: opening | clarification | profile | inferred
confidence
introduced_turn
last_confirmed_turn
hard: bool
```

At every turn, the orchestrator chooses from a small deterministic policy table:

```text
parse/update state
  → compute candidate count and evidence confidence
  → select precision, recall/diversity, override-recovery, or fallback route
  → decide whether another clarification has positive expected value
  → rank and return recommendations in the same response
```

Rules:

- explicit customer hard constraints never decay;
- an explicit override erases the named stale slot immediately;
- supplied profile preferences and inferred soft slots may decay only after a
  predeclared number of contradictory/unconfirmed turns;
- dynamic truncation drops low-confidence soft evidence before it drops catalog
  candidates that satisfy hard constraints;
- the final turn never asks an unusable follow-up; and
- all thresholds have a reason and an ablation record.

Acceptance evidence:

- transition-table tests cover accumulation, contradiction, override, Boundary,
  decay, and turn 10;
- policy decisions are deterministic;
- no scenario regression is hidden by a higher overall score; and
- complexity is retained only if it improves measured quality or produces a
  materially stronger real-world demonstration.

### 11.5 Experiment and adoption protocol

Use the same protocol for W1–W5:

1. Branch from the frozen control commit and name one hypothesis.
2. Declare the expected metric/scenario improvement and possible failure mode.
3. Keep a fixed 160-session development split and an untouched 40-session
   holdout. Preserve scenario representation and record the split procedure.
4. Run contract tests and the development evaluator.
5. Record overall Hit Rate@10, MRR, MTTC, Efficiency, TechnicalScore, all four
   scenario tables, runtime, memory when relevant, tokens, and cost.
6. Reject changes smaller than the predeclared noise threshold unless they close
   a hard correctness/reliability gap.
7. Open the holdout only for a candidate that passed the development gate.
8. Compare against commit `00f1e41`, not against memory or an uncommitted tree.
9. Commit accepted and rejected results to `ABLATION.md`; rejected code need not
   be merged.
10. Freeze one measured candidate. Never combine individually positive changes
    without measuring the combination.

Additional adoption blockers:

- any invalid/empty response;
- any evaluator/public-label/catalog modification;
- secret exposure;
- undeclared network or dependency requirement;
- non-reproducible generated assets;
- major per-scenario regression;
- misleading documentation; or
- a component that cannot run from the submitted bundle.

### 11.6 End-to-end architecture contract

The complete architecture, when optional tracks are adopted, is:

```text
frozen catalog
  → immutable product records
  → exact category/constraint indexes + BM25 + optional dense vectors

profile + current message + prior session state
  → template/generic parser
  → selective state update and override erasure
  → intent/orchestration policy
  → precision candidates + lexical candidates + optional dense candidates
  → deterministic fusion
  → optional bounded structured semantic reranking
  → validity, family diversity, and scoring-epoch de-duplication
  → Top 10 recommendations + optional structured clarification + honest usage
```

Failure behavior is part of the architecture, not an afterthought:

- exact lookup miss keeps the last non-empty candidate pool;
- BM25 error falls back to category/global deterministic order;
- vector/model error falls back to the offline control route;
- malformed customer text is treated as soft lexical context;
- no-preference is neutral;
- missing optional catalog metadata never aborts initialization; and
- a response-construction error still produces a valid list from loaded ASINs.

### 11.7 Full verification matrix

Person B should add or retain tests/evidence for all applicable rows:

| Area | Required verification |
|---|---|
| Interface | Reset required; fields/types; allowed `ask_attribute`; valid unique ASINs; `top_k`; non-negative honest usage. |
| Catalog | Exactly 50,000 unique records; checksum unchanged; null/missing optional metadata safe; no writes or injected IDs. |
| Intent | Buying, Browsing, Override candidate, explicit Override, Boundary, and unknown-message routing. |
| State | Ordered accumulation; duplicate constraint suppression; selective erasure; eligibility-epoch reset; interleaved-session isolation. |
| Retrieval | Exact position priority; safe failed intersection; BM25 fallback; category/global fill; optional dense route and fusion ablations. |
| Dialogue | Clarification plus recommendations; overload question; neutral no-preference; useful question cutoff; no turn-10 question. |
| Personalization | Profile immutability and isolation; profile-off/on ablation if used; no hard filtering from weak profile tags. |
| Semantic model | Structured validation, cache, timeout, error/no-key fallback, token/cost accounting if used. |
| Metrics | Baseline reproduction; final overall/scenario metrics; deterministic repeat; frozen-commit hash; unmodified evaluator. |
| Performance | Initialization, average/p95 response latency when measurable, full evaluator runtime, peak memory estimate, dependency/network disclosure. |
| Generalization | Fixed holdout; no public ASIN/string hardcoding; separate-user/private-set caveat; no private result claim. |
| Security | `.env` ignored; secret-history scan; caches reviewed; no credential in logs, screenshots, video, or docs. |
| Reproduction | Clean clone; checksum; decompression; dependency installation; one evaluation command; expected outputs. |

### 11.8 Deliverable content and external-asset audit

#### Devpost

The final Devpost description must explicitly contain:

- how the system addresses evolving Buying, Browsing, Override, and Boundary
  behavior;
- the sequential-experiment insight and why it matters;
- actual development tools;
- actual runtime APIs, with “none” when appropriate;
- actual libraries/frameworks;
- the frozen dataset and any assets;
- architecture and fallback behavior;
- public development metrics labeled correctly;
- runtime, tokens, estimated cost, and network dependency;
- limitations and credible next steps;
- concrete team-member contributions;
- public repository URL; and
- public YouTube URL.

#### Public repository and README

The signed-out repository audit must prove:

- source files and required lightweight assets are present;
- code comments explain non-obvious behavior rather than restating syntax;
- README setup works from a clean clone;
- checksum and catalog setup commands are correct;
- one documented command reproduces evaluation;
- metrics match the frozen commit;
- limitations distinguish template-aware public evaluation from real customer
  language;
- data attribution and license/source links are present;
- contributions name what each person actually produced; and
- no ignored/private/evidence-only file was accidentally committed.

#### Demo video

The video acceptance checklist is:

- concise end-to-end execution, not slides alone;
- one Browsing flow and one Intent Override flow;
- aggregate and per-scenario metrics from the frozen commit;
- clear statement of runtime architecture, dependencies, cost, and fallback;
- readable terminal text with no `.env`, key, profile details, or sensitive path;
- original diagrams only;
- no Amazon or third-party logos, product-page captures, product images,
  webinar/slide footage, copyrighted music, or unlicensed media;
- dataset/source attribution in narration or closing frame;
- public YouTube visibility verified while signed out;
- URL placed in Devpost and tested; and
- final video file/URL captured in the local evidence package.

### 11.9 Judging-evidence plan

Do not expect the technical score to tell the whole story. Prepare explicit
evidence for every judging criterion.

#### Technical Execution — 35%

- architecture diagram matches frozen code;
- deterministic contract/state tests;
- baseline-to-final and per-scenario metrics;
- immutable in-memory indexing and graceful fallback;
- clean repository and reproducible command; and
- clear explanation of why exact signature position outranks popularity.

#### Innovation & Problem Insight — 20%

- frame the problem as sequential experiment design under a ten-turn budget;
- show that each response jointly chooses information and product exposure;
- explain eligibility epochs for Intent Override; and
- distinguish the approach from a generic chatbot or keyword wrapper.

#### Impact & Relevance — 20%

- identify user outcomes: less search friction, better discovery, quicker
  convergence, and graceful handling of changing needs;
- identify plausible stakeholder value without claiming unmeasured conversion or
  revenue lift;
- explain applicability to other structured catalogs; and
- acknowledge where real-world language, inventory, and policy would require
  more work.

#### Feasibility & Practicality — 15%

- quantify startup/evaluation latency and memory;
- disclose zero runtime model cost for the offline RC or actual cost for an
  adopted semantic route;
- describe network independence or tested fallback;
- show frozen, read-only data and deterministic output; and
- explain deployment assumptions and operational limitations honestly.

#### Presentation & Communication — 10% at finals

- rehearse a single coherent problem→insight→design→demo→evidence→impact story;
- keep every claim tied to visible code or a measured artifact;
- prepare answers for constants, route choice, template dependence, high public
  score, private generalization, profile use, cost, and failure behavior; and
- ensure both teammates can explain the whole system, not only their assigned
  files.

### 11.10 Resource and provenance inventory

The submission should preserve or link the authoritative resources named in the
workshop brief:

- participant repository:
  `https://github.com/TechJam2026/techjam-conversational-search`;
- participant-kit release:
  `https://github.com/TechJam2026/techjam-conversational-search/releases/tag/participant-kit`;
- original Amazon Reviews 2023 documentation:
  `https://amazon-reviews-2023.github.io/`;
- local organizer documents under `docs/`;
- `DATA_ATTRIBUTION.md`;
- `SHA256SUMS`; and
- the 28 Aug Shopping Copilot workshop recording, when available to the team.

Before submission, verify the upstream URLs still open, but do not silently
replace the frozen local kit from upstream. Record the local kit commit and
catalog checksum as the actual evaluated artifacts. Link to the recording only
when the organizer supplied a public/shareable URL; never upload or redistribute
the recording yourselves.

### 11.11 Final workshop-complete definition of done

In addition to Section 9.12, all of the following must be true:

- [x] Every row in Section 11.2 is marked with real evidence, an honest deferred
      limitation, or an allowed/non-applicable justification.
- [x] The README, Devpost, video, code, and metric files describe the same frozen
      architecture—offline or enhanced—with no phantom LLM/vector components.
- [x] Buying/Browsing detection, accumulation, Override, Boundary, overload
      clarification, and ten-turn behavior have direct test evidence.
- [x] Profile use is either implemented and ablated or explicitly disclosed as a
      future enhancement; merely storing it is not called personalization.
- [x] Vector and LLM stages are either reproducible, measured, disclosed, and
      fallback-safe, or honestly omitted under the official optional-model rule.
- [x] Hit Rate@10, MRR, MTTC, Efficiency, and TechnicalScore are tied to the
      frozen commit and shown overall plus per scenario.
- [x] Initialization/evaluation runtime, Python/environment, dependencies,
      network, tokens, cost, and memory estimate are recorded.
- [x] Catalog/public labels/evaluator are unchanged and no public/private target
      leakage exists.
- [x] The secret-history and ignored-file audits pass without printing secrets.
- [x] Dataset attribution and all actual tools/APIs/libraries/assets are disclosed.
- [ ] Video contains no unauthorized trademark, copyrighted, private, or secret
      material and is public on YouTube.
- [ ] Public repository and Devpost/video links work while signed out.
- [x] Each judging criterion has at least one concrete evidence item and both
      teammates can defend the design decisions.
- [ ] Final submission confirmation, frozen hash/tag, URLs, metrics, and
      environment evidence are retained locally.

Only then is the project fully covered against the workshop brief, official kit,
submission requirements, and judging matrix.

## 12. Integrated implementation record

This section records execution of the extended plan after the original
three-hour split ended and one teammate stopped active development. It
supersedes the historical “current RC” wording elsewhere without erasing the
control needed for honest comparison.

### 12.1 Selected implementation

```text
control Agent: 00f1e413f4e984cc9dbfd7e44f2e8c7a051b566f
enhanced Agent: 0ea7705f9e295855cadc85bd44141b841ff0685f
runtime: Python standard library + in-memory SQLite FTS5
external model/API/network: none / none / none
public Hit@10: 1.000000
public MRR: 0.709905
public MTTC: 2.120000
public Efficiency: 0.888000
public TechnicalScore: 0.890571
tests: 23 participant + 3 organizer = 26/26
```

W1 is implemented through explicit precision, discovery, override-recovery, and
fallback policies. W2 is implemented as deep-copied supplied-profile
conditioning with an on/off test and ablation. W5 is implemented through slot
provenance/confidence/turn metadata, safe route traces, eligibility epochs, and
dynamic truncation of low-confidence profile evidence. Conservative family
coverage applies only during unconstrained discovery.

W3 and W4 were evaluated against their feasibility gates and not adopted. The
official rules make dense and LLM stages optional; no reproducible licensed
vector/model artifact with a justified score gain was established. The final
artifacts consistently disclose category/signature/BM25/profile retrieval, no
dense or neural semantic claim, zero model tokens/cost, and no network reliance.

### 12.2 Completed internal deliverables

- `starter/agent.py`: integrated production Agent.
- `tests/test_agent.py`: routing, profile, diversity, state, failure, and
  contract tests.
- `ablate.py` and `ABLATION.md`: reproducible feature-off experiments and
  commit-linked results.
- `demo.py` and `DEMO_OUTPUT.md`: executable and captured end-to-end inference.
- `README.md`: setup, architecture, reproduction, metrics, limitations,
  attribution, contributions, and release procedure.
- `DEVPOST.md`: copy-ready submission narrative covering tools, APIs,
  libraries, data/assets, cost, impact, and limitations.
- `DEMO_SCRIPT.md`: timed live-demo plan, evidence list, IP/safety checks, and
  publication checklist.
- `SUBMISSION_STEPS.md`: exact external publication and post-release actions.
- `docs/audits/IntentCart_End_to_End_Demo.mp4`: ignored upload-ready 2:53.8
  video with matching captions, thumbnail, and checksum manifest.
- `NOTES.md`: evaluator/catalog facts, requirement coverage, performance,
  security, and final-evaluation boundary.

### 12.3 Verified evidence

- Protected catalog, public set, evaluator, and official docs are unchanged from
  `origin/main`.
- Catalog has 50,000 unique parent ASINs and both published checksums match.
- Two enhanced evaluator outputs are byte-identical with SHA-256
  `0faad255af1b1ad12fca5923fd124e0192594e88f348bc3e3bd5caa5f3cdad71`.
- Full Git-history secret-pattern scan reports zero matching blob paths without
  printing repository contents.
- `.env`, decompressed catalog, result JSON, caches, and vector artifacts remain
  ignored and untracked.
- Production Agent imports no public targets/scenarios and performs no network
  or environment-secret lookup.
- Local Markdown links, placeholder scan, private-path scan, compilation,
  `git diff --check`, and all 26 tests pass.
- The public repository and three upstream resource pages were verified from a
  signed-out web context on 2026-09-01.

### 12.4 External account actions still required

Code cannot create or submit a YouTube/Devpost entry without the team's accounts.
The unchecked external definition-of-done groups above therefore remain
intentionally open until the owner:

1. publicly uploads the prepared video named in `SUBMISSION_STEPS.md`;
2. publishes `DEVPOST.md` and inserts the public YouTube URL;
3. verifies repository/video/Devpost links while signed out; and
4. retains submission confirmation, URLs, and the exact `submission-v1` SHA.

All source, runtime, evidence, and copy required for those account actions is
present in the repository.
