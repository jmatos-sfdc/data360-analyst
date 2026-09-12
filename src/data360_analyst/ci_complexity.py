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
import sys
from pathlib import Path

import sqlglot
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
    if n <= 1:
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
