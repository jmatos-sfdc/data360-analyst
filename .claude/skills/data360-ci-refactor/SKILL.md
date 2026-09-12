---
name: data360-ci-refactor
description: Score Calculated Insight SQL maintainability (structural depth, branch complexity, duplication, size) relative to the other CIs in the org, rank worst-first, and suggest CI-editor-safe refactors. Use when user asks to "find complex CIs", "which CIs need refactoring", "CI maintainability", or "CI complexity score".
triggers:
  - "find complex CIs"
  - "which CIs need refactoring"
  - "CI maintainability"
  - "CI complexity score"
  - "ci refactor"
---

# Data360 CI Complexity / Refactor

Scores every CI's SQL for maintainability, relative to the other CIs in the same snapshot — not
against a fixed bar. Ranks worst-first so an engagement gets a prioritized refactor backlog, with
prose (and safe rewritten SQL, where a mechanical rewrite exists) explaining why each flagged CI
scored the way it did.

This is a different concern from `data360-ci-audit`: audit finds correctness bugs (leap-year traps,
hardcoded RecordType IDs); this finds CIs that are hard to read or modify safely, even if they're
currently correct. It reuses `ci_audit.py`'s duplication and mixed-type-CASE checks directly for two of its four
scoring signals, but doesn't reimplement or modify any of audit's correctness-trap logic.

## Prerequisites

- Intake must have been run first (needs `queries/*.sql` for every CI in scope — cross-CI
  duplication needs to see the upstream CIs too, same requirement as `data360-ci-audit`)
- Virtualenv set up: `cd ~/data360-analyst && source .venv/bin/activate`

## Run

```bash
source ~/data360-analyst/.venv/bin/activate
data360 ci-complexity --output-dir ~/Projects/clients/<client>/Data360
```

## How scoring works

Four signals, each computed per CI then percentile-ranked against every other CI in the snapshot:

| Signal | What it measures |
|---|---|
| **Depth** (40%) | Subquery nesting, CASE-within-CASE, CTE chain length, join count |
| **Branch complexity** (25%) | CASE WHEN-branch count, mixed-type CASE (validator-rejected), boolean AND/OR nesting depth |
| **Duplication** (25%) | Reuses `ci_audit.py`'s repeated-derived-expression and cross-CI redundant-filter checks |
| **Size** (10%) | Distinct field count, line count |

The four percentiles combine into one 0-100 score, bucketed Low / Medium / High / Severe. Because
ranking is relative to the snapshot, a 3-CI org and a 200-CI org calibrate "Severe" differently —
the report says this explicitly. Fewer than 3 successfully-parsed CIs triggers a warning banner
instead of a silent (and meaningless) ranking.

## Refactor suggestions

Only emitted for High/Severe CIs, one per driving signal. Never suggests a top-level CTE — the CI
editor rejects `WITH ...`; suggestions use `FROM (SELECT ...) AS alias` or recommend promoting a
repeated expression into a column on the upstream input CI instead.

## Output

Writes `reports/ci-complexity-report.md` — ranked table (worst first), an excluded-CIs section for
any file that failed to parse, and a per-CI detail section with refactor suggestions.

## After running

- Not every High/Severe CI needs immediate action — some complexity is inherent to the business
  logic. Use this to prioritize a conversation, not as an automatic backlog.
- Pair with `data360-ci-audit` for a full CI health pass: audit for correctness, this for
  maintainability.
