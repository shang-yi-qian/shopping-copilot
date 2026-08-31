# IntentCart — Devpost Submission Copy

## Tagline

Stateful, route-aware conversational shopping that recommends and updates
context each turn—offline, deterministic, and cost-free at inference time.

## Public links

- Source repository: <https://github.com/shang-yi-qian/shopping-copilot>
- Demo video: insert the final public YouTube URL here and in Devpost's video
  field before submission.

## How the solution addresses the problem statement

The supplied problem identifies three weaknesses in static keyword search:
failure to distinguish Buying from Browsing, failure to accumulate or replace
intent across turns, and unnecessary conversational turns. IntentCart addresses
each weakness through explicit runtime routing, isolated structured state, and
simultaneous ranked retrieval plus proactive clarification.

IntentCart treats conversational shopping as sequential experiment design. Each
turn must rank the best products now, expose useful unseen alternatives, and ask
the question most likely to reduce uncertainty. This directly matches the
challenge's Hit@10, MRR, and mean-turns-to-conversion objectives.

## What it does

IntentCart is a headless Python Agent for the organizer's frozen 50,000-product
clothing catalog. Every response can return ten ranked parent ASINs and a
structured clarification question together.

It handles all four published behaviors:

- **Buying:** a precision policy locks catalog-supported hard requirements and
  ranks exact signature matches before broad lexical evidence.
- **Browsing:** a discovery policy combines category and BM25 recall with a weak
  supplied-profile prior and conservative product-family coverage.
- **Intent Override:** selective erasure removes the stale preference, preserves
  independent evidence, and begins a new recommendation-eligibility epoch.
- **Boundary:** no-preference answers remain neutral, so the Agent keeps useful
  recommendations without fabricating a negative constraint.

## How we built it

At startup the Agent reads the immutable catalog once and builds:

1. compact product records and deterministic popularity orders;
2. normalized category and catalog-signature reverse indexes; and
3. an in-memory SQLite FTS5 index over text and structured metadata.

`reset()` deep-copies the organizer's anonymous aggregate profile and creates an
isolated state. Every customer-stated constraint records its source, confidence,
introduction/confirmation turn, and hard/soft status. Profile tags and summary
are normalized into low-confidence terms; they can influence exploration but
never filter products and are truncated after two explicit current-session
constraints.

At each turn a deterministic orchestrator selects `precision`, `discovery`,
`override_recovery`, or `fallback`. Retrieval merges exact signatures, category,
BM25, the permitted profile prior, and deterministic category/global fallbacks.
Ranking protects exact evidence, then applies route-appropriate soft signals,
popularity, and stable ASIN tie-breaking. Unconstrained Browsing covers no more
than two products from one store/title family before deterministic backfill.

When the candidate pool exceeds the result capacity and a useful turn remains,
IntentCart asks `other` while still returning recommendations. Products shown in
the current eligibility epoch are not repeated. Exact misses keep the last
non-empty pool, FTS errors fall through to catalog order, and missing optional
metadata never aborts initialization.

## End-to-end demonstration

The repository includes both an executable demonstration and its captured
inference evidence:

```bash
python3 demo.py
```

The Browsing trace begins with 519 candidates, asks a clarification while
recommending, receives two composition constraints, narrows to one exact
candidate, and finds the target at rank 1 on turn 2.

The Intent Override trace shows the target at rank 1 before it is eligible,
processes the explicit replacement, increments the eligibility epoch, selects
`override_recovery`, and finds the eligible target at rank 1 on turn 3.

`DEMO_OUTPUT.md` contains the verified customer turns, safe route traces, ranked
predictions, and outcomes. Ground truth is used only after inference to label the
result; production Agent code never receives it.

## Results

Results below come from the unmodified evaluator over all 200 released public
development sessions. They are not private/final-set claims.

| Metric | Organizer BM25 | IntentCart |
|---|---:|---:|
| Hit@10 | 0.125000 | **1.000000** |
| MRR | 0.068034 | **0.709905** |
| MTTC | 9.810000 | **2.120000** |
| Efficiency | 0.119000 | **0.888000** |
| TechnicalScore | 0.106710 | **0.890571** |

