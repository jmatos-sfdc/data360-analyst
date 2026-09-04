---
name: data360-sql-convert
description: Convert working Query Editor (Hyper/Trino) SQL to CI editor-compatible SQL — strips aliases, rewrites CTEs as subqueries, swaps unsupported functions, fixes top-level clause restrictions. Use when user asks to "convert SQL for CI", "make this CI-ready", "CI editor compatible", or "convert query to CI".
triggers:
  - "convert SQL for CI"
  - "make this CI-ready"
  - "CI editor compatible"
  - "convert query to CI"
  - "sql convert"
  - "prepare for CI editor"
---

# Data360 SQL Convert — Query Editor → CI Editor

Mechanical transformation of working Query Editor SQL (Hyper since late 2024; legacy Trino on `queryAnsiSql`) into CI editor-compatible SQL. Run this before pasting into the CI creation modal.

For the underlying validator findings, see [`local/ci-editor-sql-research.md`](../../../local/ci-editor-sql-research.md). For full authoring guidance (patterns, hard limits, aggregatability rules), see `data360-ci-author`.

---

## Conversion rules

Apply in order — each pass cleans up shapes the next pass depends on.

### 1. Structural — top-level shape

| Query Editor / Hyper / Trino | CI Editor |
|---|---|
| `WITH name AS (...) SELECT ... FROM name` | `FROM (SELECT ... ) AS name` (subquery in FROM with alias) |
| `SELECT DISTINCT ... GROUP BY ...` | wrap in subquery: `SELECT ... FROM (SELECT DISTINCT ...) AS sub GROUP BY ...` |
| top-level `ORDER BY` | **remove** — CI output is unordered. Use a window function (`ROW_NUMBER`, `RANK`) for rank columns, sort downstream in segments/BI. |
| `EXISTS (SELECT 1 FROM x WHERE x.id = outer.id)` | `INNER JOIN x ON x.id = outer.id` (or `IN (SELECT id AS id FROM x)`) |
| `IN (SELECT col FROM x)` | `IN (SELECT col AS col_alias FROM x)` — **inner column must have an explicit alias** |
| Self-join (`FROM DMO__dlm a, DMO__dlm b ...`) | not supported — pre-build a copy in upstream CI/transform |
| Reference to DLO (`__dll`) in `FROM`/`JOIN` | use the DMO (`__dlm`) it maps to |
| `MERGE` / `INSERT` / `UPDATE` / `DELETE` / DDL | N/A — CI is SELECT-only |

### 2. Identifiers — table & alias hygiene

| Query Editor | CI Editor |
|---|---|
| `FROM ssot__Account__dlm a ... a.field` | `FROM ssot__Account__dlm ... ssot__Account__dlm.field` (no aliases) |
| `FROM "ssot__Account__dlm"` (double-quoted) | `FROM ssot__Account__dlm` |
| `'value'` or `"value"` (string literal) | `'value'` only (single quotes; double quotes are identifiers) |
| `Id__c AS Id__c` (alias = field name) | `Id__c AS customer_id__c` (alias must differ from source field name) |
| Alias matching another DMO field name | rename to avoid collision (uniqueness checked across all DMOs in the CI) |
| Output alias without `__c` suffix | append `__c` (e.g. `AS CampaignId` → `AS CampaignId__c`) |

### 3. Functions — unsupported → supported

| Query Editor | CI Editor |
|---|---|
| `MEDIAN(col)` | `PERCENTILE(col, 0.5)` |
| `PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY col)` | `PERCENTILE(col, p)` |
| `PERCENTILE_APPROX(col, p, accuracy)` | `PERCENTILE(col, p, accuracy)` |
| `PERCENTILE(col, array(0.25, 0.75))` | repeat as separate measures: `PERCENTILE(col, 0.25)`, `PERCENTILE(col, 0.75)` |
| `COUNT(*)` | `COUNT(<column>)` |
| `COUNT(DISTINCT col)` | `APPROX_COUNT_DISTINCT(col)` |
| `COALESCE(a, b, c)` | `IFNULL(a, IFNULL(b, c))` |
| `NVL(a, b)` | `IFNULL(a, b)` |
| `NVL2(a, b, c)` | `CASE WHEN a IS NOT NULL THEN b ELSE c END` |
| `DECODE(x, 1, 'A', 2, 'B', 'C')` | `CASE WHEN x = 1 THEN 'A' WHEN x = 2 THEN 'B' ELSE 'C' END` |
| `a \|\| b \|\| c` (string concat) | `CONCAT(a, b, c)` |
| `EXTRACT(YEAR FROM date)` | `YEAR(date)` (similarly `MONTH`, `DAY`, `HOUR`, `QUARTER`) |
| `DISTANCE(lat1, lng1, lat2, lng2)` | bounding-box `BETWEEN` predicates (1° lat/lng ≈ 111 km / 69 mi) |
| `TRY_CAST(x AS T)` | `CAST(x AS T)` (no graceful-null fallback — throws at runtime) |
| `REPLACE(str, old, new)` | `REGEXP_REPLACE(str, 'old', 'new')` |
| `SPLIT_PART(str, ' ', 1)` | `REGEXP_REPLACE(str, ' .*', '')` (adjust regex per delimiter) |
| `array(...)` constructor | repeat values as separate columns |

