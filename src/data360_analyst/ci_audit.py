#!/usr/bin/env python3
"""
Data 360 CI SQL Audit
Parses CI SQL with sqlglot and walks the AST to detect known fragility patterns.

Usage (recommended — matches the rest of the toolkit):
    python3 ci_audit.py --output-dir <client-data360-folder>
    # reads from <output-dir>/queries/*.sql, writes <output-dir>/reports/ci-audit.md

Usage (explicit paths — useful for one-off reports with a custom name):
    python3 ci_audit.py --queries <dir> --output <report.md>

Example:
    python3 ci_audit.py --output-dir ~/Projects/clients/<Client>/Data360

Checks (AST-based unless noted):
    Correctness traps:
    - Single-day trigger equality: `col = date_add(current_date(), -N)`
    - Leap-year bug: `MONTH(col) = MONTH(CURRENT_DATE()) AND DAY(col) = DAY(CURRENT_DATE())`
    - UTC CURRENT_DATE() usage (flag for US-TZ review)
    - Missing unsubscribe suppression: no LEFT JOIN matching `*_Unsubscribes__dlm`
    - row_number() OVER (PARTITION BY ...) grain reporting
    - Hardcoded RecordType ID literals (fragile across sandbox refresh)

    CI editor compliance (will fail to validate / save):
    - Unsupported functions: MEDIAN, COALESCE, NVL, NVL2, DECODE, EXTRACT,
      DISTANCE, TRY_CAST, PERCENTILE_CONT, REPLACE, SPLIT_PART
    - COUNT(DISTINCT) — must be APPROX_COUNT_DISTINCT
    - String-concat operator `||` (regex on raw SQL — sqlglot normalizes to CONCAT)
    - Double-quoted identifiers (regex — sqlglot normalizes them away)
    - DLO (`__dll`) referenced in FROM/JOIN — only DMOs (`__dlm`) allowed
    - DMO table aliases (`FROM ssot__Account__dlm a`) — full DMO names only
    - Self-joins (same DMO appearing twice in the same scope's FROM/JOIN)
    - Top-level DISTINCT, ORDER BY, or WITH/CTE
    - EXISTS (...) subqueries — correlated and uncorrelated
    - IN (SELECT col FROM ...) where the inner column has no explicit alias
    - SELECT alias equal to the column's source name (`Id__c AS Id__c`)
    - TRY_CONVERT_CURRENCY arity != 3 (must be (amount, src_iso, 'TGT_ISO'))

    Redundancy / cleanup:
    - Same equality filter in JOIN ON and WHERE (defensive duplication)
    - Repeated derived expression (≥3 occurrences) — candidate to lift into a column
    - Filter also enforced by an inner-joined CI's own WHERE (cross-CI)

Exits 0 always. Findings are written to the report; severity is informational.
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import sqlglot
    from sqlglot import exp
except ImportError:
    print("ERROR: sqlglot not installed. Use the bundled .venv or `pip install sqlglot`.")
    sys.exit(1)


DIALECT = "spark"  # Data Cloud SQL is ANSI + Spark-leaning (date_add, current_date, window)


# ── AST helpers ──────────────────────────────────────────────────────────────

def _unwrap(node):
    """Strip sqlglot's TsOrDsToDate / Cast wrappers to get at the underlying expression."""
    while isinstance(node, (exp.TsOrDsToDate, exp.Cast, exp.Paren)):
        inner = node.args.get("this")
        if inner is None:
            break
        node = inner
    return node


def _is_current_date(node):
    node = _unwrap(node)
    if isinstance(node, exp.CurrentDate):
        return True
    if isinstance(node, exp.Anonymous) and node.name.lower() in ("current_date", "now"):
        return True
    return False


def _is_date_add_of_today(node):
    """`date_add(current_date(), -N)` — sqlglot 30 normalizes to TsOrDsAdd."""
    node = _unwrap(node)
    if isinstance(node, exp.TsOrDsAdd):
        base = node.args.get("this")
        delta = node.args.get("expression")
        return _is_current_date(base) and isinstance(delta, (exp.Literal, exp.Neg))
    if isinstance(node, exp.Anonymous) and node.name.lower() in ("date_add", "dateadd"):
        args = node.args.get("expressions") or []
        if len(args) >= 2:
            return _is_current_date(args[0]) and isinstance(args[1], (exp.Literal, exp.Neg))
    return False


# ── Checks ───────────────────────────────────────────────────────────────────

def check_single_day_trigger(tree):
    """`col = date_add(current_date(), -N)` — one failed run = permanent miss."""
    hits = []
    for eq in tree.find_all(exp.EQ):
        left, right = _unwrap(eq.this), _unwrap(eq.expression)
        for col_side, date_side in ((left, right), (right, left)):
            if isinstance(col_side, exp.Column) and _is_date_add_of_today(date_side):
                hits.append(eq.sql(dialect=DIALECT))
                break
    return hits


def _is_month_of(node, kind):
    """node is MONTH(x) or DAY(x); returns (is_match, inner)."""
    if kind == "month" and isinstance(node, exp.Month):
        return True, _unwrap(node.args.get("this"))
    if kind == "day" and isinstance(node, exp.Day):
        return True, _unwrap(node.args.get("this"))
    # Fallback for dialects that keep it as Anonymous
    if isinstance(node, exp.Anonymous) and node.name.lower() == kind:
        inner = (node.args.get("expressions") or [None])[0]
        return True, _unwrap(inner) if inner else (True, None)
    return False, None


def _is_part_eq_today(eq, kind):
    """EQ of shape <kind>(col) = <kind>(current_date())."""
    if not isinstance(eq, exp.EQ):
        return False
    lhs, rhs = eq.this, eq.expression
    l_match, l_inner = _is_month_of(lhs, kind)
    r_match, r_inner = _is_month_of(rhs, kind)
    if not (l_match and r_match):
        return False
    return (isinstance(l_inner, exp.Column) and _is_current_date(r_inner)) or \
           (isinstance(r_inner, exp.Column) and _is_current_date(l_inner))


def _collect_and_terms(node):
    """Flatten a chain of AND nodes into its leaves."""
    if isinstance(node, exp.And):
        return _collect_and_terms(node.this) + _collect_and_terms(node.expression)
    return [node]


def check_leap_year(tree):
    """`MONTH(col) = MONTH(CURRENT_DATE) AND DAY(col) = DAY(CURRENT_DATE)` — skips Feb-29."""
    hits = []
    for where in tree.find_all(exp.Where):
        terms = _collect_and_terms(where.this)
        has_month = any(_is_part_eq_today(t, "month") for t in terms)
        has_day = any(_is_part_eq_today(t, "day") for t in terms)
        if has_month and has_day:
            hits.append(where.sql(dialect=DIALECT))
    return hits


def check_current_date_usage(tree):
    """Count CURRENT_DATE() references — UTC clock, flag for US-TZ review."""
    count = 0
    for node in tree.walk():
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, exp.CurrentDate):
            count += 1
    return count


def check_unsubscribe_suppression(tree):
    """Presence of LEFT JOIN against `*_Unsubscribes__dlm`."""
    found = []
    for join in tree.find_all(exp.Join):
        table = join.args.get("this")
        if isinstance(table, exp.Table):
            name = (table.name or "")
            if "unsubscribe" in name.lower() and name.lower().endswith("__dlm"):
                kind = (join.args.get("side") or "").upper() or "INNER"
                found.append(f"{kind} JOIN `{name}`")
    return found


def check_row_number_partition(tree):
    """Report `row_number() OVER (PARTITION BY ...)` grain for each dedup window."""
    hits = []
    for window in tree.find_all(exp.Window):
        fn = window.args.get("this")
        is_row_number = isinstance(fn, exp.RowNumber) or \
            (isinstance(fn, exp.Anonymous) and fn.name.lower() == "row_number")
        if not is_row_number:
            continue
        parts = window.args.get("partition_by") or []
        keys = ", ".join(p.sql(dialect=DIALECT) for p in parts) if parts else "(none)"
        hits.append(f"row_number() OVER (PARTITION BY {keys})")
    return hits


