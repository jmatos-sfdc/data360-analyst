# CI SQL Audit

_Scanned 8 SQL file(s)_

## Summary

### Correctness traps
- Files with single-day trigger equality: **1**
- Files with leap-year `MONTH()/DAY()` pattern: **1**
- Files with no `*_Unsubscribes__dlm` LEFT JOIN: **7** (marketing CIs only — transactional CIs are exempt)
- Files with hardcoded RecordType ID literals: **1**

### CI editor compliance
Patterns that fail the CI editor's bespoke validator at save time. Each one was confirmed live in 2026-05-23 testing or is in the internal CI SQL Builder canvas — see `local/ci-editor-sql-research.md`.

- Files using unsupported functions (MEDIAN, COALESCE, NVL, etc.): **0**
- Files using `COUNT(DISTINCT)`: **1**
- Files using `COUNT(*)` (discouraged by docs, accepted by parser): **0**
- Files with `TRY_CONVERT_CURRENCY` wrong arity (must be 3-arg): **0**
- Files referencing DLOs (`__dll`) in FROM/JOIN: **0**
- Files with DMO/CIO table aliases: **2**
- Files with self-joins (DMO appears twice in same scope): **1**
- Files with top-level `DISTINCT`: **0**
- Files with top-level `ORDER BY`: **1**
- Files with top-level CTE (`WITH ... AS`): **0**
- Files with `EXISTS (...)` subqueries: **0**
- Files with `IN (SELECT col)` (unaliased inner column): **1**
- Files with `Foo AS Foo` (alias = source field name): **0**
- Files using `||` string-concat operator: **0**
- Files with double-quoted identifiers: **0**
- Files with `DATEDIFF` inside `CASE`: **0**
- Files with `AVG(CASE ...)` nesting: **1**
- Files with `CASE` mixing NULL with concrete types: **0**
- Files reusing an aliased `NTILE` result in `CASE`: **0**
- Files with `CDP*` family inside an aggregation: **0**
- Files with CONCAT-aggregate provenance trap: **0**

### Redundancy / cleanup
- Files with same predicate in JOIN ON and WHERE: **1**
- Files with a derived expression repeated 3+ times: **1**
- Files duplicating a filter their input CI already enforces: **0**
- Files exceeding doc-recommended limits (50 measures / 10 dimensions / 5 JOINs): **0**

## Per-file findings

### `Account_Parent_Hierarchy__cio.sql`
- ⚠ No `*_Unsubscribes__dlm` LEFT JOIN found — confirm this CI is transactional (exempt).
- **DMO/CIO table alias(es)** — use full DMO names everywhere; aliasing surfaces ambiguous-column errors.
  - `Account__dlm AS child`
  - `Account__dlm AS parent`
- **Self-join detected** — the same DMO appearing twice at the same scope is not supported. Pre-build a copy in an upstream CI/transform.
  - `account__dlm`

### `Active_Customer_Accounts__cio.sql`
- ⚠ No `*_Unsubscribes__dlm` LEFT JOIN found — confirm this CI is transactional (exempt).
- **Hardcoded RecordType ID(s)** — break on sandbox refresh; join to a RecordType DMO + filter on `DeveloperName` or use a portable column-level filter instead.
  - `'0123x000000ABCDAA2'`

### `Birthday_Email_Audience__cio.sql`
- **Leap-year bug** — `MONTH()/DAY()` equality skips Feb-29 birthdays in non-leap years.
  - `WHERE MONTH(ssot__Individual__dlm.BirthDate__c) = MONTH(CURRENT_DATE) AND DAY(ssot__Individual__dlm.BirthDate__c) = DAY(CURRENT_DATE)`
- `CURRENT_DATE()` called 2 time(s) — evaluates in UTC; verify intent for late-day US events.
- ⚠ No `*_Unsubscribes__dlm` LEFT JOIN found — confirm this CI is transactional (exempt).

### `Customer_Order_Summary_QueryEditor__cio.sql`
- ⚠ No `*_Unsubscribes__dlm` LEFT JOIN found — confirm this CI is transactional (exempt).
- **`COUNT(DISTINCT)` — rejected by validator.** Use `APPROX_COUNT_DISTINCT(col)`.
  - `COUNT(DISTINCT o.Id__c)`
- **DMO/CIO table alias(es)** — use full DMO names everywhere; aliasing surfaces ambiguous-column errors.
  - `Account__dlm AS a`
  - `ssot__Individual__dlm AS i`
  - `SalesOrder__dlm AS o`
- **Top-level `ORDER BY`** — rejected (CI output is unordered). Use a window function for rank columns or sort downstream.
  - `ORDER BY total_amount__c DESC`
- **`IN (SELECT col)` without alias** — inner column needs an explicit `AS alias` for the validator to bind its type.
  - `o.Status__c IN (SELECT Status__c FROM SalesOrder__dlm)`

### `Customer_Order_Totals__cio.sql`
- Unsubscribe suppression join present:
  - LEFT JOIN `Email_Unsubscribes__dlm`
- No issues detected.

### `Customer_Profile_Score__cio.sql`
- ⚠ No `*_Unsubscribes__dlm` LEFT JOIN found — confirm this CI is transactional (exempt).
- **Repeated derived expression(s)** — candidate to lift into a column on the input CI or a CTE.
  - 4× `COALESCE(ssot__Individual__dlm.FirstName__c, 'Customer')`

### `New_Account_Welcome__cio.sql`
- **Single-day trigger equality** — a single failed run = permanent miss. Prefer `>= ... AND <` ranges.
  - Account__dlm.CreatedDate__c = DATE_ADD(CURRENT_DATE, -1)
- `CURRENT_DATE()` called 1 time(s) — evaluates in UTC; verify intent for late-day US events.
- ⚠ No `*_Unsubscribes__dlm` LEFT JOIN found — confirm this CI is transactional (exempt).

### `Order_Conversion_Rate__cio.sql`
- ⚠ No `*_Unsubscribes__dlm` LEFT JOIN found — confirm this CI is transactional (exempt).
- **Same predicate in JOIN ON and WHERE** — defensive duplication; drop one (or confirm both are intentional).
  - `SalesOrder__dlm.Status__c = 'Shipped'`
- **`AVG(CASE ...)` nesting** — rejected. Use `SUM(CASE ...) / NULLIF(COUNT(CASE ...), 0)` instead.
  - `AVG(CASE WHEN SalesOrder__dlm.Status__c = 'Shipped' THEN SalesOrder__dlm.Amount__c ELSE NULL END)…`