Currency:

| Query Editor | CI Editor |
|---|---|
| `SUM(amount)` on a Currency-typed field | `TRY_CONVERT_CURRENCY(SUM(amount), 'SRC_ISO', 'TARGET_ISO')` — 3-arg, target literal, all currency fields in the CI must convert to the same target |
| Mixing target currencies in one CI | not supported — split into one CI per target currency |

### 4. Expressions — CASE, window, and aggregate traps

These are syntactic shapes valid in Hyper/Trino but rejected by the CI validator. Each gets a mechanical rewrite:

```sql
-- CTE → subquery in FROM
-- BEFORE
WITH big_orders AS (
  SELECT customer_id, SUM(amount) AS total
  FROM SalesOrder__dlm GROUP BY customer_id
)
SELECT customer_id, total FROM big_orders WHERE total > 1000

-- AFTER
SELECT FIRST(big_orders.total) AS total__c, big_orders.customer_id AS customer_id__c
FROM (
  SELECT SalesOrder__dlm.customer_id__c AS customer_id,
         SUM(SalesOrder__dlm.amount__c) AS total
  FROM SalesOrder__dlm
  GROUP BY customer_id
) AS big_orders
WHERE big_orders.total > 1000
GROUP BY big_orders.customer_id
```

```sql
-- DATEDIFF inside CASE → pre-compute in subquery
-- BEFORE
SELECT customer_id,
  CASE WHEN DATEDIFF(NOW(), last_order_date) > 90 THEN 'lapsed' ELSE 'active' END AS status
FROM ...

-- AFTER
SELECT inner_q.customer_id__c AS customer_id__c,
  FIRST(CASE WHEN inner_q.days_since__c > 90 THEN 'lapsed' ELSE 'active' END) AS status__c
FROM (
  SELECT Customer__dlm.Id__c AS customer_id__c,
         DATEDIFF(NOW(), Customer__dlm.last_order_date__c) AS days_since__c
  FROM Customer__dlm
  GROUP BY customer_id__c, days_since__c
) AS inner_q
GROUP BY inner_q.customer_id__c
```

```sql
-- AVG(CASE ...) → SUM/COUNT
-- BEFORE
AVG(CASE WHEN cond THEN x ELSE NULL END)

-- AFTER
SUM(CASE WHEN cond THEN x ELSE 0 END) / NULLIF(COUNT(CASE WHEN cond THEN 1 END), 0)
```

```sql
-- CASE with mixed branch types → unify type
-- BEFORE
CASE WHEN cond THEN amount ELSE NULL END    -- numeric mixed with NULL

-- AFTER
CASE WHEN cond THEN amount ELSE 0 END        -- both numeric (use 0.0 for float)
```

```sql
-- NTILE alias reuse → repeat full expression
-- BEFORE
SELECT NTILE(3) OVER (ORDER BY MAX(score)) AS bucket,
  CASE WHEN bucket = 1 THEN 'low' WHEN bucket = 2 THEN 'mid' ELSE 'high' END AS tier
FROM ...

-- AFTER (repeat the NTILE expression in each branch)
SELECT FIRST(NTILE(3) OVER (ORDER BY MAX(score))) AS bucket__c,
  FIRST(CASE
    WHEN NTILE(3) OVER (ORDER BY MAX(score)) = 1 THEN 'low'
    WHEN NTILE(3) OVER (ORDER BY MAX(score)) = 2 THEN 'mid'
    ELSE 'high'
  END) AS tier__c
FROM ...
GROUP BY ...
```

```sql
-- Window function with bare ORDER BY → aggregate the column
-- BEFORE
ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date DESC)

-- AFTER (when SELECT contains other aggregations)
ROW_NUMBER() OVER (PARTITION BY MAX(customer_id) ORDER BY MAX(order_date) DESC)
```

### 5. JOINs — preserve outer-join semantics, add FKQ if required

