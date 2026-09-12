#!/usr/bin/env python3
"""
Data 360 CI Complexity / Refactor
Scores CI SQL maintainability (structural depth, branch complexity, size,
duplication) relative to the other CIs in the same snapshot, and suggests
refactors for the worst offenders.

Usage (matches ci_audit.py):
    python3 ci_complexity.py --output-dir <client-data360-folder>
    # reads from <output-dir>/queries/*.sql, writes
    # <output-dir>/reports/ci-complexity-report.md
"""

import argparse
import statistics
import sys
from pathlib import Path

from sqlglot import exp

from data360_analyst import ci_audit

DIALECT = ci_audit.DIALECT


def aggregate_ci_metrics(trees, raw_sql):
    """Combine per-statement structural metrics across every statement in one
    CI's SQL file into a single per-CI dict. `duplication_count` starts at 0 —
    callers fill it in via `collect_duplication_counts` once the whole
    snapshot's cross-CI filter index is available.
    """
    per_statement = [compute_structural_metrics(t) for t in trees if t is not None]

    def _max(key):
        return max((m[key] for m in per_statement), default=0)

    def _sum(key):
        return sum(m[key] for m in per_statement)

    distinct_fields = set()
    for tree in trees:
        if tree is not None:
            distinct_fields |= {c.sql(dialect=DIALECT) for c in tree.find_all(exp.Column)}

    return {
        "subquery_depth": _max("subquery_depth"),
        "case_nesting_depth": _max("case_nesting_depth"),
        "cte_chain_length": _max("cte_chain_length"),
        "boolean_condition_depth": _max("boolean_condition_depth"),
        "join_count": _sum("join_count"),
        "when_branch_count": _sum("when_branch_count"),
        "mixed_type_case_count": _sum("mixed_type_case_count"),
        "distinct_field_count": len(distinct_fields),
        "line_count": raw_sql.count("\n") + 1,
        "duplication_count": 0,
    }


def collect_duplication_counts(parsed):
    """`parsed` is `{filename: trees}` for every successfully-parsed CI in the
    snapshot (same shape `ci_audit.build_ci_filter_index` expects). Returns
    `{ci_name_lower: duplication_hit_count}`, combining within-CI repeated
    derived expressions and cross-CI redundant filters.
    """
    ci_filter_index = ci_audit.build_ci_filter_index(parsed)
    counts = {}
    for fname, trees in parsed.items():
        name = Path(fname).stem.lower()
        total = 0
        for tree in trees:
            if tree is None:
                continue
            total += len(ci_audit.check_repeated_derived_expressions(tree))
            total += len(ci_audit.check_cross_ci_redundant_filter(tree, ci_filter_index))
        counts[name] = total
    return counts


def compute_structural_metrics(tree):
    """Per-statement structural signals. No cross-statement or cross-CI
    aggregation — callers combine these across a CI's statements.
    """
    subquery_depth = _max_nesting_depth(tree, exp.Subquery)
    case_nesting_depth = _max_nesting_depth(tree, exp.Case)

    when_branch_count = 0
    mixed_type_case_count = 0
    for case in tree.find_all(exp.Case):
        when_branch_count += len(case.args.get("ifs") or [])
    mixed_type_case_count = len(ci_audit.check_case_mixed_types(tree))

    with_node = tree.args.get("with_")
    cte_chain_length = len(with_node.expressions) if with_node else 0

    join_count = len(list(tree.find_all(exp.Join)))

    boolean_condition_depth = 0
    where = tree.args.get("where")
    if where is not None:
        boolean_condition_depth = max(boolean_condition_depth, _connector_depth(where.this))
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None:
            boolean_condition_depth = max(boolean_condition_depth, _connector_depth(on))

    distinct_field_count = len({c.sql(dialect=DIALECT) for c in tree.find_all(exp.Column)})

    return {
        "subquery_depth": subquery_depth,
        "case_nesting_depth": case_nesting_depth,
        "cte_chain_length": cte_chain_length,
        "join_count": join_count,
        "when_branch_count": when_branch_count,
        "mixed_type_case_count": mixed_type_case_count,
        "boolean_condition_depth": boolean_condition_depth,
        "distinct_field_count": distinct_field_count,
    }


