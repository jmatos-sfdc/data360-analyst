---
name: data360-sql-convert
description: Convert working Query Editor SQL to CI editor-compatible SQL — strips aliases, swaps unsupported functions, uses full table names. Use when user asks to "convert SQL for CI", "make this CI-ready", "CI editor compatible", or "convert query to CI".
triggers:
  - "convert SQL for CI"
  - "make this CI-ready"
  - "CI editor compatible"
  - "convert query to CI"
  - "sql convert"
  - "prepare for CI editor"
---

# Data360 SQL Convert — Query Editor → CI Editor

Mechanical transformation of working Query Editor SQL into CI editor-compatible SQL. Run this before pasting into the CI creation modal.

## Conversion rules

Apply these transformations in order:

### 1. Remove table aliases

Replace all aliases with full DMO names.

```sql
-- BEFORE (Query Editor)
SELECT a.ssot__Id__c, c.ssot__Name__c
FROM ssot__Account__dlm a
JOIN ssot__Campaign__dlm c ON a.ssot__Id__c = c.WhatId__c

-- AFTER (CI Editor)
SELECT ssot__Account__dlm.ssot__Id__c, ssot__Campaign__dlm.ssot__Name__c
FROM ssot__Account__dlm
JOIN ssot__Campaign__dlm ON ssot__Account__dlm.ssot__Id__c = ssot__Campaign__dlm.WhatId__c
```

### 2. Remove double-quoted table names

```sql
-- BEFORE
FROM "ssot__Account__dlm"

-- AFTER
FROM ssot__Account__dlm
```

### 3. Swap unsupported functions

| Query Editor | CI Editor |
|---|---|
| `REPLACE(str, old, new)` | `REGEXP_REPLACE(str, 'old', 'new')` |
| `SPLIT_PART(str, ' ', 1)` | `REGEXP_REPLACE(str, ' .*', '')` |
| `COUNT(DISTINCT field)` | `APPROX_COUNT_DISTINCT(field)` |
| `COALESCE(a, b)` | `IFNULL(a, b)` |

### 4. Wrap non-grouped columns with FIRST()

Any column in SELECT that is not in the GROUP BY and is not an aggregate (`SUM`, `MIN`, `MAX`, `COUNT`, `APPROX_COUNT_DISTINCT`) must be wrapped in `FIRST()`.

```sql
-- BEFORE (Query Editor — implicit grouping)
SELECT campaign_name, SUM(amount)

-- AFTER (CI Editor — explicit FIRST)
SELECT FIRST(campaign_name) AS CampaignName__c, SUM(amount) AS TotalAmount__c
```

### 5. Add `__c` suffixes to output aliases

CI editor expects all output column aliases to end in `__c`.

```sql
-- BEFORE
AS CampaignId

-- AFTER
AS CampaignId__c
```

## Workflow

1. User provides working Query Editor SQL (or pastes it)
2. Apply all 5 transformations
3. Present the converted SQL
4. User pastes into CI editor and validates

## Verification

After conversion, scan the output for:
- Any remaining table aliases (single letter or short word before a `.`)
- Any remaining double-quoted table names
- Any `REPLACE(`, `SPLIT_PART(`, `COUNT(DISTINCT`, `COALESCE(` calls
- Any non-aggregated, non-FIRST() columns in SELECT that aren't in GROUP BY
- Any output aliases missing `__c`

Flag anything found before the user pastes.