```sql
-- LEFT JOIN with WHERE filter that breaks the OUTER semantics
-- BEFORE
LEFT JOIN EmailEngagement__dlm ON ...
WHERE EmailEngagement__dlm.action = 'Open'   -- this turns LEFT into INNER

-- AFTER (push filter into JOIN ON to preserve LEFT JOIN)
LEFT JOIN EmailEngagement__dlm ON ...
  AND EmailEngagement__dlm.action = 'Open'
```

```sql
-- Add KQ_* predicates when the org uses Foreign Key Qualifiers
-- BEFORE
JOIN IndividualIdentityLink__dlm
  ON ContactPointEmail__dlm.PartyId__c = IndividualIdentityLink__dlm.SourceRecordId__c

-- AFTER (FKQ-enabled org)
JOIN IndividualIdentityLink__dlm
  ON ContactPointEmail__dlm.PartyId__c = IndividualIdentityLink__dlm.SourceRecordId__c
  AND ContactPointEmail__dlm.KQ_PartyId__c = IndividualIdentityLink__dlm.KQ_SourceRecordId__c
```

Check the DMO sidecar (`object-model/dmos/<name>.yaml`) for `KQ_*` fields to determine if FKQ is required.

```sql
-- NULL keys silently exclude rows. If null-key rows must be preserved:
-- BEFORE
JOIN B ON A.key = B.key

-- AFTER
JOIN B ON IFNULL(A.key, '__missing__') = IFNULL(B.key, '__missing__')
```

### 6. WHERE / filter cleanup

| Query Editor | CI Editor |
|---|---|
| `WHERE alias_name = ...` (alias reference) | `WHERE <full expression> = ...` (no alias references in WHERE) |
| `WHERE SUM(x) > 100` (aggregate in WHERE) | move to `HAVING` (if supported) or wrap in subquery |
| `WHERE date_col = ''` (empty string for missing date) | `WHERE date_col IS NULL` |

### 7. SELECT — wrap non-grouped dimensions with FIRST()

Any column in SELECT that is not in GROUP BY and is not already an aggregate must be wrapped in `FIRST()` (or `MAX`/`MIN`). This is also the canonical pattern for **making a dimension activatable** — wrap it in `FIRST` to convert it to a measure.

```sql
-- BEFORE (Query Editor — implicit grouping or pass-through)
SELECT campaign_name, SUM(amount) AS total

-- AFTER (CI Editor — explicit FIRST)
SELECT FIRST(campaign_name) AS CampaignName__c,
       SUM(amount)            AS TotalAmount__c
```

### 8. CONCAT-aggregate provenance trap

The validator tracks aggregate provenance through expressions and across CI boundaries.

```sql
-- WRONG — provenance tracker rejects CONCAT of aggregate-derived value
FIRST(CONCAT('Count~', CAST(COUNT(x) AS STRING)))

-- WORKAROUND — split into two CIs
-- Upstream CI: publish the count as a STRING measure
SELECT CAST(COUNT(x) AS STRING) AS count_str__c, ...

-- Downstream CI: CONCAT of the published STRING field validates
SELECT CONCAT('Count~', UpstreamCI__cio.count_str__c) AS label__c, ...
```

---

## Workflow

The fastest path is `ci_convert.py` — it applies the mechanical subset of the rules above automatically and flags everything that needs human judgment. Use it first, then hand-finish what it flagged.

```bash
# Single file — converted SQL to stdout, notes to stderr
data360 ci-convert --input query.sql

# Or print a unified diff against the original
data360 ci-convert --input query.sql --diff

# Batch — convert every CI in a snapshot
data360 ci-convert --output-dir ~/Projects/clients/<Client>/Data360
# writes <root>/queries-converted/*.sql + <root>/reports/ci-convert.md
```

What `ci_convert.py` auto-fixes today:
- Identifier hygiene (DMO/CIO table aliases, double-quoted identifiers, `IN (SELECT col)` aliasing)
- Top-level `ORDER BY` removal
- Function swaps: `COALESCE`/`NVL`/`NVL2`/`DECODE`/`MEDIAN`/`EXTRACT`/`TRY_CAST`, `COUNT(DISTINCT)` → `APPROX_COUNT_DISTINCT`, `||` → `CONCAT`
- Expression rewrites: `AVG(CASE ...)` → `SUM/COUNT`, `CASE ... ELSE NULL` → typed zero

What it flags (does **not** auto-fix — apply by hand):
- Self-joins, DLO (`__dll`) refs, top-level CTEs, top-level `DISTINCT`, `EXISTS (...)`
- `DISTANCE`, `SPLIT_PART`, `REPLACE`, `TRY_CONVERT_CURRENCY` arity, multi-column `COUNT(DISTINCT)`
- `AVG(CASE ...)` with multiple WHEN branches, CASE branches mixing more than one concrete type with NULL
- FIRST() wrapping of non-grouped dimensions, LEFT-JOIN-WHERE pushdown, NTILE alias reuse, CONCAT-aggregate provenance