def _max_nesting_depth(tree, node_type):
    """Max depth of `node_type` nodes nested inside other `node_type` nodes."""
    best = 0
    for node in tree.find_all(node_type):
        depth = 1
        parent = node.parent
        while parent is not None:
            if isinstance(parent, node_type):
                depth += 1
            parent = parent.parent
        best = max(best, depth)
    return best


def _connector_depth(node):
    """Depth of AND/OR nesting in a boolean expression tree."""
    # Unwrap Paren nodes to reach connectors inside
    if isinstance(node, exp.Paren):
        return _connector_depth(node.this)
    if not isinstance(node, exp.Connector):
        return 0
    left = _connector_depth(node.this)
    right = _connector_depth(node.expression)
    return 1 + max(left, right)


_SIGNAL_WEIGHTS = {"depth": 0.40, "branch": 0.25, "duplication": 0.25, "size": 0.10}


def _raw_signals(m):
    return {
        "depth": m["subquery_depth"] + m["case_nesting_depth"] + m["cte_chain_length"] + m["join_count"],
        "branch": m["when_branch_count"] + 2 * m["mixed_type_case_count"] + m["boolean_condition_depth"],
        "duplication": m["duplication_count"],
        "size": m["distinct_field_count"] + m["line_count"] / 10.0,
    }


def _percentile_rank(value, all_values):
    n = len(all_values)
    if n <= 1 or min(all_values) == max(all_values):
        return 0.5
    rank = sum(1 for v in all_values if v <= value) - 1
    return rank / (n - 1)


def _bucket(score):
    if score < 25:
        return "Low"
    if score < 50:
        return "Medium"
    if score < 75:
        return "High"
    return "Severe"


def normalize_and_score(all_metrics):
    raw_by_ci = {name: _raw_signals(m) for name, m in all_metrics.items()}
    signals = list(_SIGNAL_WEIGHTS)
    values_by_signal = {s: [raw_by_ci[name][s] for name in raw_by_ci] for s in signals}

    results = {}
    for name, raw in raw_by_ci.items():
        breakdown = {
            s: _percentile_rank(raw[s], values_by_signal[s])
            for s in signals
        }
        weighted = {s: breakdown[s] * _SIGNAL_WEIGHTS[s] for s in signals}
        score = round(100 * sum(weighted.values()))
        drivers = sorted(signals, key=lambda s: weighted[s], reverse=True)
        results[name] = {
            "score": score,
            "bucket": _bucket(score),
            "breakdown": breakdown,
            "drivers": drivers,
        }
    return results


def suggest_refactor(ci_name, trees, score_result):
    """Prose + (where safe) rewritten-SQL suggestions for the signals that
    drove this CI's score, for CIs in the High/Severe buckets only. Rewrites
    never use a top-level CTE — the CI editor rejects Common Table Expressions; the safe
    shape is `FROM (SELECT ...) AS alias`.
    """
    if score_result["bucket"] not in ("High", "Severe"):
        return []

    breakdown = score_result.get("breakdown") or {}
    all_signals = ("depth", "branch", "duplication", "size")
    contributions = [breakdown.get(s, 0) for s in all_signals]
    median_contribution = statistics.median(contributions)

    suggestions = []
    for driver in score_result["drivers"]:
        if breakdown.get(driver, 0) < median_contribution:
            continue  # not a top contributor for this CI — skip to avoid suggestion noise
        if driver == "depth":
            suggestions.extend(_suggest_depth_refactor(trees))
        elif driver == "duplication":
            suggestions.extend(_suggest_duplication_refactor(trees))
        elif driver == "branch":
            suggestions.extend(_suggest_branch_refactor(trees))
        elif driver == "size":
            suggestions.extend(_suggest_size_refactor(trees))
    return suggestions