# ── Redundancy / cleanup checks ──────────────────────────────────────────────

# RecordType IDs are 18-char (or 15-char) Salesforce IDs starting with '012'.
_RECORDTYPE_LITERAL = re.compile(r"^012[A-Za-z0-9]{12,15}$")


def check_hardcoded_record_type_ids(tree):
    """Find string literals that look like Salesforce RecordType IDs (`012...`).

    These break on sandbox refresh from a different source — the ID changes per
    org. Prefer joining to a RecordType DMO + filtering by DeveloperName, or
    using a portable column-level filter (e.g. `AccountTypeId IS NOT NULL`).
    """
    hits = []
    for lit in tree.find_all(exp.Literal):
        if lit.is_string and _RECORDTYPE_LITERAL.match(lit.this or ""):
            hits.append(lit.this)
    return hits


def _equality_signature(eq):
    """Canonical 'col=value' signature for an EQ between a column and a literal.

    Returns None if the EQ isn't a column-to-literal comparison. Column side is
    rendered as `Table.column` (lowercased) so JOIN ON and WHERE refs of the
    same predicate match even if written in different case.
    """
    if not isinstance(eq, exp.EQ):
        return None
    lhs, rhs = _unwrap(eq.this), _unwrap(eq.expression)
    for col_side, lit_side in ((lhs, rhs), (rhs, lhs)):
        if isinstance(col_side, exp.Column) and isinstance(lit_side, exp.Literal):
            tbl = (col_side.table or "").lower()
            col = (col_side.name or "").lower()
            return f"{tbl}.{col}={lit_side.this!r}"
    return None


def check_join_where_duplicate_filter(tree):
    """Same `col = literal` predicate present in a JOIN ON clause AND in WHERE.

    Defensive duplication. Not always a bug, but worth a review — at minimum
    one of them is dead code, and they drift independently.
    """
    join_sigs = {}
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is None:
            continue
        for term in _collect_and_terms(on):
            sig = _equality_signature(term)
            if sig:
                join_sigs[sig] = term.sql(dialect=DIALECT)

    hits = []
    seen = set()
    for where in tree.find_all(exp.Where):
        for term in _collect_and_terms(where.this):
            sig = _equality_signature(term)
            if sig and sig in join_sigs and sig not in seen:
                seen.add(sig)
                hits.append(join_sigs[sig])
    return hits


def check_repeated_derived_expressions(tree, threshold=3):
    """Same multi-token derived expression (`IFNULL`, `CONCAT`, `REGEXP_REPLACE`,
    `CAST`, etc.) appearing N+ times across SELECT/JOIN/GROUP BY/WHERE.

    Strong candidate for promoting into a column on the input CI, or wrapping
    in a CTE. Only reports expressions that actually appear in multiple
    surface clauses; one-offs in a single SELECT column are ignored.
    """
    counter = Counter()
    surface_clauses = set()
    interesting_types = (exp.Coalesce, exp.If, exp.Case, exp.Concat, exp.Cast)
    interesting_funcs = ("ifnull", "concat", "regexp_replace", "regexp_extract",
                         "coalesce", "to_char", "date_format")

    for node in tree.walk():
        n = node[0] if isinstance(node, tuple) else node
        is_interesting = isinstance(n, interesting_types) or (
            isinstance(n, exp.Anonymous) and n.name.lower() in interesting_funcs
        )
        if not is_interesting:
            continue
        s = n.sql(dialect=DIALECT)
        # Skip trivial single-arg wrappers (e.g. `CAST(col AS STRING)` is fine repeated)
        if len(s) < 25:
            continue
        counter[s] += 1

    hits = [(expr, n) for expr, n in counter.most_common() if n >= threshold]
    return hits


def _ci_filter_signatures(tree):
    """Collect column-to-literal equality predicates from JOIN ONs + WHEREs.

    Used by the cross-CI check below — if a downstream CI inner-joins this CI
    AND duplicates one of these filters, the duplicate is redundant.
    """
    sigs = set()
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None:
            for term in _collect_and_terms(on):
                sig = _equality_signature(term)
                if sig:
                    sigs.add(sig)
    for where in tree.find_all(exp.Where):
        for term in _collect_and_terms(where.this):
            sig = _equality_signature(term)
            if sig:
                sigs.add(sig)
    return sigs


def _ci_join_signatures(tree):
    """Collect column-to-literal equality predicates joined to FROM tables only.

    Specifically the predicates attached to a base DMO via JOIN ON. These are
    the ones that get duplicated when a downstream CI also filters the same
    underlying DMO via INNER JOIN — and that's the redundancy we want to flag.
    """
    sigs = []
    for join in tree.find_all(exp.Join):
        tbl = join.args.get("this")
        on = join.args.get("on")
        if not isinstance(tbl, exp.Table) or on is None:
            continue
        tbl_name = (tbl.name or "").lower()
        if not tbl_name:
            continue
        for term in _collect_and_terms(on):
            sig = _equality_signature(term)
            if sig:
                sigs.append((tbl_name, sig, term.sql(dialect=DIALECT)))
    return sigs


def check_cross_ci_redundant_filter(tree, ci_filter_index):
    """Filter on table T duplicates a filter the same DMO already has in the
    upstream CI we're INNER JOIN'ing.

    `ci_filter_index` is `{ci_name_lower: set_of_filter_signatures}` for every
    CI in the audit batch. When this CI INNER JOINs a CI in that index AND we
    also filter the same DMO with the same signature, surface it.
    """
    # First, collect which CIs this tree inner-joins.
    joined_cis = set()
    for join in tree.find_all(exp.Join):
        side = (join.args.get("side") or "").upper()
        kind = (join.args.get("kind") or "").upper()
        if side and side != "INNER":
            continue
        if kind and kind not in ("INNER", ""):
            continue
        tbl = join.args.get("this")
        if isinstance(tbl, exp.Table):
            name = (tbl.name or "").lower()
            if name.endswith("__cio") and name in ci_filter_index:
                joined_cis.add(name)

    if not joined_cis:
        return []

    # Then, every column-to-literal predicate this tree applies to a base DMO
    # is a candidate. If the upstream CI already enforces it, flag.
    hits = []
    for tbl_name, sig, raw in _ci_join_signatures(tree):
        if tbl_name.endswith("__cio"):
            continue  # don't flag CI↔CI joins, only base DMO filters
        for ci in joined_cis:
            if sig in ci_filter_index[ci]:
                hits.append((raw, ci))
                break
    return hits


# ── CI editor compliance checks ──────────────────────────────────────────────

# Functions the CI editor's bespoke validator rejects, with the canonical
# substitute. Verified live in the CI editor on 2026-05-23 (see
# local/ci-editor-sql-research.md for the full test log).
_UNSUPPORTED_FUNCTIONS = {
    "median": "PERCENTILE(col, 0.5)",
    "percentile_cont": "PERCENTILE(col, p)",
    "coalesce": "IFNULL(a, IFNULL(b, c)) — nest for 3+ fallbacks",
    "nvl": "IFNULL(a, b)",
    "nvl2": "CASE WHEN a IS NOT NULL THEN b ELSE c END",
    "decode": "CASE WHEN x = 1 THEN 'A' WHEN x = 2 THEN 'B' ELSE 'C' END",
    "extract": "YEAR(date) / MONTH(date) / DAY(date) / HOUR(date)",
    "distance": "bounding-box BETWEEN predicates (1° lat/lng ≈ 111 km)",
    "try_cast": "CAST(x AS T) — no graceful-null fallback",
    "replace": "REGEXP_REPLACE(str, 'old', 'new')",
    "split_part": "REGEXP_REPLACE(str, ' .*', '') — adjust regex per delimiter",
    "array": "repeat values as separate columns",
}