Hand workflow when not using the script:
1. User provides working Query Editor SQL.
2. Apply transformations 1-8 in order.
3. Present the converted SQL with a short note on any non-mechanical decisions (e.g. "split into 2 CIs because of CONCAT-aggregate trap" or "removed top-level ORDER BY — sort downstream").
4. User pastes into CI editor and validates.
5. If validation fails, read the first 1-2 error lines (cascades are noise) and apply the relevant rule.

---

## Verification checklist

After conversion, scan output for:

**Structural**
- [ ] No `WITH ... AS` CTEs (rewritten as subqueries in FROM)
- [ ] No top-level `DISTINCT` (wrapped in subquery)
- [ ] No top-level `ORDER BY` (removed or replaced with window function)
- [ ] No `EXISTS (...)` (rewritten as JOIN or `IN (SELECT col AS alias)`)
- [ ] No bare `IN (SELECT col)` — all inner columns aliased
- [ ] No DLO (`__dll`) references — replaced with mapped DMO (`__dlm`)
- [ ] No DMO appearing twice in FROM/JOIN (no self-joins)

**Identifiers**
- [ ] No table aliases (single letter or short word before a `.`)
- [ ] No double-quoted identifiers
- [ ] All string literals use single quotes
- [ ] No alias matches its source field name
- [ ] No alias collides with another DMO field name in the CI
- [ ] All output aliases end in `__c`

**Functions**
- [ ] No `MEDIAN`, `PERCENTILE_CONT`, `COALESCE`, `NVL`, `NVL2`, `DECODE`
- [ ] No `||` operator (replaced with `CONCAT`)
- [ ] No `EXTRACT(... FROM ...)` (replaced with `YEAR/MONTH/DAY/HOUR`)
- [ ] No `DISTANCE(...)` (replaced with bounding-box predicates)
- [ ] No `COUNT(*)` (replaced with `COUNT(<column>)`)
- [ ] No `COUNT(DISTINCT)` (replaced with `APPROX_COUNT_DISTINCT`)
- [ ] No `TRY_CAST` (replaced with `CAST` — flag runtime null risk)
- [ ] No `REPLACE`, `SPLIT_PART`, `array(...)` constructor
- [ ] Currency aggregates wrapped in `TRY_CONVERT_CURRENCY(amount, 'SRC', 'TGT')` — 3-arg form

**Expressions**
- [ ] No `DATEDIFF` inside `CASE` (pre-computed in subquery)
- [ ] No `AVG(CASE ...)` (rewritten as `SUM/COUNT`)
- [ ] No `CASE` branches mixing `NULL` with numeric/string types
- [ ] No `NTILE` results aliased and reused — full expression repeated in each `CASE` branch
- [ ] No `CDP*` family (CDPDAY, CDPMONTH, etc.) inside aggregations or measures
- [ ] No CONCAT of aggregate-derived numerics (split into CI-on-CI if needed)

**SELECT / GROUP BY**
- [ ] Every non-aggregate SELECT column either appears in GROUP BY or is wrapped in `FIRST/MAX/MIN`
- [ ] Every column inside `OVER(...)` is aggregated when SELECT contains other aggregates
- [ ] At least one measure (aggregate function on a field) is present
- [ ] No more than 50 measures, 10 dimensions, ~5 JOINs

**JOINs**
- [ ] WHERE-clause filters that should preserve LEFT JOIN semantics moved into `JOIN ON`
- [ ] FKQ (`KQ_*`) predicates added if the org uses them (check DMO sidecar)
- [ ] No JOIN keys that could be NULL on either side without `IFNULL` handling

Flag anything found before the user pastes.

---

## Reading validator errors after paste

If validation fails:
- **Cascading flood (30+ identical lines)** — parser failed on a function name itself. Read the first 1-2 lines.
- **Field-resolution cascade** — a column wasn't found. The first "Cannot find type for node ..." is the one to fix; ignore downstream errors.
- **JOIN-error cascade** — a misspelled field in `ON` triggers downstream "function not supported" noise. Fix the JOIN/field first.
- **Errors that spell out the workaround** — `COUNT(DISTINCT)` → "please use APPROX_COUNT_DISTINCT"; top-level `DISTINCT` → "please use it in subquery". Follow the message.
- **`__cio.null` artifact** — almost always a missing alias on an inner SELECT column.
