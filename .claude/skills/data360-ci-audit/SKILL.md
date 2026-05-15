---
name: data360-ci-audit
description: Audit Calculated Insight SQL for known correctness traps — leap-year bugs, single-day triggers, hard-coded IDs, missing unsubscribe suppression, dedup window grain. Use when user asks to "audit CIs", "check CI SQL", "review calculated insights", or "find CI bugs".
triggers:
  - "audit CIs"
  - "check CI SQL"
  - "review calculated insights"
  - "find CI bugs"
  - "ci audit"
---

# Data360 CI Audit — SQL Static Analysis

Parse each CI's SQL with sqlglot and check for known correctness traps. AST-based, not regex — robust to whitespace, aliasing, function casing, and parenthesization.

## Prerequisites

- Intake must have been run first (needs `queries/*.sql` files on disk)
- Virtualenv set up: `cd ~/Projects/salesforce/data360-analyst && source .venv/bin/activate`

## Run

```bash
source ~/Projects/salesforce/data360-analyst/.venv/bin/activate
python ~/Projects/salesforce/data360-analyst/ci_audit.py --output-dir ~/Projects/clients/<client>/Data360
```

## What it checks

| Check | What it catches |
|---|---|
| **Single-day trigger equality** | `col = date_add(current_date(), -N)` — one failed run = permanent miss |
| **Leap-year bug** | `MONTH(col) = MONTH(CURRENT_DATE) AND DAY(col) = DAY(CURRENT_DATE)` skips Feb 29 |
| **`CURRENT_DATE()` usage** | Counts occurrences so user can review UTC vs US-timezone intent |
| **Missing unsubscribe suppression** | Flags SQL with no `LEFT JOIN *_Unsubscribes__dlm` |
| **`ROW_NUMBER() PARTITION BY` grain** | Reports every dedup window for manual verification |

## Output

Writes findings to `reports/ci-audit.md` in the client's Data360 folder. Each finding includes the CI name, the line of SQL, and a plain-English explanation of the risk.

## After running

- Review findings with the team — not every finding is a bug (some `CURRENT_DATE()` usage is intentional)
- Hard-coded RecordTypeIds are the most common real issue — they break on sandbox refresh
- For CIs with complex joins, follow up with `cluster_cis_by_dmo.py` to understand the join topology
