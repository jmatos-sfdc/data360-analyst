# CI SQL Convert

_Converted 8 file(s) — 8 auto-fix(es), 1 manual flag(s), 1 remaining violation(s) after conversion._

Severities:
- **auto** — converter rewrote the SQL; nothing further required.
- **flag** / **manual** — converter could not auto-fix; review before paste.
- **remaining** — the audit still flags this category on the converted output.

## Account_Parent_Hierarchy__cio.sql

**Auto-applied** (1)

- `dmo_table_alias` × 1 — Stripped DMO/CIO table alias(es) and rewrote column qualifiers: Account__dlm, Account__dlm.

**Flagged for review** (1)

- `self_join` × 1 — DMO `account__dlm` appears 2× in the same scope. Self-joins are unsupported — pre-build the second copy in an upstream CI / transform.

**Remaining audit violations** (1)

- `self_joins (1)`

## Customer_Order_Summary_QueryEditor__cio.sql

**Auto-applied** (6)

- `count_distinct` × 1 — Replaced COUNT(DISTINCT col) with APPROX_COUNT_DISTINCT(col).
- `dmo_table_alias` × 1 — Stripped DMO/CIO table alias(es) and rewrote column qualifiers: Account__dlm, SalesOrder__dlm, ssot__Individual__dlm.
- `in_subquery_unaliased` × 1 — Added explicit aliases to IN(SELECT ...) inner columns.
- `top_level_order_by` × 1 — Removed top-level ORDER BY — CI output is unordered. Sort downstream in segments / BI, or use a window function (ROW_NUMBER, RANK) for rank columns.
  - `ORDER BY total_amount__c DESC`
- `unsupported_function:coalesce` × 1 — Replaced COALESCE(...) with CI editor-supported equivalent (IFNULL(a, IFNULL(b, c)) — nest for 3+ fallbacks).
- `unsupported_function:extract` × 1 — Replaced EXTRACT(...) with CI editor-supported equivalent (YEAR(date) / MONTH(date) / DAY(date) / HOUR(date)).

## Order_Conversion_Rate__cio.sql

**Auto-applied** (1)

- `avg_case_nesting` × 1 — Rewrote AVG(CASE ...) as SUM(CASE ...) / NULLIF(COUNT(CASE ...), 0).

## Clean files

_5 file(s) needed no conversion and audit-clean post-convert:_

- Active_Customer_Accounts__cio.sql
- Birthday_Email_Audience__cio.sql
- Customer_Order_Totals__cio.sql
- Customer_Profile_Score__cio.sql
- New_Account_Welcome__cio.sql