def _function_name(node):
    """Return the lowercase function name for a Func/Anonymous, or None."""
    if isinstance(node, exp.Anonymous):
        return (node.name or "").lower()
    if isinstance(node, exp.Func):
        # sqlglot has typed nodes for many functions — class name is canonical.
        cls = type(node).__name__.lower()
        # sqlglot normalizes some forms — Coalesce, Cast, etc. — to classes
        # whose name we can match against the unsupported list.
        return cls
    return None


def check_unsupported_functions(tree):
    """Functions the CI validator hard-rejects. Returns [(fn_name, suggestion)].

    Only matches `exp.Anonymous` so the function name reflects what the user
    actually wrote — sqlglot normalizes IFNULL and other safe forms into typed
    nodes like `exp.Coalesce`, which would produce false positives if matched.
    """
    hits = []
    seen = set()
    for node in tree.walk():
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, exp.Anonymous):
            name = (n.name or "").lower()
            if name in _UNSUPPORTED_FUNCTIONS and name not in seen:
                seen.add(name)
                hits.append((name.upper(), _UNSUPPORTED_FUNCTIONS[name]))
    return hits


def check_count_distinct(tree):
    """COUNT(DISTINCT col) — validator rejects with explicit pointer to
    APPROX_COUNT_DISTINCT.
    """
    hits = []
    for cnt in tree.find_all(exp.Count):
        inner = cnt.args.get("this")
        if isinstance(inner, exp.Distinct):
            hits.append(cnt.sql(dialect=DIALECT))
    return hits


def check_count_star(tree):
    """COUNT(*) — help-doc forbids. Live parser accepts (V2) but treat as
    discouraged; runtime/edge-case behavior is unverified.
    """
    hits = []
    for cnt in tree.find_all(exp.Count):
        inner = cnt.args.get("this")
        if isinstance(inner, exp.Star):
            hits.append(cnt.sql(dialect=DIALECT))
    return hits


def check_try_convert_currency_arity(tree):
    """TRY_CONVERT_CURRENCY must be 3-arg form (amount, src_iso, 'TGT_ISO').

    1-arg / 2-arg forms fail validation explicitly: "TRY_CONVERT_CURRENCY
    function has to take 3 arguments (Number, Text, Text)..."
    """
    hits = []
    for node in tree.walk():
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, exp.Anonymous) and (n.name or "").lower() == "try_convert_currency":
            args = n.args.get("expressions") or []
            if len(args) != 3:
                hits.append((n.sql(dialect=DIALECT), len(args)))
    return hits


def check_dlo_in_from(tree):
    """DLOs (`__dll`) cannot be used in CIs — only DMOs (`__dlm`)."""
    hits = []
    for tbl in tree.find_all(exp.Table):
        name = (tbl.name or "")
        if name.lower().endswith("__dll"):
            hits.append(name)
    return list(dict.fromkeys(hits))  # de-dup preserving order


def check_dmo_table_aliases(tree):
    """DMO/CIO references that have a table alias.

    Subquery aliases (`FROM (SELECT ...) AS sub`) are required and fine —
    only direct DMO/CIO aliases are flagged. Walks every Table node and
    checks if it carries an alias_or_name.
    """
    hits = []
    for tbl in tree.find_all(exp.Table):
        name = (tbl.name or "")
        alias = tbl.alias
        if not alias:
            continue
        lname = name.lower()
        # Only flag DMO and CIO aliases — those are the ones the validator rejects.
        if lname.endswith("__dlm") or lname.endswith("__cio"):
            hits.append(f"{name} AS {alias}")
    return hits


def check_self_joins(tree):
    """Same DMO/CIO referenced twice within the same SELECT scope's FROM/JOIN.

    Walks each SELECT statement and counts table references. Subquery
    occurrences in inner SELECTs are scoped separately, so this only flags
    a literal self-join at the same level.
    """
    hits = []
    for select in tree.find_all(exp.Select):
        # Count direct table references at this scope only — not nested SELECTs.
        names = []
        # FROM (sqlglot stores it as `from_` to avoid the Python keyword clash;
        # older code that used "from" silently returned None).
        from_ = select.args.get("from_") or select.args.get("from")
        if from_:
            for t in from_.find_all(exp.Table):
                # Skip references inside a nested Select within FROM.
                if any(isinstance(p, exp.Select) and p is not select
                       for p in _ancestors(t, stop=select)):
                    continue
                if t.name:
                    names.append(t.name.lower())
        # JOINs (this scope only)
        for j in select.args.get("joins") or []:
            t = j.args.get("this")
            if isinstance(t, exp.Table) and t.name:
                names.append(t.name.lower())

        seen = set()
        for n in names:
            if not (n.endswith("__dlm") or n.endswith("__cio")):
                continue
            if n in seen:
                hits.append(n)
            seen.add(n)
    return list(dict.fromkeys(hits))


def _ancestors(node, stop=None):
    """Yield ancestors of `node` up to (but not including) `stop`."""
    p = node.parent
    while p is not None and p is not stop:
        yield p
        p = p.parent


def check_top_level_distinct(tree):
    """SELECT DISTINCT at the outermost SELECT — rejected. (Inside a subquery
    is fine.)
    """
    hits = []
    # The outermost SELECT is the tree itself if it's a Select, otherwise the
    # first Select that has no Select ancestor.
    outer = tree if isinstance(tree, exp.Select) else None
    if outer is None:
        for s in tree.find_all(exp.Select):
            if not any(isinstance(a, exp.Select) for a in _ancestors(s)):
                outer = s
                break
    if outer is not None and outer.args.get("distinct"):
        hits.append(outer.sql(dialect=DIALECT)[:120] + "…")
    return hits


def check_top_level_order_by(tree):
    """ORDER BY at the outermost SELECT — rejected (CI output is unordered).
    Inside `OVER(...)` is fine.
    """
    hits = []
    outer = tree if isinstance(tree, exp.Select) else None
    if outer is None:
        for s in tree.find_all(exp.Select):
            if not any(isinstance(a, exp.Select) for a in _ancestors(s)):
                outer = s
                break
    if outer is None:
        return hits
    order = outer.args.get("order")
    if order is not None:
        # Confirm it's not nested inside a Window — find_all from outer.args
        # gives us only the direct ORDER BY, not OVER's.
        hits.append(order.sql(dialect=DIALECT))
    return hits


def check_top_level_cte(tree):
    """`WITH ... AS (...) SELECT ...` at top level — rejected. CTEs are not
    supported; rewrite as `FROM (SELECT ...) AS alias`.
    """
    hits = []
    if isinstance(tree, exp.With) or (isinstance(tree, exp.Select) and tree.args.get("with")):
        # Collect the CTE names for the message.
        with_node = tree if isinstance(tree, exp.With) else tree.args.get("with")
        for cte in with_node.expressions or []:
            alias = cte.alias_or_name
            if alias:
                hits.append(alias)
    return hits


def check_exists_subquery(tree):
    """EXISTS (...) — the validator rejects with "Exists function ... is not
    supported for now." Correlated and uncorrelated both fail.
    """
    hits = []
    for node in tree.walk():
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, exp.Exists):
            hits.append(n.sql(dialect=DIALECT)[:120])
    return hits


def check_in_subquery_unaliased(tree):
    """`IN (SELECT col FROM ...)` where the inner SELECT's column has no
    explicit alias — validator emits "name cannot be null" cascade.

    `IN (SELECT col AS col_alias FROM ...)` is required.
    """
    hits = []
    for node in tree.walk():
        n = node[0] if isinstance(node, tuple) else node
        if not isinstance(n, exp.In):
            continue
        # The right side might be a Subquery.
        sub = n.args.get("query") or n.args.get("expression")
        if not isinstance(sub, (exp.Subquery, exp.Select)):
            # Could be a list of values (`IN ('a', 'b')`) — fine.
            continue
        inner = sub.this if isinstance(sub, exp.Subquery) else sub
        if not isinstance(inner, exp.Select):
            continue
        cols = inner.expressions or []
        if len(cols) != 1:
            continue
        only = cols[0]
        # If the projection is already an Alias, we're good.
        if isinstance(only, exp.Alias):
            continue
        # Bare Column or expression — needs an alias.
        hits.append(n.sql(dialect=DIALECT)[:120])
    return hits