def _suggest_depth_refactor(trees):
    out = []
    deepest_case = None
    deepest_depth = 0
    for tree in trees:
        if tree is None:
            continue
        for case in tree.find_all(exp.Case):
            depth = 1
            parent = case.parent
            while parent is not None:
                if isinstance(parent, exp.Case):
                    depth += 1
                parent = parent.parent
            if depth > deepest_depth:
                deepest_depth, deepest_case = depth, case
    if deepest_case is not None and deepest_depth >= 2:
        out.append(
            "Nested CASE expressions are hard to read and modify safely. "
            "Flatten the inner CASE into the outer one's WHEN conditions "
            "(combine using AND), or promote the inner CASE into its own "
            f"column on the input CI:\n```sql\n{deepest_case.sql(dialect=DIALECT)}\n```"
        )
    return out


# Label repeated-expression families by their most common source spelling —
# sqlglot normalizes IFNULL/NVL/COALESCE all to the same Coalesce AST node
# and always renders it back out as "COALESCE(...)" regardless of dialect,
# so the original spelling in the CI's source SQL can't be recovered from
# the parsed tree. Label by family instead of assuming one canonical name.
_DUP_LABELS = (
    ("COALESCE(", "IFNULL/COALESCE/NVL"),
    ("CONCAT(", "CONCAT"),
    ("CAST(", "CAST"),
    ("CASE", "CASE"),
)


def _dup_label(expr):
    upper = expr.upper()
    for prefix, label in _DUP_LABELS:
        if upper.startswith(prefix):
            return label
    return "expression"


def _suggest_duplication_refactor(trees):
    out = []
    seen = set()
    for tree in trees:
        if tree is None:
            continue
        for expr, count in ci_audit.check_repeated_derived_expressions(tree):
            if expr in seen:
                continue
            seen.add(expr)
            out.append(
                f"This {_dup_label(expr)} expression — `{expr}` — is repeated "
                f"{count}+ times across this CI's SELECT/JOIN/GROUP BY/WHERE "
                "clauses. Promote it into a column on the input CI (preferred), "
                "or compute it once in a derived subquery — "
                f"`FROM (SELECT {expr} AS derived_value, ... ) AS src` — never a "
                "top-level Common Table Expression, which the CI editor rejects."
            )
    if not out:
        out.append(
            "This CI's duplication score is driven at least partly by a "
            "cross-CI redundant filter (the same WHERE/JOIN condition already "
            "enforced by an upstream CI), not by a repeated expression within "
            "this CI's own SQL. Check `data360 ci-audit`'s report for a "
            "\"cross-CI redundant filter\" finding on this CI — that check has "
            "full corpus context and can name the specific filter and the "
            "other CI it's shared with."
        )
    return out


def _suggest_branch_refactor(trees):
    out = []
    for tree in trees:
        if tree is None:
            continue
        for hit in ci_audit.check_case_mixed_types(tree):
            out.append(
                "This CASE mixes a NULL branch alongside typed literals, which "
                "the CI editor's validator rejects. Replace the NULL literal "
                f"using a typed default (`0`, `0.0`, or `''`):\n```sql\n{hit}\n```"
            )
    return out


def _suggest_size_refactor(trees):
    return [
        "This CI references a large number of distinct fields across a lot of "
        "SQL. Consider whether it's answering more than one question — "
        "splitting it into two focused CIs is usually easier to maintain than "
        "one large one, even though it doesn't reduce a mechanical metric here."
    ]


