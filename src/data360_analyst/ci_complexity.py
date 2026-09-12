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
