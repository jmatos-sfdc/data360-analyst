---
name: data360-ci-author
description: Guide for writing Calculated Insight SQL in Data Cloud — covers CI editor validator constraints, supported/unsupported functions, canonical patterns, hard limits, and aggregatable-measure rules. Use when user asks to "write a CI", "create a calculated insight", "draft CI SQL", "CI SQL help", or "review CI SQL before saving".
triggers:
  - "write a CI"
  - "create a calculated insight"
  - "draft CI SQL"
  - "CI SQL help"
  - "new calculated insight"
  - "CI editor"
  - "review CI SQL"
---

# Data360 CI Authoring Guide

Reference for writing Calculated Insight SQL that passes the CI editor validator on the first try.

The CI editor is **not** the Query Editor. It runs a bespoke (Spark-leaning) validator that rejects many shapes Hyper/Trino would accept. Treat any SQL that "worked in Query Editor" as a draft, not a finished CI — convert it through `data360-sql-convert` first.

For full provenance and live test results, see [`local/ci-editor-sql-research.md`](../../../local/ci-editor-sql-research.md) — this skill is the actionable distillation.

---

## Hard limits — refactor when you hit these

- **50 measures per CI** (any field wrapped in an aggregate)
- **10 dimensions per CI** (any non-aggregate column in `SELECT` — including literal constants like `0` or `'flag'`)
- **~4-5 JOINs per CI** — soft ceiling; past that, validator errors become unreliable and runtime degrades
- **Field aliases must be unique within the CI** AND must not collide with any existing DMO field name (including fields on joined DMOs you don't reference in `SELECT`)

When you hit a limit, split the CI by grain or channel and chain CIs (CI-on-CI is the supported pattern).

---

## Architectural rules

### Required structure

- **At least one measure required** — an aggregate function on at least one field. The validator's "Measure collection cannot be empty" / "At last one FactTable need" errors trace back to this.
- **Every non-aggregate column in `SELECT` must appear in `GROUP BY`.**
- **No nested aggregates** — `SUM(COUNT(x))` is illegal.
- **DMOs only** — DLOs (`__dll`) cannot be used in CI `FROM`/`JOIN`. Use the DMO (`__dlm`) the DLO maps to.
- **DMO names are case-sensitive** — the validator runs an FQK (Fully-Qualified-Keyword) check against the metadata catalog. `ssot__AIAgentSession__dlm` ≠ `ssot__AiAgentSession__dlm`. Always copy from intake YAML or Data Explorer.
- **Avoid table aliases** — use full `DMOName__dlm.field__c` everywhere. Aliasing surfaces ambiguous-column errors that don't reproduce with full names.
- **String literals: single quotes only.** `"value"` is parsed as an identifier and fails field resolution.
- **Alias cannot match the field's original name.** `Id__c AS Id__c` errors; `Id__c AS customer_id__c` works.

### Forbidden at the top level

- `SELECT DISTINCT` — wrap in subquery: `FROM (SELECT DISTINCT ... ) AS sub`
- `ORDER BY` — CI output is unordered; use window functions (`ROW_NUMBER`, `RANK`) for rank columns or sort downstream in segments/BI
- `COUNT(*)` — use `COUNT(<column>)`. (The live parser does accept `COUNT(*)`, but the help-doc forbids it; treat as discouraged.)

### Clause-by-clause

| Clause | Rules |
|---|---|
| `SELECT` | Currency fields **must** use `TRY_CONVERT_CURRENCY(amount, 'SRC', 'TGT')` — 3-arg form. Constants (`0`, `'text'`) become dimensions. |
| `WHERE` | No aggregates. No alias references. Use `IS NULL` (not empty string) for missing date filters. |
| `GROUP BY` | Non-aggregate expressions or standalone dimension aliases (not inside expressions). No measure aliases. |
| `ORDER BY` | Forbidden at top level. Allowed inside `OVER(...)`. |
| Window `OVER(...)` | Every column referenced (including in `ORDER BY`) must be aggregated when SELECT contains other aggregations. `PARTITION BY MAX(col)`, `ORDER BY MAX(col) DESC` — not bare columns. |
| `CASE` | Branches must return the same type — no mixing `NULL` with numerics (use `0` or `0.0`). A `CASE` containing an aggregate becomes a (non-aggregatable) measure. |
| Comments | `/* ... */` multiline supported. |

---

## JOIN rules

- **Left-table field listed first in `ON` predicates** — `LeftDMO__dlm.field__c = RightDMO__dlm.field__c`.
- **NULLs in JOIN keys are silently excluded** — no error, just missing rows. Use `IFNULL(key, '__missing__')` on both sides if null-key rows must be preserved.
- **Self-joins are not supported** — pre-build a copy in an upstream CI/transform.
- **FKQ requirement** — if your org provisions Foreign Key Qualifier (`KQ_*`) fields on a DMO, every JOIN's `ON` clause must include the matching `KQ_*` predicate on each side. Check the DMO sidecar (`object-model/dmos/<name>.yaml`) for `KQ_*` fields.
- **Filter inside `JOIN ON` to preserve LEFT JOIN semantics** — moving the filter to `WHERE` degrades a LEFT JOIN to an INNER JOIN.
- **Errors cascade from JOIN/field issues** — when an `ON` predicate references a misspelled or unmapped field, the validator emits 3-5 cascading errors. Fix the JOIN/field error first; downstream "function not supported" errors are usually artifacts.

`INNER`, `LEFT`, `FULL OUTER` confirmed supported. `RIGHT` and `CROSS` not directly tested but help-doc-implied.

---

## Aggregatable vs non-aggregatable measures

This distinction governs how segments/activations can roll up your CI's measures. Source: `c360_a_calculated_insights_aggregates.htm`.

- **Aggregatable** (`Y`): `SUM`, `COUNT`, `AVG`, `MIN`, `MAX`, `MEAN`, `FIRST`, `LAST` — segments can roll up across fewer dimensions than the CI defines.
- **Non-aggregatable** (`N`): everything else — `APPROX_COUNT_DISTINCT`, `PERCENTILE`, `STDDEV`, all window functions (`RANK`, `NTILE`, `ROW_NUMBER`, `LAG`, `LEAD`, `DENSE_RANK`, `PERCENT_RANK`, `FIRST_VALUE`, `LAST_VALUE`), all date/string/math/boolean functions, and any `CASE` containing an aggregate.

When a CI publishes a non-aggregatable measure, **segments and activations must specify ALL dimensions defined on the CI**. You can't ask "rank by product alone" if the CI was defined as "rank by product × location" — both must be supplied.

**Activation: dimensions cannot be activated.** To make a dimension reach a downstream destination, wrap it in `FIRST()` / `MAX()` / `MIN()` to convert it into a (non-aggregatable) measure.

```sql
-- WRONG — Region__c is a dimension, can't be activated
SELECT Account__dlm.Region__c AS Region__c, ...
GROUP BY Region__c

-- RIGHT — wrapping makes Region__c a measure
SELECT FIRST(Account__dlm.Region__c) AS Region__c, ...
GROUP BY Account__dlm.Id__c
```

---

## Streaming / real-time CIs

Streaming and real-time CIs support **only `SUM` and `COUNT`** as aggregates. All other aggregate functions are batch-only.

`publishScheduleInterval` enum: `NotScheduled`, `ExternallyManaged`, `One` (1hr), `Six`, `Twelve`, `TwentyFour` (daily), `Streaming`, `SystemManaged`.

---

## Function inventory

### Supported aggregates

| Function | Aggregatable | Notes |
|---|---|---|
| `SUM`, `COUNT`, `AVG`, `MIN`, `MAX`, `MEAN` | Y | `MEAN` likely an `AVG` alias. |
| `FIRST`, `LAST` | Y | First/last value in a group. `FIRST` is the canonical "passthrough a dimension as a measure" pattern. |
| `APPROX_COUNT_DISTINCT(col)` | N | HyperLogLog++ — replacement for `COUNT(DISTINCT)`. |
| `PERCENTILE(col, p)` | N | Replacement for `MEDIAN` and `PERCENTILE_CONT`. 3-arg form `PERCENTILE(col, p, accuracy)` accepted (Spark `PERCENTILE_APPROX` style). Multi-percentile via array (`PERCENTILE(col, array(0.25, 0.75))`) **not** supported — repeat as separate measures. |
| `STDDEV(col)` | N | Standard deviation. |

### Date/Time

`HOUR`, `DAY`, `MONTH`, `QUARTER`, `YEAR`, `DATEDIFF`, `MONTHS_BETWEEN`, `DAYOFWEEK`, `DAYOFMONTH`, `DAYOFYEAR`, `TO_DATE`, `TO_TIMESTAMP`, `DATE_TRUNC`, `DATE_ADD`, `DATE_SUB`, `HOUR_ADD`, `HOUR_SUB`, `SECOND_ADD`, `SECOND_SUB`, `CURRENT_DATE`, `NOW`.

`CDP*` family (`CDPHOUR`, `CDPDAY`, `CDPMONTH`, `CDPQUARTER`, `CDPYEAR`) — Salesforce-specific date truncation that returns a **timestamp**, not an integer:
- `CDPDAY('2009-07-30 01:10:05')` → `'2009-07-30 00:00:00'`
- **`CDP*` is dimension-only** — cannot appear inside an aggregation or as a measure expression.

`DATEDIFF` returns days only. For sub-day differences use `HOUR_SUB` / `SECOND_SUB` math.

`NOW()` and `CURRENT_DATE()` return the same value within a single query (snapshot at query-start, UTC).

### Window / analytical (all non-aggregatable)

`LAG`, `LEAD`, `NTILE`, `RANK`, `PERCENT_RANK`, `DENSE_RANK`, `ROW_NUMBER`, `FIRST_VALUE`, `LAST_VALUE`.

### Pattern matching

`REGEXP`, `REGEXP_EXTRACT(col, pattern, group_idx)`, `REGEXP_REPLACE(col, pattern, replacement)`, `LIKE`, `RLIKE`, `NOTLIKE`, `NOTRLIKE`, `CONTAINS`.

### Null handling

`ISNULL(col)`, `ISNOTNULL(col)`, `IFNULL(a, b)`, `NULLIF(a, b)`. Use `ISNULL`/`ISNOTNULL` function form (not standard `IS NULL` operator) inside nested expressions to avoid ambiguity.

### Strings

`SUBSTRING`, `SUBSTR`, `RTRIM`, `UPPER`, `LOWER`, `CONCAT(a, b, ...)`.

### Math

`MOD(a, b)`, `ABS`, `RAND`, `ROUND`, `GREATEST`, `LEAST`, `LOG`, `EXP`, `BETWEEN`. No `%` modulo operator.

### Boolean

`IS_TRUE`, `IS_FALSE`, `HAS_BOOLEAN_VALUE`, `HAS_NO_BOOLEAN_VALUE` — canonical null-checks for boolean columns.

### Currency

`TRY_CONVERT_CURRENCY(amount, source_iso, 'TARGET_ISO')` — 3-arg form, target must be a literal string. **The CI itself must declare a target currency dimension; all currency fields must convert to the same target.** Plan one CI per target currency for multi-currency reporting.

### Confirmed unsupported (do not use)

| Don't use | Use instead |
|---|---|
| `WITH name AS (...)` (CTEs) | `FROM (SELECT ... ) AS name` |
| `EXISTS (SELECT ...)` | `INNER JOIN` or `IN (SELECT col AS alias FROM ...)` |
| `IN (SELECT col FROM ...)` (no alias) | `IN (SELECT col AS alias FROM ...)` — alias required |
| `MEDIAN(col)` | `PERCENTILE(col, 0.5)` |
| `PERCENTILE_CONT(p) WITHIN GROUP (...)` | `PERCENTILE(col, p)` |
| `COALESCE(a, b, c)` | `IFNULL(a, IFNULL(b, c))` |
| `NVL(a, b)` | `IFNULL(a, b)` |
| `NVL2(a, b, c)` | `CASE WHEN a IS NOT NULL THEN b ELSE c END` |
| `DECODE(x, 1, 'A', 2, 'B', 'C')` | `CASE WHEN x = 1 THEN 'A' WHEN x = 2 THEN 'B' ELSE 'C' END` |
| `a \|\| b` (string concat) | `CONCAT(a, b)` |
| `EXTRACT(YEAR FROM date)` | `YEAR(date)` |
| `DISTANCE(lat1, lng1, lat2, lng2)` | bounding-box `BETWEEN` predicates (1° lat/lng ≈ 111 km) |
| `COUNT(DISTINCT col)` | `APPROX_COUNT_DISTINCT(col)` |
| `TRY_CAST(x AS T)` | `CAST(x AS T)` (no graceful-null fallback — throws at runtime) |
| `array(...)` constructor | repeat values as separate columns |
| `REPLACE(str, old, new)` | `REGEXP_REPLACE(str, old, new)` |
| `SPLIT_PART(str, delim, n)` | `REGEXP_REPLACE(str, ' .*', '')` (adjust regex per delimiter) |
| Self-joins | pre-build a copy in upstream CI/transform |

---

## Expression-level traps

Each of these is a syntactic shape that's valid Spark/Hyper SQL but rejected by the CI validator.

- **`DATEDIFF` cannot be used inside `CASE`.** Pre-compute in an inner subquery and reference the resulting column outside.
  ```sql
  -- WRONG
  CASE WHEN DATEDIFF(a, b) > 30 THEN 'old' ELSE 'new' END
  -- RIGHT
  SELECT CASE WHEN days_diff__c > 30 THEN 'old' ELSE 'new' END AS bucket__c
  FROM (SELECT DATEDIFF(a, b) AS days_diff__c, ... FROM ...)
  ```

- **`AVG` cannot be nested with `CASE`.** Use the explicit form:
  ```sql
  -- WRONG
  AVG(CASE WHEN cond THEN x ELSE NULL END)
  -- RIGHT
  SUM(CASE WHEN cond THEN x ELSE 0 END)
  / NULLIF(COUNT(CASE WHEN cond THEN 1 END), 0)
  ```

- **`CASE` branch types must match.** Replace `NULL` branches with `0` / `0.0` / `''` matching the other branches.

- **`NTILE` results cannot be aliased and reused** — repeat the full `NTILE(n) OVER (...)` expression in each downstream `CASE` branch.

- **CONCAT-aggregate provenance trap.** The validator tracks aggregate provenance through expressions. `FIRST(CONCAT('Count~', CAST(COUNT(x) AS STRING)))` fails — provenance survives `CAST AS STRING` and `FIRST()` wrapping but does NOT survive crossing a CI boundary if upstream is published as `STRING`. Workaround: publish the aggregate as `CAST(... AS STRING) AS Foo__c` on an upstream CI, then `CONCAT` of `Foo__c` validates downstream.

---

## Canonical patterns

### Pattern 1: Source-to-Unified-Individual link

The standard rollup-to-unified-profile shape. Used in 4 of 4 official help-doc examples.

```sql
SELECT
  COUNT(EmailEngagement__dlm.Id__c) AS email_engagement_count__c,
  UnifiedIndividual__dlm.Id__c       AS customer_id__c
FROM EmailEngagement__dlm
JOIN IndividualIdentityLink__dlm
  ON EmailEngagement__dlm.IndividualId__c = IndividualIdentityLink__dlm.SourceRecordId__c
JOIN UnifiedIndividual__dlm
  ON UnifiedIndividual__dlm.Id__c = IndividualIdentityLink__dlm.UnifiedRecordId__c
GROUP BY customer_id__c
```

### Pattern 2: Filter inside JOIN ON (preserves LEFT JOIN semantics)

```sql
LEFT JOIN IndividualIdentityLink__dlm
  ON IndividualIdentityLink__dlm.SourceRecordId__c = EmailEngagement__dlm.IndividualId__c
  AND EmailEngagement__dlm.EngagementChannelActionId__c = 'Open'
```

### Pattern 3: RFM scoring with NTILE

```sql
SELECT
  sub2.cust_id__c                                                            AS id__c,
  FIRST(sub2.rfm_recency__c*100 + sub2.rfm_frequency__c*10 + sub2.rfm_monetary__c) AS rfm_combined__c,
  FIRST(sub2.rfm_recency__c)   AS Recency__c,
  FIRST(sub2.rfm_frequency__c) AS Frequency__c,
  FIRST(sub2.rfm_monetary__c)  AS Monetary__c
FROM (
  SELECT
    UnifiedIndividual__dlm.Id__c                                              AS cust_id__c,
    NTILE(4) OVER (ORDER BY MAX(SALESORDER__dlm.checkout_date__c))            AS rfm_recency__c,
    NTILE(4) OVER (ORDER BY SUM(SALESORDER__dlm.orderid__c))                  AS rfm_frequency__c,
    NTILE(4) OVER (ORDER BY AVG(SALESORDER__dlm.grand_total_amount__c))       AS rfm_monetary__c
  FROM SALESORDER__dlm
  LEFT JOIN IndividualIdentityLink__dlm ON SALESORDER__dlm.partyid__c = IndividualIdentityLink__dlm.SourceRecordId__c
  LEFT JOIN UnifiedIndividual__dlm     ON UnifiedIndividual__dlm.Id__c = IndividualIdentityLink__dlm.UnifiedRecordId__c
  GROUP BY cust_id__c
) AS sub2
GROUP BY sub2.cust_id__c
```

`NTILE(n) OVER (ORDER BY <agg>)` wrapped in `FIRST()` is the canonical "recover an aggregatable measure from a window function" trick.

### Pattern 4: Three-layer engagement score (CI-on-CI)

**Layer 1 — Channel Detail** (per customer × channel raw counts):

```sql
SELECT
  IndividualIdentityLink__dlm.UnifiedRecordId__c           AS customer_id__c,
  EngagementChannel__dlm.ChannelName__c                    AS channel__c,
  COUNT(EmailEngagement__dlm.Id__c)                        AS engagement_count__c
FROM EmailEngagement__dlm
JOIN IndividualIdentityLink__dlm
  ON EmailEngagement__dlm.IndividualId__c = IndividualIdentityLink__dlm.SourceRecordId__c
JOIN EngagementChannel__dlm
  ON EmailEngagement__dlm.ChannelId__c = EngagementChannel__dlm.Id__c
GROUP BY customer_id__c, channel__c
```

**Layer 2 — Channel Summary** (NTILE bucket per channel):

```sql
SELECT
  ChannelDetail__cio.customer_id__c                        AS customer_id__c,
  ChannelDetail__cio.channel__c                            AS channel__c,
  FIRST(NTILE(3) OVER (ORDER BY MAX(ChannelDetail__cio.engagement_count__c))) AS channel_score__c
FROM ChannelDetail__cio
GROUP BY customer_id__c, channel__c
```

**Layer 3 — Overall Score** (one row per customer):

```sql
SELECT
  ChannelSummary__cio.customer_id__c                       AS customer_id__c,
  SUM(ChannelSummary__cio.channel_score__c)                AS overall_score__c
FROM ChannelSummary__cio
GROUP BY customer_id__c
```

Note `FROM ChannelDetail__cio` (not `__dlm`) — published CIs are referenced by their CIO output object name.

### Pattern 5: Birthday / Age (3-layer with leap-year handling)

```sql
SELECT
  outer_q.customer_id__c                                   AS customer_id__c,
  FIRST(outer_q.age_years__c)                              AS age__c
FROM (
  SELECT
    inner_q.customer_id__c                                 AS customer_id__c,
    inner_q.years_diff__c -
      CASE
        WHEN MONTH(CURRENT_DATE()) < inner_q.birth_month__c THEN 1
        WHEN MONTH(CURRENT_DATE()) = inner_q.birth_month__c
             AND DAY(CURRENT_DATE()) < inner_q.birth_day__c THEN 1
        ELSE 0
      END                                                  AS age_years__c
  FROM (
    SELECT
      Individual__dlm.Id__c                                AS customer_id__c,
      YEAR(CURRENT_DATE()) - YEAR(Individual__dlm.BirthDate__c) AS years_diff__c,
      MONTH(Individual__dlm.BirthDate__c)                  AS birth_month__c,
      DAY(Individual__dlm.BirthDate__c)                    AS birth_day__c
    FROM Individual__dlm
    GROUP BY customer_id__c, years_diff__c, birth_month__c, birth_day__c
  ) AS inner_q
  GROUP BY customer_id__c, age_years__c
) AS outer_q
GROUP BY outer_q.customer_id__c
```

3 layers because `DATEDIFF` cannot be used inside `CASE` and arithmetic on a date difference inside an aggregation tangles aggregate provenance.

### Pattern 6: Email Status Flags (multi-flag SUM/CASE rollup)

```sql
SELECT
  IndividualIdentityLink__dlm.UnifiedRecordId__c           AS customer_id__c,
  SUM(CASE WHEN EmailEngagement__dlm.EngagementChannelActionId__c = 'Open'   THEN 1 ELSE 0 END) AS open_count__c,
  SUM(CASE WHEN EmailEngagement__dlm.EngagementChannelActionId__c = 'Click'  THEN 1 ELSE 0 END) AS click_count__c,
  SUM(CASE WHEN EmailEngagement__dlm.EngagementChannelActionId__c = 'Unsub'  THEN 1 ELSE 0 END) AS unsub_count__c,
  SUM(CASE WHEN EmailEngagement__dlm.EngagementChannelActionId__c = 'Bounce' THEN 1 ELSE 0 END) AS bounce_count__c
FROM EmailEngagement__dlm
JOIN IndividualIdentityLink__dlm
  ON EmailEngagement__dlm.IndividualId__c = IndividualIdentityLink__dlm.SourceRecordId__c
GROUP BY customer_id__c
```

Each branch returns `1` or `0` — never `NULL` — to honor the CASE-type-consistency rule. Downstream consumers derive booleans (`open_count__c > 0`).

### Pattern 7: Shared Email with FKQ joins

```sql
SELECT
  ContactPointEmail__dlm.EmailAddress__c                   AS email__c,
  IndividualIdentityLink__dlm.UnifiedRecordId__c           AS customer_id__c,
  FIRST(RANK() OVER (
    PARTITION BY ContactPointEmail__dlm.EmailAddress__c
    ORDER BY MAX(ContactPointEmail__dlm.LastModifiedDate__c) DESC
  ))                                                       AS recency_rank__c
FROM ContactPointEmail__dlm
JOIN IndividualIdentityLink__dlm
  ON ContactPointEmail__dlm.PartyId__c = IndividualIdentityLink__dlm.SourceRecordId__c
  AND ContactPointEmail__dlm.KQ_PartyId__c = IndividualIdentityLink__dlm.KQ_SourceRecordId__c
GROUP BY email__c, customer_id__c
```

`KQ_*` predicate on every `ON` clause — required when the org provisions FKQ fields.

---

## DPE upsert key pattern

CIs that feed a Data Processing Engine (DPE) to upsert custom-object records must emit a deterministic ExternalId. Wrap the concat in `FIRST()` because the CI groups by a coarser key than the components live on.

```sql
FIRST(CONCAT(<entityId>, '~', <eventId>)) AS RecordExternalId__c,
```

- **Delimiter** — pick one (`~`, `|`, `__`) and use it across every CI in the org. Mixed delimiters defeat parsers.
- **Key components** — must produce one row per intended grain. Duplicate ExternalIds cause silent overwrites at upsert time.
- **CONCAT-aggregate trap** — see Expression-level traps above. If the components include a number, publish it as a STRING in an upstream CI before concatenating.

### Adjacent: DPE-from-CI restriction

When a DPE sources from a CI, the platform rejects ANY transformation node between Datasource and Writeback (Formula, Filter, Join, Aggregate, Append, Slice, Hierarchy). Error: *"Calculated Insights doesn't support transformations."*

Means: if you need string concatenation, derived columns, etc. on a CI-sourced DPE, you must do it INSIDE the source CI (subject to the CONCAT-aggregate trap) or surface the value as a separate field on the writeback target.

---

## Hard-coded ID fragility

Hard-coding an Id literal (RecordTypeId, queue Id, etc.) anywhere in CI SQL is a deferred failure: validates fine, runs in dev, silently returns zero rows after a sandbox refresh.

```sql
-- FRAGILE — Id changes on sandbox refresh
WHERE ssot__Account__dlm.RecordTypeId__c = '<some 18-char id>'
```

Safer:
- Filter by a stable business field (`Type`, `Status`, a `__c` enum)
- If the Id is unavoidable, externalize to config/parameter and document the swap in the deployment runbook

---

## Reading validator errors

- **Cascading flood (30+ identical lines)** — parser failed on a function name itself (e.g. unsupported `TRY_CAST`). Read the first 1-2 lines; rest is recovery-pass noise.
- **Field-resolution cascades (3-5 lines)** — column not found. The first "Cannot find type for node ..." error is the one to fix; ignore the rest.
- **JOIN-error cascades** — when an `ON` predicate references a misspelled field, downstream "function not supported" errors are usually artifacts. Fix the JOIN first.
- **`__cio.null` artifacts** — validator materializing a subquery into an internal CIO and failing to bind a type. Almost always traces back to a missing alias on an inner SELECT column.
- **Errors that spell out the workaround** — several validator messages explicitly tell you the substitute (e.g. `COUNT(DISTINCT)` → "please use APPROX_COUNT_DISTINCT", top-level `DISTINCT` → "please use it in subquery"). Follow the message.

---

## Authoring workflow

1. **Draft in Query Editor** — use CTEs, `MEDIAN`, `COALESCE`, aliases freely for rapid iteration.
2. **Convert for CI** — run through `data360-sql-convert` (or apply the conversions table above by hand).
3. **Validate in CI creation modal** — paste, click validate, fix errors before publishing.
4. **Verify aggregatability** — confirm each measure's `Y`/`N` matches the segment/activation requirements downstream.
5. **Verify the CIO** — after publish, query the `__cio` from Query Editor, check row counts and sample data.
6. **Write QA queries** — version-controlled validation SQL others can run independently.
