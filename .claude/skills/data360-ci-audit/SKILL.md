---
name: data360-ci-audit
description: Audit Calculated Insight SQL for correctness traps and redundancy patterns — leap-year bugs, single-day triggers, hardcoded RecordType IDs, defensive duplicate filters, repeated derived expressions, and filters duplicated across an inner-joined CI. Use when user asks to "audit CIs", "check CI SQL", "review calculated insights", "find CI bugs", or "find CI redundancies".
triggers:
  - "audit CIs"
  - "check CI SQL"
  - "review calculated insights"
  - "find CI bugs"
  - "find CI redundancies"
  - "ci audit"
---

# Data360 CI Audit — SQL Static Analysis

Parse each CI's SQL with sqlglot and check for known correctness traps and redundancy patterns. AST-based, not regex — robust to whitespace, aliasing, function casing, and parenthesization. Cross-CI checks (filter duplicated by an inner-joined CI) work over the entire batch, so an `intake.py` snapshot of the org is required.

## Prerequisites

- Intake must have been run first (needs `queries/*.sql` files on disk for every CI in scope, including dependencies — the cross-CI check needs to see the upstream CIs)
- Virtualenv set up: `cd ~/data360-analyst && source .venv/bin/activate`

## Run

```bash
source ~/data360-analyst/.venv/bin/activate
data360 ci-audit --output-dir ~/Projects/clients/<client>/Data360

# Audit + auto-fix in one shot — adds queries-converted/ + reports/ci-convert.md
data360 ci-audit --output-dir ~/Projects/clients/<client>/Data360 --fix
```

`--fix` runs `ci_convert.py` against the same queries directory after the audit completes. Useful when you want both the diagnostic report (which CIs have what issues) and the converted SQL (mechanically rewritten where the converter can, flagged otherwise) in a single pass.

## What it checks

### Correctness traps

| Check | What it catches |
|---|---|
| **Single-day trigger equality** | `col = date_add(current_date(), -N)` — one failed run = permanent miss |
| **Leap-year bug** | `MONTH(col) = MONTH(CURRENT_DATE) AND DAY(col) = DAY(CURRENT_DATE)` skips Feb 29 |
| **`CURRENT_DATE()` usage** | Counts occurrences so user can review UTC vs US-timezone intent |
| **Missing unsubscribe suppression** | Flags SQL with no `LEFT JOIN *_Unsubscribes__dlm` |
| **`ROW_NUMBER() PARTITION BY` grain** | Reports every dedup window for manual verification |
| **Hardcoded RecordType IDs** | String literals matching `012...` — fragile across sandbox refresh; prefer joining a RecordType DMO + filtering on `DeveloperName` |

### Redundancy / cleanup

| Check | What it catches |
|---|---|
| **Same predicate in JOIN ON and WHERE** | Defensive duplication; one is dead code, and they drift independently |
| **Repeated derived expression (3+ uses)** | Same `IFNULL(...)` / `CONCAT(...)` / `REGEXP_REPLACE(...)` reused 3+ times across SELECT / JOIN / GROUP BY — candidate to lift into a column on the input CI or a CTE |
| **Filter already enforced by an inner-joined CI** | A column-to-literal predicate on a base DMO that the upstream CI being inner-joined already enforces — the duplicate is dead code |

## Output

Writes findings to `reports/ci-audit.md` in the client's Data360 folder. Summary section is split into "Correctness traps" and "Redundancy / cleanup". Per-file findings include the CI name, the SQL fragment, and a plain-English explanation of the risk.

## After running

- Review findings with the team — not every finding is a bug. Some `CURRENT_DATE()` usage is intentional, defensive duplicates may be a deliberate safety net, and a repeated `IFNULL` may be cheaper than a CTE.
- Hardcoded RecordType IDs are the most common real issue — they break on sandbox refresh.
- The "filter already enforced" finding is especially valuable for foundational CIs like `Common_Owner_Hierarchy__cio` whose filters get duplicated by every dependent. One fix to the foundation removes redundancy across dozens of dependents.
- For CIs with complex joins, follow up with `cluster_cis_by_dmo.py` to understand the join topology.