def check_alias_equals_field_name(tree):
    """`Id__c AS Id__c` — alias matches the source field's bare name.

    The CI editor errors with the FQK / type-binding cascade only when the
    source is a bare (unqualified) column — the parser can't tell which
    DMO's `Id__c` the downstream reference targets. Qualified references
    like `subquery_alias.col AS col` are fine because the qualifier
    disambiguates within the inner scope.
    """
    hits = []
    for alias in tree.find_all(exp.Alias):
        target = alias.this
        if not isinstance(target, exp.Column):
            continue
        if target.table:
            continue  # qualified reference — not the failure mode
        src = (target.name or "")
        out = (alias.alias or "")
        if src and out and src.lower() == out.lower():
            hits.append(f"{target.sql(dialect=DIALECT)} AS {out}")
    return hits


# Regex-based checks — sqlglot normalizes these away, so we have to scan the
# raw SQL text. Comments and string literals are stripped first.

_RX_LINE_COMMENT = re.compile(r"--[^\n]*")
_RX_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_RX_STRING_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*'")


def _strip_comments_and_strings(sql):
    """Remove comments and single-quoted literals so regex scans don't fire
    on text inside `'...'` or `/* ... */`.
    """
    sql = _RX_BLOCK_COMMENT.sub(" ", sql)
    sql = _RX_LINE_COMMENT.sub(" ", sql)
    sql = _RX_STRING_LITERAL.sub("''", sql)
    return sql


_RX_DOUBLE_PIPE = re.compile(r"\|\|")
_RX_DOUBLE_QUOTED_IDENT = re.compile(r'"[^"\n]+"')


def check_concat_operator(sql_text):
    """`a || b` — sqlglot normalizes to CONCAT, so we scan raw text.
    Strip comments and strings first to avoid matching `'a||b'` literals.
    """
    cleaned = _strip_comments_and_strings(sql_text)
    return _RX_DOUBLE_PIPE.findall(cleaned)


def check_double_quoted_identifiers(sql_text):
    """`"ssot__Account__dlm"` and `"value"` — both rejected. Double-quoted
    string literals are parsed as identifiers and fail field resolution;
    double-quoted DMO names also fail.
    """
    cleaned = _strip_comments_and_strings(sql_text)
    return _RX_DOUBLE_QUOTED_IDENT.findall(cleaned)


# ── CI editor trap detection (Pass 2) ────────────────────────────────────────


def _contains_node_type(node, node_type):
    """Check if `node` (or any descendant) contains a `node_type` instance."""
    if isinstance(node, node_type):
        return True
    for child in node.walk():
        c = child[0] if isinstance(child, tuple) else child
        if c is node:
            continue
        if isinstance(c, node_type):
            return True
    return False


def _is_datediff(node):
    if isinstance(node, exp.DateDiff):
        return True
    if isinstance(node, exp.Anonymous) and (node.name or "").lower() == "datediff":
        return True
    return False


def check_datediff_in_case(tree):
    """`CASE WHEN DATEDIFF(...) > N THEN ...` — validator rejects.

    Pre-compute DATEDIFF in an inner subquery and reference the resulting
    column in the outer CASE.
    """
    hits = []
    for case in tree.find_all(exp.Case):
        # Walk the Case's WHEN/ELSE branches looking for DateDiff.
        for child in case.walk():
            n = child[0] if isinstance(child, tuple) else child
            if n is case:
                continue
            if _is_datediff(n):
                hits.append(case.sql(dialect=DIALECT)[:140] + "…")
                break
    return hits


def check_avg_case_nesting(tree):
    """`AVG(CASE ...)` — validator rejects. Use the SUM/COUNT pattern instead."""
    hits = []
    for avg in tree.find_all(exp.Avg):
        inner = avg.args.get("this")
        if isinstance(inner, exp.Case):
            hits.append(avg.sql(dialect=DIALECT)[:140] + "…")
    return hits


def _case_branch_types(case):
    """For a CASE, return the set of high-level types observed across THEN
    and explicit ELSE branches. Classifies into 'null', 'number', 'string',
    'other'. A missing ELSE (implicit NULL) is ignored — the validator
    accepts that form.
    """
    types = set()

    def classify(node):
        if isinstance(node, exp.Null):
            return "null"
        if isinstance(node, exp.Literal):
            return "string" if node.is_string else "number"
        if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
            return "number" if not node.this.is_string else "string"
        if isinstance(node, exp.Boolean):
            return "boolean"
        return "other"

    for if_ in case.args.get("ifs") or []:
        true_branch = if_.args.get("true")
        if true_branch is not None:
            types.add(classify(true_branch))
    default = case.args.get("default")
    if default is not None:
        types.add(classify(default))
    return types


def check_case_mixed_types(tree):
    """`CASE` with a NULL branch alongside numeric/string branches — the
    validator rejects mixed-type CASE; replace NULL with `0` / `0.0` / `''`.

    Only flags cases where a literal NULL appears alongside concrete typed
    literals — not cases that mix numeric expressions across branches
    (those resolve to a common type at runtime).
    """
    hits = []
    for case in tree.find_all(exp.Case):
        types = _case_branch_types(case)
        if "null" not in types:
            continue
        concrete = types - {"null", "other"}
        if concrete:
            hits.append(case.sql(dialect=DIALECT)[:140] + "…")
    return hits


def check_ntile_alias_reuse(tree):
    """NTILE result aliased and reused in a downstream CASE — the validator
    requires the full `NTILE(n) OVER (...)` expression to be repeated in
    each CASE branch.

    Detection: find `<expr> AS alias` where the expression is/contains a
    Window with NTILE, then check whether `alias` is referenced as a Column
    elsewhere in the same SELECT scope.
    """
    hits = []
    for select in tree.find_all(exp.Select):
        # Collect aliases whose value contains a NTILE window.
        ntile_aliases = {}
        for proj in select.expressions or []:
            if not isinstance(proj, exp.Alias):
                continue
            for child in [proj.this, *(c[0] if isinstance(c, tuple) else c
                                       for c in proj.this.walk())]:
                if isinstance(child, exp.Window):
                    fn = child.args.get("this")
                    is_ntile = isinstance(fn, exp.Anonymous) and (fn.name or "").lower() == "ntile"
                    is_ntile = is_ntile or type(fn).__name__.lower() == "ntile"
                    if is_ntile:
                        ntile_aliases[(proj.alias or "").lower()] = proj.alias
                        break

        if not ntile_aliases:
            continue

        # Look for references to those aliases in CASE expressions in the
        # same SELECT scope.
        for case in select.find_all(exp.Case):
            for col in case.find_all(exp.Column):
                cname = (col.name or "").lower()
                if cname in ntile_aliases:
                    hits.append(f"{ntile_aliases[cname]} reused in CASE")
                    break
    return list(dict.fromkeys(hits))


def check_cdp_in_aggregate(tree):
    """CDP* family (CDPDAY, CDPMONTH, etc.) inside an aggregation — the
    canvas rule says these are dimension-only.
    """
    cdp_names = {"cdphour", "cdpday", "cdpmonth", "cdpquarter", "cdpyear"}
    hits = []
    aggregate_types = (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max, exp.AggFunc)
    for agg in tree.find_all(*aggregate_types):
        for child in agg.walk():
            n = child[0] if isinstance(child, tuple) else child
            if n is agg:
                continue
            if isinstance(n, exp.Anonymous) and (n.name or "").lower() in cdp_names:
                hits.append(f"{n.name.upper()} inside {type(agg).__name__.upper()}(...)")
                break
    return list(dict.fromkeys(hits))


