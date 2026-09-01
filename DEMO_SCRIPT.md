# IntentCart Demo Video Runbook

Target length: under 3:00. This is a manual recording plan for the team member
who will narrate and record the final video. It demonstrates real inference
results and the official evaluator; it does not rely on unsupported claims.

Record with your preferred screen-capture tool and save working media outside
the repository or under the ignored `docs/audits/` directory. Generated media,
captions, thumbnails, account evidence, and private audit bundles are not part
of the GitHub source submission.

## Before recording

- Use the frozen `submission-v2` tag in a clean checkout; retain
  `submission-v1` only for the comparison segment.
- Close `.env`, password managers, private chats, account pages, and unrelated
  terminal history.
- Use a terminal prompt/path that does not expose private information.
- Increase terminal font size and test 1080p readability.
- Confirm `python3 demo.py` and the evaluator run successfully.
- Confirm `git status --short` is clean and `git rev-parse HEAD` matches
  `git rev-list -n 1 submission-v2`.
- Never show or narrate an API key. The selected Agent needs no key.
- Use only original terminal output and the repository's Mermaid diagram.
- Do not include product/storefront images, brand logos, organizer webinar or
  slide footage, third-party clips, or copyrighted music.

## Recording sequence

### 0:00–0:19 — Problem and promise

Show the README title and say:

> Static search forgets evolving intent. IntentCart treats each turn as a joint
> recommendation and information decision: rank likely products, cover useful
> alternatives, and ask one clarification without wasting the turn.

State that the solution targets Buying, Browsing, Intent Override, and Boundary
behavior over the organizer's frozen 50,000-product catalog.

### 0:19–0:44 — Architecture

Show the README Mermaid diagram and point out:

- immutable signature/category indexes and in-memory SQLite FTS5;
- isolated profile and constraint state;
- precision, discovery, override-recovery, and fallback policies;
- deterministic ranking, family coverage, and seen-ASIN handling; and
- v2 calibration only after explicit evidence reduces a pool to at most 20; and
- simultaneous recommendations plus structured clarification.

Say explicitly:

> The selected runtime is standard-library Python plus SQLite FTS5. It makes zero
> model or network calls, uses zero tokens, and costs zero dollars in model fees.

### 0:44–1:20 — Browsing inference

Run:

```bash
python3 demo.py
```

For `public_0007`, highlight:

1. vague Browsing input;
2. `route=discovery`, 519 candidates, ten predictions, and `ask_attribute=other`;
3. the two disclosed composition constraints;
4. profile terms becoming inactive as explicit intent takes over; and
5. candidate narrowing to one and target rank 1 on turn 2.

### 1:20–1:40 — Intent Override inference

For `public_0003`, highlight:

1. the stale initial preference;
2. the target appearing at rank 1 before it can legally score;
3. the explicit replacement message;
4. `route=override_recovery` and the new eligibility epoch; and
5. the target scoring at rank 1 on turn 3.

Explain that the demo consults public ground truth only after each prediction to
label the result. `starter/agent.py` never receives targets or scenario labels.

### 1:40–1:58 — Contract and tests

Run:

```bash
python3 -m unittest discover -s tests -v
```

Show `Ran 33 tests ... OK`. Mention direct coverage for output validity, session
isolation, Buying/Browsing routing, profile immutability and soft ranking,
family coverage, accumulation, neutral Boundary behavior, selective override,
eligibility epochs, v1 preservation, calibrated ranking, strict API output,
fallbacks, determinism, top-k, and turn 10.

### 1:58–2:20 — V1 versus v2 selection

Show `V2_COMPARISON.md` or run:

```bash
python3 compare_versions.py
```

State that v2 preserves 100% Hit@10 and improves TechnicalScore from 0.890571
to 0.935151. Mention that every one of five deterministic diagnostic slices
improves. Briefly disclose the rejected bounded LLM trial: zero score delta,
25,020/953 tokens, US$0.006195, and two safe fallbacks across 11 calls. Never
show `.env` or an API response.

### 2:20–2:36 — Official results

Run before recording, or run live if the 15-second pause is acceptable:

```bash
python3 -m evaluator.local_evaluator --output results.json
```

Show and narrate:

```text
Hit@10          1.000000
MRR             0.856504
MTTC            2.090000
Efficiency      0.891000
TechnicalScore  0.935151
Tokens          0
```

Then show the per-scenario table in README: Hit@10 is 1.0 in all four scenarios.
Label every number “200-session public development,” not final/private.

### 2:36–2:54 — Feasibility, impact, and close

Show the feasibility table and say:

- 4.94-second initialization and roughly 21.1 ms mean response latency on the
  recorded Apple M2 Pro environment;
- approximately 418 MiB maximum resident memory;
- no GPU, external service, model download, tokens, or model cost;
- fewer turns and higher rank mean less search friction in this benchmark; and
- applicability to other structured text catalogs is plausible, but no revenue
  or private-set uplift is claimed.

Show limitations: deterministic-template dependence, lexical semantic limits,
weak generic profile tags, approximate families, and no untouched holdout.

Show the public repository URL and closing attribution:

> IntentCart uses the organizer-provided clothing catalog derived from Amazon
> Reviews 2023 and the organizer's simulated public sessions. Source,
> reproducibility steps, ablations, and the complete evidence are in the public
> repository.

## Required visible evidence

- [x] Frozen tag/commit visible.
- [x] `python3 demo.py` inference output visible.
- [x] Browsing rank-1 turn-2 result visible.
- [x] Override eligibility and rank-1 turn-3 result visible.
- [x] 33/33 passing tests visible.
- [x] V1/v2 comparison and LLM rejection evidence visible.
- [x] Overall and all four scenario metrics visible.
- [x] Runtime, memory, tokens, cost, and network dependency stated.
- [x] Public-development label visible.
- [x] Repository and dataset attribution visible.

## Final safety and publication audit

- [x] No `.env`, API key, private profile content, hidden final data, email,
      private filesystem path, or account credential appears in any frame.
- [x] No unlicensed music, logos, product imagery, storefront pages, webinar
      footage, organizer slides, or third-party media appears.
- [ ] Captions are accurate and readable.
- [ ] Video description links the public repository and credits Amazon
      Reviews 2023.
- [ ] Video visibility is Public, not Unlisted or Private if the rules require
      public access.
- [ ] YouTube URL opens while signed out.
- [ ] YouTube URL is placed in the Devpost video field and submission body.
- [ ] Devpost opens while signed out and links back to the repository/video.
- [ ] Final video URL, Devpost URL, release SHA, and submission confirmation are
      retained in the team's external evidence record.

## Suggested video description

```text
IntentCart is our TechJam 2026 Shopping Copilot submission: a deterministic,
stateful conversational retrieval Agent for the organizer's frozen 50,000-item
catalog. This demo shows live Browsing and Intent Override inference, tests, and
public-development evaluation.

Source: https://github.com/shang-yi-qian/shopping-copilot

Dataset attribution: organizer-prepared derivative of Amazon Reviews 2023 by
McAuley Lab, UC San Diego. Sessions are simulated and are not real shopping
conversations. No product imagery or third-party media is used.
```