IntentCart achieved 100% Hit@10 in Buying, Browsing, Intent Override, and
Boundary. Two full evaluator runs produced byte-identical JSON. The committed
result SHA-256 is
`0faad255af1b1ad12fca5923fd124e0192594e88f348bc3e3bd5caa5f3cdad71`.

On an Apple M2 Pro MacBook Pro with 16 GB memory, macOS 26.5.2, Python 3.12.5,
and SQLite 3.45.3:

- Agent initialization: 4.853555 seconds;
- evaluation after setup: 9.080255 seconds;
- cold end-to-end evaluator: 14.85 seconds;
- mean/P95 response latency: 21.311968/41.749625 ms over 424 calls; and
- maximum resident memory: 428,244,992 bytes (408.4 MiB).

## Model, API, cost, and feasibility

The frozen runtime uses **no external model or API**. It requires no API key,
network call, GPU, vector database, model download, or third-party Python
package. Prompt/completion tokens are 0/0 and estimated model cost is US$0.

The official rules make LLMs optional. We did not label BM25 as dense or
semantic retrieval, and we did not add a phantom LLM for presentation. A dense
or structured semantic stage would need a licensed reproducible artifact,
dependency and memory accounting, validated fallback, and a measured improvement
over this control before adoption.

## Impact and relevance

The measured outcome is less search friction in this challenge: the target is
covered in all public sessions and found in 2.12 turns on average. In a real
catalog, the same state/route pattern could help shoppers explore broadly,
converge after giving requirements, and recover gracefully when their need
changes. That creates a plausible opportunity for better discovery and
conversion, but we do not claim unmeasured revenue lift.

The design is practical for other structured text catalogs because it is
deterministic, headless, network-independent, and cheap to run. Real deployment
would still require broader language evaluation, canonical product families,
live inventory/policy integration, observability, and privacy controls.

## Built with

### Development tools

- VS Code
- Git and GitHub
- Codex
- macOS terminal tooling

### Runtime APIs

- None

### Libraries and frameworks

- Python 3.10+ standard library
- SQLite FTS5 through Python's bundled `sqlite3`
- `unittest`

### Dataset and assets

- Organizer-provided frozen `Clothing_Shoes_and_Jewelry` catalog derived from
  [Amazon Reviews 2023](https://amazon-reviews-2023.github.io/)
- Organizer-provided 200-session public development set and evaluator
- Original Mermaid architecture diagram and original terminal/demo output
- No product images, storefront captures, logos, webinar clips, third-party
  footage, or copyrighted music

## Limitations and next steps

- The parser is optimized for the organizer's deterministic message templates,
  not unrestricted customer language.
- BM25/catalog signatures cannot infer every synonym or cross-category use case.
- Profile tags are generic and form only a weak supplied prior; there is no
  learned identity or cross-session memory.
- Store/title family is an approximation rather than canonical product lineage.
- There is no dense embedding or LLM stage in the frozen runtime.
- The public set was fully observed, so no untouched holdout claim is made.

Next we would test a licensed reproducible in-memory dense route, schema-validated
semantic reranking with strict timeout/cost fallback, more robust language, and
information-gain estimation for specific questions. Each would be accepted only
after a fixed-split ablation with no scenario reliability loss.

## Team contributions

- **Tay Kai:** Agent engineering and final integration—catalog signatures,
  indexes, ranking, dialogue state, override handling, adaptive routes, profile
  conditioning, family coverage, demo tooling, and freeze verification.
- **Yi Qian:** Evaluation and delivery—evaluator analysis, initial contract/state
  tests, catalog audits, ablation evidence, reproducibility documentation, and
  release handoff.
- **Together:** architecture review, control selection, metrics review, and
  submission story. Tay Kai completed the enhanced build and remaining artifacts
  after Yi Qian ended active development.

## Source and reproducibility

Repository: <https://github.com/shang-yi-qian/shopping-copilot>

```bash
python3 -m unittest discover -s tests -v
python3 demo.py
python3 -m evaluator.local_evaluator --output results.json
```

The submission is frozen by the `submission-v1` tag. Full setup, checksums,
architecture, ablations, limitations, and final-evaluation procedure are in the
repository README.