def check_concat_aggregate_provenance(tree):
    """`CONCAT(..., CAST(<aggregate> AS STRING), ...)` — validator tracks
    aggregate provenance through CAST and rejects CONCAT of aggregate-derived
    values. Workaround: publish the aggregate as a STRING measure on an
    upstream CI, then CONCAT the published field.
    """
    hits = []
    aggregate_types = (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max,
                       exp.AggFunc, exp.Distinct)
    for cc in tree.find_all(exp.Concat):
        # Walk CONCAT's children for any CAST that wraps an aggregate.
        for child in cc.walk():
            n = child[0] if isinstance(child, tuple) else child
            if n is cc:
                continue
            if not isinstance(n, exp.Cast):
                continue
            for inner in n.walk():
                inner_n = inner[0] if isinstance(inner, tuple) else inner
                if inner_n is n:
                    continue
                if isinstance(inner_n, aggregate_types):
                    hits.append(cc.sql(dialect=DIALECT)[:140] + "…")
                    break
            else:
                continue
            break
    return list(dict.fromkeys(hits))


def check_hard_limits(tree):
    """Soft hard-limit checks against the outermost SELECT:
    - 50 measures, 10 dimensions, ~5 JOINs.

    Returns a dict with whichever limits are exceeded.
    """
    outer = tree if isinstance(tree, exp.Select) else None
    if outer is None:
        for s in tree.find_all(exp.Select):
            if not any(isinstance(a, exp.Select) for a in _ancestors(s)):
                outer = s
                break
    if outer is None:
        return {}

    measures = 0
    dimensions = 0
    aggregate_types = (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max,
                       exp.AggFunc, exp.RowNumber)
    for proj in outer.expressions or []:
        body = proj.this if isinstance(proj, exp.Alias) else proj
        if any(isinstance(c[0] if isinstance(c, tuple) else c, aggregate_types)
               or isinstance(c[0] if isinstance(c, tuple) else c, exp.Window)
               for c in body.walk()):
            measures += 1
        else:
            dimensions += 1

    # Count JOINs at the outer scope only.
    joins = len(outer.args.get("joins") or [])

    findings = {}
    if measures > 50:
        findings["measures"] = measures
    if dimensions > 10:
        findings["dimensions"] = dimensions
    if joins > 5:
        findings["joins"] = joins
    return findings


# ── Driver ───────────────────────────────────────────────────────────────────

def parse_file(path):
    """Parse a CI SQL file. Returns (trees, raw_sql, error). One of (trees,
    error) is always None.
    """
    sql = path.read_text()
    try:
        return sqlglot.parse(sql, read=DIALECT), sql, None
    except Exception as e:
        return None, sql, str(e)


# ── Self-verifying confirm queries ────────────────────────────────────────────
#
# A static check proves a *pattern* is present; it can't prove the pattern
# actually bites this org's data. For the data-dependent traps we emit a small
# Query-Editor-runnable SQL the user can paste into their own org to confirm (or
# dismiss) the finding — turning "you might have a leap-year bug" into "run this;
# if it returns > 0 rows, you do." Queries are for the Query Editor, not the CI
# editor, so full DMO names + plain SQL are fine.


def _source_table(col_node, tree):
    """Resolve the real table a column belongs to: its qualifier if present
    (mapping an alias back to the underlying DMO so the confirm query is
    runnable), else the query's first FROM/JOIN table (single-source CIs, the
    common case). Returns None when nothing resolves."""
    alias_map = {t.alias: t.name for t in tree.find_all(exp.Table) if t.alias and t.name}
    qualifier = getattr(col_node, "table", None)
    if qualifier:
        return alias_map.get(qualifier, qualifier)
    tables = [t.name for t in tree.find_all(exp.Table) if t.name]
    return tables[0] if tables else None


def _leap_year_confirm(tree):
    """SELECT counting Feb-29 rows the MONTH()/DAY() equality would skip."""
    out = []
    for where in tree.find_all(exp.Where):
        terms = _collect_and_terms(where.this)
        if not any(_is_part_eq_today(t, "month") for t in terms):
            continue
        day_col = None
        for t in terms:
            if not _is_part_eq_today(t, "day"):
                continue
            for side in (t.this, t.expression):
                matched, inner = _is_month_of(side, "day")
                if matched and isinstance(inner, exp.Column):
                    day_col = inner
        if day_col is None:
            continue
        table = _source_table(day_col, tree)
        if not table:
            continue
        col = day_col.name
        out.append({
            "finding": "leap_year",
            "sql": f"SELECT COUNT(*) AS feb29_rows\nFROM {table}\n"
                   f"WHERE MONTH({col}) = 2 AND DAY({col}) = 29;",
            "expect": "feb29_rows > 0 means those records are silently skipped every non-leap year.",
        })
    return out


def _single_day_confirm(tree):
    """Show which days the `col = date_add(current_date(), -N)` filter captures
    over the last week — a gap = the day a failed run would permanently drop."""
    out = []
    for eq in tree.find_all(exp.EQ):
        left, right = _unwrap(eq.this), _unwrap(eq.expression)
        col_node = None
        for col_side, date_side in ((left, right), (right, left)):
            if isinstance(col_side, exp.Column) and _is_date_add_of_today(date_side):
                col_node = col_side
                break
        if col_node is None:
            continue
        table = _source_table(col_node, tree)
        if not table:
            continue
        col = col_node.name
        out.append({
            "finding": "single_day_trigger",
            "sql": f"SELECT {col}, COUNT(*) AS rows_on_day\nFROM {table}\n"
                   f"WHERE {col} >= DATE_ADD(CURRENT_DATE(), -7)\n"
                   f"GROUP BY {col}\nORDER BY {col};",
            "expect": "a missing day in the last week = a run that skipped that day dropped those rows for good.",
        })
    return out


def _record_type_confirm(tree):
    """For each hardcoded 012... literal, count rows it matches in THIS org."""
    out = []
    for eq in tree.find_all(exp.EQ):
        lhs, rhs = _unwrap(eq.this), _unwrap(eq.expression)
        for col_side, lit_side in ((lhs, rhs), (rhs, lhs)):
            if (isinstance(col_side, exp.Column) and isinstance(lit_side, exp.Literal)
                    and lit_side.is_string and _RECORDTYPE_LITERAL.match(lit_side.this or "")):
                table = _source_table(col_side, tree)
                if not table:
                    continue
                col = col_side.name
                out.append({
                    "finding": "hardcoded_record_type_ids",
                    "sql": f"SELECT COUNT(*) AS rows_matching\nFROM {table}\n"
                           f"WHERE {col} = '{lit_side.this}';",
                    "expect": "rows_matching = 0 means this ID doesn't exist in this org — "
                              "the CI silently returns nothing (breaks after a sandbox refresh).",
                })
    return out


def build_confirm_queries(tree):
    """Runnable confirm queries for the data-dependent findings in one tree."""
    queries = []
    queries += _leap_year_confirm(tree)
    queries += _single_day_confirm(tree)
    queries += _record_type_confirm(tree)
    return queries


