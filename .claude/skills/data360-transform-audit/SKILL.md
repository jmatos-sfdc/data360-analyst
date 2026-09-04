---
name: data360-transform-audit
description: Audit Data Transform definitions — check dedup grain, formula patterns, null handling, bypassed transforms. Use when user asks to "audit transforms", "check transforms", "review data transforms", or "transform audit".
triggers:
  - "audit transforms"
  - "check transforms"
  - "review data transforms"
  - "transform audit"
---

# Data360 Transform Audit

Review Data Transform DAG definitions for correctness patterns, consistency, and potential issues.

## Where transform data lives

After intake:
```
~/Projects/clients/<Client>/Data360/
├── object-model/transforms/<name>.yaml   — metadata summary
└── transforms/<name>.json                — full DAG definition (HTML-decoded)
```

The JSON files contain the complete node graph: `load` → `join` → `formula` → `filter` → `aggregate` → `outputD360`.

## DAG node types

| Node type | What it does | Key parameters |
|---|---|---|
| `load` | Source DMO/DLO | `dataset.name`, `fields[]` |
| `join` | Join two branches | `joinType` (LOOKUP/INNER/LEFT), `leftKeys`, `rightKeys` |
| `formula` | SQL expression | `expressionType: "SQL"`, `fields[].formulaExpression` (HTML-encoded) |
| `filter` / `sqlFilter` | Row filter | `filterExpressions[]` or raw SQL |
| `aggregate` | GROUP BY + aggregations | `groupByFields`, `aggregations[]` |
| `extractTable` | Extract from nested | Table extraction parameters |
| `schema` | Schema mapping | Field mapping definitions |
| `outputD360` | Write to DMO | Target DMO name, write mode |

## What to check

### Dedup grain (`row_number()` in formula nodes)

Look for `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)` patterns:
- Is the `PARTITION BY` grain correct? (individual vs individual+account vs individual+account+date)
- Is the `ORDER BY` deterministic? (timestamp DESC is good; ID alone may be arbitrary)
- Is the result filtered to `= 1`? (if not, the dedup isn't actually deduplicating)

### Formula correctness

- **Single-day trigger:** `field = date_add(current_date(), -N)` — same fragility as in CIs
- **Null handling:** `field != 'Y' OR field IS NULL` is NOT equivalent to `IFNULL(field,'') <> 'Y'`. Flag inconsistencies.
- **Boolean patterns:** `CASE WHEN field = 'true' THEN 'Y' ELSE 'N' END` — check that the source field values match the comparison

### Bypassed transforms

Cross-reference with CI audit findings:
- If a CI contains inline join/filter logic that a transform already handles, the CI is bypassing the transform
- Common sign: account-type exclusion patterns appearing in both CIs and a "master profile" transform

### DPE-managed transforms

Transforms with `DPE_*` prefix or auto-generated IDs are Auto Cloud package-managed. These are:
- Excluded from audits by default
- Not editable in the UI
- Created by Data Processor Engine configurations

Flag them for awareness but don't audit their internals.

### Join consistency

- Are the same source DMOs joined with the same keys across transforms?
- Are `LOOKUP` joins used where `INNER` should be (or vice versa)?
- Are `rightQualifier` values consistent?

## Output

Write findings to `~/Projects/clients/<Client>/Data360/reports/transform-audit.md`. Group by severity: critical (dedup grain wrong), medium (null handling inconsistency), low (naming/style).
