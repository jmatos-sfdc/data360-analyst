"""Tests for ci_complexity.py — structural metrics, scoring, and refactor
suggestions for CI SQL maintainability."""

import sqlglot

from data360_analyst import ci_complexity

DIALECT = "spark"


def _tree(sql):
    return sqlglot.parse_one(sql, read=DIALECT)


def test_subquery_depth_counts_nesting():
    sql = "SELECT * FROM (SELECT * FROM (SELECT id FROM t) a) b"
    m = ci_complexity.compute_structural_metrics(_tree(sql))
    assert m["subquery_depth"] == 2


def test_subquery_depth_zero_when_flat():
    m = ci_complexity.compute_structural_metrics(_tree("SELECT id FROM t"))
    assert m["subquery_depth"] == 0


def test_case_nesting_depth_counts_case_in_case():
    sql = (
        "SELECT CASE WHEN x = 1 THEN "
        "(CASE WHEN y = 2 THEN 'a' ELSE 'b' END) ELSE 'c' END AS f FROM t"
    )
    m = ci_complexity.compute_structural_metrics(_tree(sql))
    assert m["case_nesting_depth"] == 2


def test_when_branch_count_sums_across_cases():
    sql = (
        "SELECT "
        "CASE WHEN a=1 THEN 1 WHEN a=2 THEN 2 ELSE 0 END, "
        "CASE WHEN b=1 THEN 1 ELSE 0 END "
        "FROM t"
    )
    m = ci_complexity.compute_structural_metrics(_tree(sql))
    assert m["when_branch_count"] == 3


def test_mixed_type_case_count_flags_null_vs_number():
    sql = "SELECT CASE WHEN x=1 THEN 1 ELSE NULL END AS f FROM t"
    m = ci_complexity.compute_structural_metrics(_tree(sql))
    assert m["mixed_type_case_count"] == 1


def test_mixed_type_case_count_zero_when_consistent():
    sql = "SELECT CASE WHEN x=1 THEN 1 ELSE 0 END AS f FROM t"
    m = ci_complexity.compute_structural_metrics(_tree(sql))
    assert m["mixed_type_case_count"] == 0


def test_cte_chain_length_counts_ctes():
    sql = "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a"
    m = ci_complexity.compute_structural_metrics(_tree(sql))
    assert m["cte_chain_length"] == 2


def test_join_count():
    sql = "SELECT * FROM a JOIN b ON a.id=b.id JOIN c ON b.id=c.id"
    m = ci_complexity.compute_structural_metrics(_tree(sql))
    assert m["join_count"] == 2


def test_boolean_condition_depth_counts_and_or_nesting():
    sql = "SELECT * FROM t WHERE (x=1 AND y=2) OR (z=3 AND w=4 AND v=5)"
    m = ci_complexity.compute_structural_metrics(_tree(sql))
    assert m["boolean_condition_depth"] >= 2


def test_boolean_condition_depth_zero_when_no_where():
    m = ci_complexity.compute_structural_metrics(_tree("SELECT id FROM t"))
    assert m["boolean_condition_depth"] == 0


def test_distinct_field_count_counts_unique_columns():
    sql = "SELECT a, b, a FROM t WHERE a = 1"
    m = ci_complexity.compute_structural_metrics(_tree(sql))
    assert m["distinct_field_count"] == 2
