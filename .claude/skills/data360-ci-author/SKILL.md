---
name: data360-ci-author
description: Guide for writing Calculated Insight SQL in Data Cloud — covers CI editor constraints, supported/unsupported functions, DPE upsert-key patterns, and hard-coded-Id fragility. Use when user asks to "write a CI", "create a calculated insight", "draft CI SQL", or "CI SQL help".
triggers:
  - "write a CI"
  - "create a calculated insight"
  - "draft CI SQL"
  - "CI SQL help"
  - "new calculated insight"
  - "CI editor"
---

# Data360 CI Authoring Guide

Everything you need to write Calculated Insight SQL that passes the Data Cloud CI editor validator on the first try.

## CI Editor Constraints

The CI editor is NOT the same as the ad-hoc Query Editor. Many functions and syntax patterns that work in the Query Editor are rejected by the CI editor.

### Table references

- **No table aliases.** Use full DMO names everywhere (SELECT, JOIN ON, WHERE, GROUP BY).
  ```sql
  -- WRONG: uses alias
  FROM ssot__Account__dlm a
  WHERE a.ssot__Type__c = 'Customer'

  -- RIGHT: full name
  FROM ssot__Account__dlm
  WHERE ssot__Account__dlm.ssot__Type__c = 'Customer'
  ```

- **No double-quoted table names.** The Query Editor accepts them; the CI editor does not.
  ```sql
  -- WRONG
  FROM "ssot__Account__dlm"

  -- RIGHT
  FROM ssot__Account__dlm
  ```

### Unsupported functions (use alternatives)

| Don't use | Use instead | Notes |
|---|---|---|
| `REPLACE(str, old, new)` | `REGEXP_REPLACE(str, 'pattern', 'replacement')` | Literal string replacement works with REGEXP_REPLACE |
| `SPLIT_PART(str, delim, n)` | `REGEXP_REPLACE(str, ' .*', '')` | Strips everything after first space (for first token). Adjust regex for other delimiters. |
| `COUNT(DISTINCT field)` | `APPROX_COUNT_DISTINCT(field)` | HyperLogLog estimation — exact for small-to-medium cardinalities |
| `COALESCE(a, b)` | `IFNULL(a, b)` | CI editor uses `IFNULL`; Query Editor uses `COALESCE` |

### Supported CI-specific functions

| Function | Purpose | Notes |
|---|---|---|
| `FIRST(expr)` | Return first value in group | Required for non-grouped columns in SELECT. CI-only — doesn't exist in Query Editor. |
| `IFNULL(a, b)` | Null fallback | CI editor version of COALESCE |
| `REGEXP_REPLACE(str, pattern, replacement)` | Regex-based string replacement | The universal string manipulation tool in CI SQL |
| `APPROX_COUNT_DISTINCT(field)` | Approximate distinct count | Required replacement for COUNT(DISTINCT) |
| `CONCAT(a, b, ...)` | String concatenation | Supports multiple arguments |
| `DATE_TRUNC('DAY', date)` | Truncate to date | Used for fire-once notification date checks |
| `DATE_ADD(date, n)` | Add days to date | Negative n for lookback (e.g., -365 for 1 year) |
| `MIN()`, `MAX()`, `SUM()`, `COUNT()` | Standard aggregates | All work as expected |

## DPE upsert key pattern

For CIs that feed a Data Processing Engine (DPE) to create or upsert records into Salesforce custom objects, the DPE matches on a deterministic ExternalId column. The CI is responsible for emitting that key.

```sql
FIRST(CONCAT(<entityId>, '~', <eventId>))  AS RecordExternalId__c,
```

Conventions worth picking deliberately:
- **Delimiter** — pick one (`~`, `|`, `__`) and stick to it across every CI in the org. Mixed delimiters defeat downstream parsers.
- **Key components** — must produce one row per intended grain. Duplicate ExternalIds cause silent overwrites at upsert time, not errors.
- **`FIRST(CONCAT(...))`** — wrapping in `FIRST()` is required when the CI groups by a coarser key but the components live on a finer-grained input.

## Hard-coded ID fragility

Hard-coding an Id literal (RecordTypeId, RecordType DeveloperName resolved to an Id, queue Id, etc.) anywhere in CI SQL is a deferred failure: the CI passes validation, runs in dev, and silently returns zero rows the next time the org is refreshed from a different source.

```sql
-- FRAGILE — Id changes on sandbox refresh
WHERE ssot__Account__dlm.RecordTypeId__c = '<some 18-char id>'
```

Safer patterns:
- Filter by a stable business field (`AccountType`, `Status`, a custom `__c` enum) instead of the Id
- If the Id is unavoidable, externalize it (config table, parameter) and document the swap step in the deployment runbook

## Testing workflow

1. **Draft SQL in the ad-hoc Query Editor** — use aliases, REPLACE, COUNT(DISTINCT), COALESCE freely for rapid iteration
2. **Convert for CI editor** — remove aliases, swap to full table names, replace unsupported functions
3. **Validate in CI creation modal** — paste and click validate before publishing
4. **Publish and query the CIO** — verify row counts and sample data via Query Editor
5. **Post QA queries** — write validation queries others can run independently