def audit_file(trees, raw_sql, ci_filter_index=None):
    findings = {
        # Existing checks.
        "single_day_trigger": [],
        "leap_year": [],
        "current_date_calls": 0,
        "unsubscribe_joins": [],
        "row_number_partitions": [],
        "hardcoded_record_type_ids": [],
        "join_where_duplicate_filter": [],
        "repeated_derived_expressions": [],
        "cross_ci_redundant_filter": [],
        # CI editor compliance — Pass 1.
        "unsupported_functions": [],
        "count_distinct": [],
        "count_star": [],
        "try_convert_currency_arity": [],
        "dlo_in_from": [],
        "dmo_table_aliases": [],
        "self_joins": [],
        "top_level_distinct": [],
        "top_level_order_by": [],
        "top_level_cte": [],
        "exists_subquery": [],
        "in_subquery_unaliased": [],
        "alias_equals_field_name": [],
        "concat_operator": [],
        "double_quoted_identifiers": [],
        # CI editor compliance — Pass 2.
        "datediff_in_case": [],
        "avg_case_nesting": [],
        "case_mixed_types": [],
        "ntile_alias_reuse": [],
        "cdp_in_aggregate": [],
        "concat_aggregate_provenance": [],
        "hard_limits": {},
        # Runnable confirm queries for data-dependent findings (self-verifying).
        "confirm_queries": [],
    }
    for tree in trees:
        if tree is None:
            continue
        # Existing.
        findings["single_day_trigger"] += check_single_day_trigger(tree)
        findings["leap_year"] += check_leap_year(tree)
        findings["current_date_calls"] += check_current_date_usage(tree)
        findings["unsubscribe_joins"] += check_unsubscribe_suppression(tree)
        findings["row_number_partitions"] += check_row_number_partition(tree)
        findings["hardcoded_record_type_ids"] += check_hardcoded_record_type_ids(tree)
        findings["join_where_duplicate_filter"] += check_join_where_duplicate_filter(tree)
        findings["repeated_derived_expressions"] += check_repeated_derived_expressions(tree)
        if ci_filter_index is not None:
            findings["cross_ci_redundant_filter"] += check_cross_ci_redundant_filter(tree, ci_filter_index)
        # CI editor compliance — Pass 1.
        findings["unsupported_functions"] += check_unsupported_functions(tree)
        findings["count_distinct"] += check_count_distinct(tree)
        findings["count_star"] += check_count_star(tree)
        findings["try_convert_currency_arity"] += check_try_convert_currency_arity(tree)
        findings["dlo_in_from"] += check_dlo_in_from(tree)
        findings["dmo_table_aliases"] += check_dmo_table_aliases(tree)
        findings["self_joins"] += check_self_joins(tree)
        findings["top_level_distinct"] += check_top_level_distinct(tree)
        findings["top_level_order_by"] += check_top_level_order_by(tree)
        findings["top_level_cte"] += check_top_level_cte(tree)
        findings["exists_subquery"] += check_exists_subquery(tree)
        findings["in_subquery_unaliased"] += check_in_subquery_unaliased(tree)
        findings["alias_equals_field_name"] += check_alias_equals_field_name(tree)
        # CI editor compliance — Pass 2.
        findings["datediff_in_case"] += check_datediff_in_case(tree)
        findings["avg_case_nesting"] += check_avg_case_nesting(tree)
        findings["case_mixed_types"] += check_case_mixed_types(tree)
        findings["ntile_alias_reuse"] += check_ntile_alias_reuse(tree)
        findings["cdp_in_aggregate"] += check_cdp_in_aggregate(tree)
        findings["concat_aggregate_provenance"] += check_concat_aggregate_provenance(tree)
        # Take the largest hard-limit hit across statements.
        for k, v in check_hard_limits(tree).items():
            if v > findings["hard_limits"].get(k, 0):
                findings["hard_limits"][k] = v
        # Self-verifying confirm queries for the data-dependent findings.
        findings["confirm_queries"] += build_confirm_queries(tree)

    # Regex-based checks operate on raw SQL once per file, not per-tree.
    findings["concat_operator"] = check_concat_operator(raw_sql)
    findings["double_quoted_identifiers"] = check_double_quoted_identifiers(raw_sql)
    return findings


def build_ci_filter_index(parsed):
    """Map `<ci_name_lowercased>` → set of column-to-literal predicate signatures.

    `parsed` is `{filename: trees_or_None}`. CI name is derived from the filename
    stem (e.g. `Common_Owner_Hierarchy__cio.sql` → `common_owner_hierarchy__cio`).
    """
    index = {}
    for fname, trees in parsed.items():
        if not trees:
            continue
        stem = Path(fname).stem.lower()
        if not stem.endswith("__cio"):
            continue
        sigs = set()
        for tree in trees:
            if tree is not None:
                sigs |= _ci_filter_signatures(tree)
        index[stem] = sigs
    return index