def build_report(all_metrics, scores, suggestions, excluded, low_n_warning):
    """Ranked (worst-first) markdown report over `scores`/`suggestions` from
    `normalize_and_score`/`suggest_refactor`, plus any parse-error exclusions.
    Scoring is corpus-relative (see methodology note below) — never read a
    bucket as an absolute quality bar.
    """
    lines = ["# CI Complexity / Refactor", ""]
    lines.append(
        "_Scores rank each CI against the other CIs in this snapshot only — "
        "a bucket reflects relative standing within this org, not a universal "
        "quality bar. A 3-CI org and a 200-CI org will "
        "calibrate \"Severe\" differently._"
    )
    lines.append("")

    if low_n_warning:
        lines.append(
            "> **Warning:** too few CIs scored successfully in this snapshot "
            "for percentile-based ranking to be meaningful. Treat scores "
            "below as indicative only."
        )
        lines.append("")

    ranked = sorted(scores.items(), key=lambda kv: kv[1]["score"], reverse=True)

    lines.append("## Ranked (worst first)")
    lines.append("")
    lines.append("| CI | Score | Bucket | Top driver |")
    lines.append("|---|---|---|---|")
    for name, result in ranked:
        driver = result["drivers"][0] if result["drivers"] else "—"
        lines.append(f"| {name} | {result['score']} | {result['bucket']} | {driver} |")
    lines.append("")

    if excluded:
        lines.append("## Excluded — parse error")
        lines.append("")
        for name, err in sorted(excluded.items()):
            lines.append(f"- **{name}**: parse error — {err}")
        lines.append("")

    lines.append("## Detail")
    lines.append("")
    for name, result in ranked:
        lines.append(f"### {name} — {result['score']} ({result['bucket']})")
        lines.append("")
        for suggestion in suggestions.get(name, []):
            lines.append(suggestion)
            lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Score CI SQL maintainability and suggest refactors",
        epilog=(
            "Pass --output-dir <root> to use the toolkit's standard layout "
            "(reads <root>/queries/*.sql, writes "
            "<root>/reports/ci-complexity-report.md). Or pass --queries and "
            "--output for explicit paths."
        ),
    )
    parser.add_argument("--output-dir", help="Client Data360 root — derives queries/ and reports/ paths")
    parser.add_argument("--queries", help="Directory of .sql files (overrides --output-dir)")
    parser.add_argument("--output", help="Path to write the markdown report (overrides --output-dir)")
    args = parser.parse_args()

    if not args.output_dir and not (args.queries and args.output):
        parser.error("provide --output-dir, or both --queries and --output")

    q_dir = Path(args.queries).expanduser() if args.queries else Path(args.output_dir).expanduser() / "queries"
    out_path = (
        Path(args.output).expanduser()
        if args.output
        else Path(args.output_dir).expanduser() / "reports" / "ci-complexity-report.md"
    )

    if not q_dir.is_dir():
        print(f"ERROR: queries dir not found: {q_dir}")
        sys.exit(1)

    parsed = {}
    raw_sql = {}
    excluded = {}
    for sql_path in sorted(q_dir.glob("*.sql")):
        trees, raw, err = ci_audit.parse_file(sql_path)
        if err is not None:
            excluded[sql_path.stem.lower()] = err
        else:
            parsed[sql_path.name] = trees
            raw_sql[sql_path.name] = raw

    if not parsed:
        print(f"No parseable .sql files under {q_dir}")
        sys.exit(0)

    duplication_counts = collect_duplication_counts(parsed)

    all_metrics = {}
    for fname, trees in parsed.items():
        name = Path(fname).stem.lower()
        metrics = aggregate_ci_metrics(trees, raw_sql[fname])
        metrics["duplication_count"] = duplication_counts.get(name, 0)
        all_metrics[name] = metrics

    scores = normalize_and_score(all_metrics)

    trees_by_name = {Path(fname).stem.lower(): trees for fname, trees in parsed.items()}
    suggestions = {
        name: suggest_refactor(name, trees_by_name[name], scores[name])
        for name in scores
    }

    report = build_report(
        all_metrics, scores, suggestions, excluded, low_n_warning=len(parsed) < 3
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"Wrote {out_path}  ({len(parsed)} file(s) scored, {len(excluded)} excluded)")


if __name__ == "__main__":
    main()
