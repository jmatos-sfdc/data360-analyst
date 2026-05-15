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

Checks (AST-based, not regex):
    - Single-day trigger equality: `col = date_add(current_date(), -N)`
    - Leap-year bug: `MONTH(col) = MONTH(CURRENT_DATE()) AND DAY(col) = DAY(CURRENT_DATE())`
    - UTC CURRENT_DATE() usage (flag for US-TZ review)
    - Missing unsubscribe suppression: no LEFT JOIN matching `*_Unsubscribes__dlm`
    - row_number() OVER (PARTITION BY ...) grain reporting

Exits 0 always. Findings are written to the report; severity is informational.
"""

import argparse
import sys
from collections import defaultdict
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


# ── Driver ───────────────────────────────────────────────────────────────────

def audit_file(path):
    sql = path.read_text()
    try:
        trees = sqlglot.parse(sql, read=DIALECT)
    except Exception as e:
        return {"parse_error": str(e)}

    findings = {
        "single_day_trigger": [],
        "leap_year": [],
        "current_date_calls": 0,
        "unsubscribe_joins": [],
        "row_number_partitions": [],
    }
    for tree in trees:
        if tree is None:
            continue
        findings["single_day_trigger"] += check_single_day_trigger(tree)
        findings["leap_year"] += check_leap_year(tree)
        findings["current_date_calls"] += check_current_date_usage(tree)
        findings["unsubscribe_joins"] += check_unsubscribe_suppression(tree)
        findings["row_number_partitions"] += check_row_number_partition(tree)
    return findings


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

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Files with single-day trigger equality: **{totals['single_day_trigger']}**")
    lines.append(f"- Files with leap-year `MONTH()/DAY()` pattern: **{totals['leap_year']}**")
    lines.append(f"- Files with no `*_Unsubscribes__dlm` LEFT JOIN: **{totals['missing_unsubscribe_join']}** "
                 "(marketing CIs only — transactional CIs are exempt)")
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
    args = parser.parse_args()

    if not args.output_dir and not (args.queries and args.output):
        parser.error("provide --output-dir, or both --queries and --output")

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

    results = {}
    for sql in sorted(q_dir.glob("*.sql")):
        results[sql.name] = audit_file(sql)

    if not results:
        print(f"No .sql files under {q_dir}")
        sys.exit(0)

    report = format_report(results)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"Wrote {out_path}  ({len(results)} file(s) scanned)")


if __name__ == "__main__":
    main()