def format_report(results):
    lines = ["# CI SQL Audit", ""]
    lines.append(f"_Scanned {len(results)} SQL file(s)_")
    lines.append("")

    # Summary
    totals = defaultdict(int)
    for f in results.values():
        if "parse_error" in f:
            totals["parse_errors"] += 1
            continue
        if f["single_day_trigger"]:
            totals["single_day_trigger"] += 1
        if f["leap_year"]:
            totals["leap_year"] += 1
        if f["current_date_calls"] and not f["unsubscribe_joins"]:
            pass  # not itself a finding
        if not f["unsubscribe_joins"]:
            totals["missing_unsubscribe_join"] += 1

    # CI-editor-compliance keys — any non-empty (or non-zero limit) counts.
    compliance_keys = (
        "unsupported_functions", "count_distinct", "count_star",
        "try_convert_currency_arity", "dlo_in_from", "dmo_table_aliases",
        "self_joins", "top_level_distinct", "top_level_order_by",
        "top_level_cte", "exists_subquery", "in_subquery_unaliased",
        "alias_equals_field_name", "concat_operator",
        "double_quoted_identifiers", "datediff_in_case", "avg_case_nesting",
        "case_mixed_types", "ntile_alias_reuse", "cdp_in_aggregate",
        "concat_aggregate_provenance",
    )
    for f in results.values():
        if "parse_error" in f:
            continue
        if f.get("hardcoded_record_type_ids"):
            totals["hardcoded_record_type_ids"] += 1
        if f.get("join_where_duplicate_filter"):
            totals["join_where_duplicate_filter"] += 1
        if f.get("repeated_derived_expressions"):
            totals["repeated_derived_expressions"] += 1
        if f.get("cross_ci_redundant_filter"):
            totals["cross_ci_redundant_filter"] += 1
        for k in compliance_keys:
            if f.get(k):
                totals[k] += 1
        if f.get("hard_limits"):
            totals["hard_limits"] += 1

    lines.append("## Summary")
    lines.append("")
    lines.append("### Correctness traps")
    lines.append(f"- Files with single-day trigger equality: **{totals['single_day_trigger']}**")
    lines.append(f"- Files with leap-year `MONTH()/DAY()` pattern: **{totals['leap_year']}**")
    lines.append(f"- Files with no `*_Unsubscribes__dlm` LEFT JOIN: **{totals['missing_unsubscribe_join']}** "
                 "(marketing CIs only — transactional CIs are exempt)")
    lines.append(f"- Files with hardcoded RecordType ID literals: **{totals['hardcoded_record_type_ids']}**")
    lines.append("")
    lines.append("### CI editor compliance")
    lines.append("Patterns that fail the CI editor's bespoke validator at save time. "
                 "Each one was confirmed live in 2026-05-23 testing or is in the "
                 "internal CI SQL Builder canvas — see `local/ci-editor-sql-research.md`.")
    lines.append("")
    lines.append(f"- Files using unsupported functions (MEDIAN, COALESCE, NVL, etc.): **{totals['unsupported_functions']}**")
    lines.append(f"- Files using `COUNT(DISTINCT)`: **{totals['count_distinct']}**")
    lines.append(f"- Files using `COUNT(*)` (discouraged by docs, accepted by parser): **{totals['count_star']}**")
    lines.append(f"- Files with `TRY_CONVERT_CURRENCY` wrong arity (must be 3-arg): **{totals['try_convert_currency_arity']}**")
    lines.append(f"- Files referencing DLOs (`__dll`) in FROM/JOIN: **{totals['dlo_in_from']}**")
    lines.append(f"- Files with DMO/CIO table aliases: **{totals['dmo_table_aliases']}**")
    lines.append(f"- Files with self-joins (DMO appears twice in same scope): **{totals['self_joins']}**")
    lines.append(f"- Files with top-level `DISTINCT`: **{totals['top_level_distinct']}**")
    lines.append(f"- Files with top-level `ORDER BY`: **{totals['top_level_order_by']}**")
    lines.append(f"- Files with top-level CTE (`WITH ... AS`): **{totals['top_level_cte']}**")
    lines.append(f"- Files with `EXISTS (...)` subqueries: **{totals['exists_subquery']}**")
    lines.append(f"- Files with `IN (SELECT col)` (unaliased inner column): **{totals['in_subquery_unaliased']}**")
    lines.append(f"- Files with `Foo AS Foo` (alias = source field name): **{totals['alias_equals_field_name']}**")
    lines.append(f"- Files using `||` string-concat operator: **{totals['concat_operator']}**")
    lines.append(f"- Files with double-quoted identifiers: **{totals['double_quoted_identifiers']}**")
    lines.append(f"- Files with `DATEDIFF` inside `CASE`: **{totals['datediff_in_case']}**")
    lines.append(f"- Files with `AVG(CASE ...)` nesting: **{totals['avg_case_nesting']}**")
    lines.append(f"- Files with `CASE` mixing NULL with concrete types: **{totals['case_mixed_types']}**")
    lines.append(f"- Files reusing an aliased `NTILE` result in `CASE`: **{totals['ntile_alias_reuse']}**")
    lines.append(f"- Files with `CDP*` family inside an aggregation: **{totals['cdp_in_aggregate']}**")
    lines.append(f"- Files with CONCAT-aggregate provenance trap: **{totals['concat_aggregate_provenance']}**")
    lines.append("")
    lines.append("### Redundancy / cleanup")
    lines.append(f"- Files with same predicate in JOIN ON and WHERE: **{totals['join_where_duplicate_filter']}**")
    lines.append(f"- Files with a derived expression repeated 3+ times: **{totals['repeated_derived_expressions']}**")
    lines.append(f"- Files duplicating a filter their input CI already enforces: **{totals['cross_ci_redundant_filter']}**")
    lines.append(f"- Files exceeding doc-recommended limits (50 measures / 10 dimensions / 5 JOINs): **{totals['hard_limits']}**")
    if totals["parse_errors"]:
        lines.append(f"- Files sqlglot could not parse: **{totals['parse_errors']}**")
    lines.append("")

    # Per file
    lines.append("## Per-file findings")
    lines.append("")
    for name in sorted(results):
        f = results[name]
        lines.append(f"### `{name}`")
        if "parse_error" in f:
            lines.append(f"- ⚠ sqlglot parse error: `{f['parse_error']}`")
            lines.append("")
            continue

        any_finding = False

        if f["single_day_trigger"]:
            any_finding = True
            lines.append("- **Single-day trigger equality** — a single failed run = permanent miss. "
                         "Prefer `>= ... AND <` ranges.")
            for h in f["single_day_trigger"]:
                lines.append(f"  - {h}")

        if f["leap_year"]:
            any_finding = True
            lines.append("- **Leap-year bug** — `MONTH()/DAY()` equality skips Feb-29 birthdays in non-leap years.")
            for h in f["leap_year"]:
                lines.append(f"  - `{h}`")

        if f["current_date_calls"]:
            lines.append(f"- `CURRENT_DATE()` called {f['current_date_calls']} time(s) — "
                         "evaluates in UTC; verify intent for late-day US events.")
            any_finding = True

        if f["unsubscribe_joins"]:
            lines.append("- Unsubscribe suppression join present:")
            for h in f["unsubscribe_joins"]:
                lines.append(f"  - {h}")
        else:
            lines.append("- ⚠ No `*_Unsubscribes__dlm` LEFT JOIN found — confirm this CI is transactional (exempt).")
            any_finding = True

        if f["row_number_partitions"]:
            lines.append("- Dedup window(s):")
            for h in f["row_number_partitions"]:
                lines.append(f"  - `{h}`")

        if f.get("hardcoded_record_type_ids"):
            any_finding = True
            lines.append("- **Hardcoded RecordType ID(s)** — break on sandbox refresh; "
                         "join to a RecordType DMO + filter on `DeveloperName` "
                         "or use a portable column-level filter instead.")
            for h in f["hardcoded_record_type_ids"]:
                lines.append(f"  - `'{h}'`")

        if f.get("join_where_duplicate_filter"):
            any_finding = True
            lines.append("- **Same predicate in JOIN ON and WHERE** — defensive duplication; "
                         "drop one (or confirm both are intentional).")
            for h in f["join_where_duplicate_filter"]:
                lines.append(f"  - `{h}`")

        if f.get("repeated_derived_expressions"):
            any_finding = True
            lines.append("- **Repeated derived expression(s)** — candidate to lift into a "
                         "column on the input CI or a CTE.")
            for expr, n in f["repeated_derived_expressions"]:
                lines.append(f"  - {n}× `{expr}`")

        if f.get("cross_ci_redundant_filter"):
            any_finding = True
            lines.append("- **Filter duplicates one already enforced by an inner-joined CI** — "
                         "the upstream CI's WHERE/ON already applies this predicate, so the "
                         "duplicate is dead code that drifts independently.")
            for raw, ci in f["cross_ci_redundant_filter"]:
                lines.append(f"  - `{raw}` (already in `{ci}`)")

        # CI editor compliance findings.
        if f.get("unsupported_functions"):
            any_finding = True
            lines.append("- **Unsupported function(s)** — CI validator rejects:")
            for fn, suggestion in f["unsupported_functions"]:
                lines.append(f"  - `{fn}` → use `{suggestion}`")

        if f.get("count_distinct"):
            any_finding = True
            lines.append("- **`COUNT(DISTINCT)` — rejected by validator.** Use `APPROX_COUNT_DISTINCT(col)`.")
            for h in f["count_distinct"]:
                lines.append(f"  - `{h}`")

        if f.get("count_star"):
            any_finding = True
            lines.append("- **`COUNT(*)` — discouraged.** Help-doc forbids it; live parser accepts it. "
                         "Prefer `COUNT(<column>)`.")
            for h in f["count_star"]:
                lines.append(f"  - `{h}`")

        if f.get("try_convert_currency_arity"):
            any_finding = True
            lines.append("- **`TRY_CONVERT_CURRENCY` wrong arity** — must be 3-arg "
                         "`(amount, source_iso, 'TARGET_ISO')`.")
            for h, n_args in f["try_convert_currency_arity"]:
                lines.append(f"  - {n_args}-arg call: `{h}`")

        if f.get("dlo_in_from"):
            any_finding = True
            lines.append("- **DLO referenced in FROM/JOIN** — DLOs (`__dll`) cannot be CI sources. "
                         "Use the DMO (`__dlm`) the DLO maps to.")
            for h in f["dlo_in_from"]:
                lines.append(f"  - `{h}`")

        if f.get("dmo_table_aliases"):
            any_finding = True
            lines.append("- **DMO/CIO table alias(es)** — use full DMO names everywhere; "
                         "aliasing surfaces ambiguous-column errors.")
            for h in f["dmo_table_aliases"]:
                lines.append(f"  - `{h}`")

        if f.get("self_joins"):
            any_finding = True
            lines.append("- **Self-join detected** — the same DMO appearing twice at the same scope is "
                         "not supported. Pre-build a copy in an upstream CI/transform.")
            for h in f["self_joins"]:
                lines.append(f"  - `{h}`")

        if f.get("top_level_distinct"):
            any_finding = True
            lines.append("- **Top-level `DISTINCT`** — rejected. Wrap in subquery: "
                         "`FROM (SELECT DISTINCT ...) AS sub`.")

        if f.get("top_level_order_by"):
            any_finding = True
            lines.append("- **Top-level `ORDER BY`** — rejected (CI output is unordered). "
                         "Use a window function for rank columns or sort downstream.")
            for h in f["top_level_order_by"]:
                lines.append(f"  - `{h}`")

        if f.get("top_level_cte"):
            any_finding = True
            lines.append("- **Top-level CTE (`WITH ... AS`)** — rejected. "
                         "Rewrite as `FROM (SELECT ...) AS alias`.")
            for h in f["top_level_cte"]:
                lines.append(f"  - `{h}`")

        if f.get("exists_subquery"):
            any_finding = True
            lines.append("- **`EXISTS (...)` subquery** — rejected with "
                         "\"Exists function ... is not supported for now.\" "
                         "Rewrite as `INNER JOIN` or `IN (SELECT col AS alias)`.")
            for h in f["exists_subquery"]:
                lines.append(f"  - `{h}`")

        if f.get("in_subquery_unaliased"):
            any_finding = True
            lines.append("- **`IN (SELECT col)` without alias** — inner column needs an explicit "
                         "`AS alias` for the validator to bind its type.")
            for h in f["in_subquery_unaliased"]:
                lines.append(f"  - `{h}`")

        if f.get("alias_equals_field_name"):
            any_finding = True
            lines.append("- **Alias equals source field name** — `Id__c AS Id__c` errors; "
                         "rename to differ from the source.")
            for h in f["alias_equals_field_name"]:
                lines.append(f"  - `{h}`")

        if f.get("concat_operator"):
            any_finding = True
            lines.append(f"- **`||` string-concat operator** ({len(f['concat_operator'])}× occurrence) — "
                         "rejected. Use `CONCAT(...)`.")

        if f.get("double_quoted_identifiers"):
            any_finding = True
            lines.append(f"- **Double-quoted identifier(s)** ({len(f['double_quoted_identifiers'])}× occurrence) — "
                         "use single quotes for string literals; remove quotes from DMO names.")
            for h in f["double_quoted_identifiers"][:5]:
                lines.append(f"  - `{h}`")

        if f.get("datediff_in_case"):
            any_finding = True
            lines.append("- **`DATEDIFF` inside `CASE`** — rejected. Pre-compute "
                         "`DATEDIFF(...) AS days_diff__c` in an inner subquery.")
            for h in f["datediff_in_case"]:
                lines.append(f"  - `{h}`")

        if f.get("avg_case_nesting"):
            any_finding = True
            lines.append("- **`AVG(CASE ...)` nesting** — rejected. Use "
                         "`SUM(CASE ...) / NULLIF(COUNT(CASE ...), 0)` instead.")
            for h in f["avg_case_nesting"]:
                lines.append(f"  - `{h}`")

        if f.get("case_mixed_types"):
            any_finding = True
            lines.append("- **`CASE` branches mix NULL with concrete types** — replace NULL "
                         "branch with `0` (number) / `0.0` (float) / `''` (string).")
            for h in f["case_mixed_types"]:
                lines.append(f"  - `{h}`")

        if f.get("ntile_alias_reuse"):
            any_finding = True
            lines.append("- **`NTILE` alias reused in `CASE`** — repeat the full "
                         "`NTILE(n) OVER (...)` expression in each CASE branch.")
            for h in f["ntile_alias_reuse"]:
                lines.append(f"  - `{h}`")

        if f.get("cdp_in_aggregate"):
            any_finding = True
            lines.append("- **`CDP*` family inside an aggregation** — `CDPDAY`/`CDPMONTH`/etc. "
                         "are dimension-only.")
            for h in f["cdp_in_aggregate"]:
                lines.append(f"  - `{h}`")

        if f.get("concat_aggregate_provenance"):
            any_finding = True
            lines.append("- **CONCAT-aggregate provenance trap** — `CONCAT(..., CAST(<aggregate> AS STRING), ...)` "
                         "fails. Publish the aggregate as a STRING measure on an upstream CI, then CONCAT "
                         "the published field downstream.")
            for h in f["concat_aggregate_provenance"]:
                lines.append(f"  - `{h}`")

        if f.get("hard_limits"):
            any_finding = True
            lines.append("- **Doc-recommended limit exceeded** — refactoring candidate; "
                         "split the CI by grain or channel:")
            hl = f["hard_limits"]
            if "measures" in hl:
                lines.append(f"  - {hl['measures']} measures (limit: 50)")
            if "dimensions" in hl:
                lines.append(f"  - {hl['dimensions']} dimensions (limit: 10)")
            if "joins" in hl:
                lines.append(f"  - {hl['joins']} JOINs (soft limit: 5)")

        if f.get("confirm_queries"):
            # Dedupe identical queries (same trap repeated across statements).
            seen_sql = set()
            unique = []
            for q in f["confirm_queries"]:
                if q["sql"] not in seen_sql:
                    seen_sql.add(q["sql"])
                    unique.append(q)
            lines.append("- **Confirm against your org** — run in the Query Editor to verify "
                         "the data-dependent findings above are real, not just pattern matches:")
            for q in unique:
                lines.append(f"  - _{q['finding']}_ — {q['expect']}")
                lines.append("")
                lines.append("    ```sql")
                for sql_line in q["sql"].splitlines():
                    lines.append(f"    {sql_line}")
                lines.append("    ```")

        if not any_finding:
            lines.append("- No issues detected.")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Audit CI SQL files for known fragility patterns",
        epilog=(
            "Pass --output-dir <root> to use the toolkit's standard layout "
            "(reads <root>/queries/*.sql, writes <root>/reports/ci-audit.md). "
            "Or pass --queries and --output for explicit paths."
        ),
    )
    parser.add_argument("--output-dir", help="Client Data360 root — derives queries/ and reports/ paths")
    parser.add_argument("--queries", help="Directory of .sql files (overrides --output-dir)")
    parser.add_argument("--output", help="Path to write the markdown report (overrides --output-dir)")
    parser.add_argument(
        "--fix",
        action="store_true",
        help=(
            "After auditing, run ci_convert.py in batch mode against the same "
            "queries dir. Writes <root>/queries-converted/*.sql and "
            "<root>/reports/ci-convert.md. Requires --output-dir."
        ),
    )
    args = parser.parse_args()

    if not args.output_dir and not (args.queries and args.output):
        parser.error("provide --output-dir, or both --queries and --output")
    if args.fix and not args.output_dir:
        parser.error("--fix requires --output-dir (uses the standard layout)")

    if args.queries:
        q_dir = Path(args.queries).expanduser()
    else:
        q_dir = Path(args.output_dir).expanduser() / "queries"

    if args.output:
        out_path = Path(args.output).expanduser()
    else:
        out_path = Path(args.output_dir).expanduser() / "reports" / "ci-audit.md"

    if not q_dir.is_dir():
        print(f"ERROR: queries dir not found: {q_dir}")
        sys.exit(1)

    # Pass 1: parse every file. Failures get recorded as parse_error in the
    # results so they still surface in the report. Keep the raw SQL too —
    # the regex-based compliance checks need it.
    parsed = {}
    raw_sql = {}
    parse_errors = {}
    for sql in sorted(q_dir.glob("*.sql")):
        trees, raw, err = parse_file(sql)
        raw_sql[sql.name] = raw
        if err is not None:
            parse_errors[sql.name] = err
        else:
            parsed[sql.name] = trees

    # Pass 2: build the cross-CI filter index from successfully-parsed CIs.
    ci_filter_index = build_ci_filter_index(parsed)

    # Pass 3: audit each file with the index in hand.
    results = {name: {"parse_error": err} for name, err in parse_errors.items()}
    for name, trees in parsed.items():
        results[name] = audit_file(trees, raw_sql[name], ci_filter_index=ci_filter_index)

    if not results:
        print(f"No .sql files under {q_dir}")
        sys.exit(0)

    report = format_report(results)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"Wrote {out_path}  ({len(results)} file(s) scanned)")

    if args.fix:
        # Lazy-import to keep ci_audit standalone (ci_convert already imports
        # back into ci_audit for the post-convert audit re-run).
        from ci_convert import _audit_converted, convert_file, format_notes_report

        root = Path(args.output_dir).expanduser()
        out_dir = root / "queries-converted"
        report_path = root / "reports" / "ci-convert.md"
        out_dir.mkdir(parents=True, exist_ok=True)

        convert_results = {}
        for sql in sorted(q_dir.glob("*.sql")):
            r = convert_file(sql)
            r.remaining_violations = _audit_converted(r.converted_sql)
            convert_results[sql.name] = r
            (out_dir / sql.name).write_text(r.converted_sql)
        report_path.write_text(format_notes_report(convert_results))
        total_auto = sum(r.auto_count() for r in convert_results.values())
        total_flag = sum(r.flag_count() for r in convert_results.values())
        total_remaining = sum(len(r.remaining_violations) for r in convert_results.values())
        print(
            f"Converted {len(convert_results)} file(s) → {out_dir}\n"
            f"  auto-fixes: {total_auto}\n"
            f"  flagged:    {total_flag}\n"
            f"  remaining:  {total_remaining}\n"
            f"  report:     {report_path}"
        )


if __name__ == "__main__":
    main()
