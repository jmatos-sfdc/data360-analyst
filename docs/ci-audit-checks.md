# `ci_audit.py` — full check list

Every check `ci_audit.py` runs is AST-based (parsed with `sqlglot`), not regex. Grouped by intent below. See the [README](../README.md#what-it-surfaces) for context and real-world examples.

## Correctness traps

- Single-day trigger equality — `col = date_add(current_date(), -N)` is a one-bad-run-and-you-miss-the-day pattern
- Leap-year bug — `MONTH(col) = MONTH(CURRENT_DATE) AND DAY(col) = DAY(CURRENT_DATE)` silently skips Feb-29 birthdays
- `CURRENT_DATE()` UTC drift — flags every call site for review against late-day US-timezone events
- Missing unsubscribe suppression — flags marketing CIs without a `LEFT JOIN` to `*_Unsubscribes__dlm`
- Dedup window grain — reports every `row_number() OVER (PARTITION BY ...)` so you can confirm the partition keys match intent
- Hardcoded RecordType IDs — string literals matching `012...` that break on sandbox refresh

## CI editor compliance (save-time validator rejections)

- Unsupported functions — `MEDIAN`, `COALESCE`, `NVL`, `NVL2`, `DECODE`, `EXTRACT`, `DISTANCE`, `TRY_CAST`, `REPLACE`, `SPLIT_PART`, etc. with the canonical CI-supported substitute
- `COUNT(DISTINCT)` — the validator rejects this with an explicit pointer to `APPROX_COUNT_DISTINCT`
- `TRY_CONVERT_CURRENCY` arity — must be the 3-arg form `(amount, 'SRC', 'TGT')`
- DLO (`__dll`) references in `FROM`/`JOIN` — only DMOs (`__dlm`) are valid
- DMO/CIO table aliases — the canvas guide explicitly prohibits aliasing; use the full name everywhere
- Self-joins (same DMO twice in one scope), top-level `DISTINCT` / `ORDER BY` / CTE, `EXISTS (...)`, unaliased `IN (SELECT col)`
- `Foo AS Foo` (alias matching the source field's bare name), `||` concat operator, double-quoted identifiers
- Expression-level traps — `DATEDIFF` inside `CASE`, `AVG(CASE ...)` nesting, `CASE` mixing NULL with concrete types, NTILE alias reuse, `CDP*` family inside aggregations, CONCAT of aggregate-derived values

## Redundancy / cleanup

- Same predicate in JOIN ON and WHERE — defensive duplication; one is dead code, and they drift independently
- Repeated derived expression (3+ uses) — same `IFNULL(...)` / `CONCAT(...)` / `REGEXP_REPLACE(...)` repeated across SELECT/JOIN/GROUP BY; candidate to lift into a column or CTE
- Filter already enforced by an inner-joined CI — cross-CI walk that flags column-to-literal predicates a downstream CI re-applies on a base DMO when the upstream CI it joins already filters the same way. One fix to the foundational CI removes redundancy across every dependent.
- Doc-recommended limits exceeded — 50 measures / 10 dimensions / ~5 JOINs per CI; refactoring trigger
