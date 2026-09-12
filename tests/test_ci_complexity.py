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


def test_aggregate_ci_metrics_takes_max_depth_across_statements():
    sql = "SELECT * FROM (SELECT * FROM (SELECT 1) a) b; SELECT * FROM (SELECT 1) c"
    trees = sqlglot.parse(sql, read=DIALECT)
    m = ci_complexity.aggregate_ci_metrics(trees, sql)
    assert m["subquery_depth"] == 2


def test_aggregate_ci_metrics_sums_join_count_across_statements():
    sql = "SELECT * FROM a JOIN b ON a.id=b.id; SELECT * FROM c JOIN d ON c.id=d.id"
    trees = sqlglot.parse(sql, read=DIALECT)
    m = ci_complexity.aggregate_ci_metrics(trees, sql)
    assert m["join_count"] == 2


def test_aggregate_ci_metrics_unions_distinct_fields_across_statements():
    trees = sqlglot.parse("SELECT a FROM t; SELECT b FROM t", read=DIALECT)
    m = ci_complexity.aggregate_ci_metrics(trees, "SELECT a FROM t; SELECT b FROM t")
    assert m["distinct_field_count"] == 2


def test_aggregate_ci_metrics_line_count_from_raw_sql():
    raw = "SELECT a\nFROM t\nWHERE a = 1"
    trees = sqlglot.parse(raw, read=DIALECT)
    m = ci_complexity.aggregate_ci_metrics(trees, raw)
    assert m["line_count"] == 3


def test_aggregate_ci_metrics_duplication_count_defaults_zero():
    trees = sqlglot.parse("SELECT a FROM t", read=DIALECT)
    m = ci_complexity.aggregate_ci_metrics(trees, "SELECT a FROM t")
    assert m["duplication_count"] == 0


def test_collect_duplication_counts_within_ci():
    # Same non-trivial IFNULL expression repeated 3x — check_repeated_derived_expressions
    # threshold is 3.
    sql = (
        "SELECT IFNULL(a.phone_number__c, 'unknown'), "
        "IFNULL(a.phone_number__c, 'unknown'), "
        "IFNULL(a.phone_number__c, 'unknown') FROM a"
    )
    parsed = {"Some_CI__cio.sql": sqlglot.parse(sql, read=DIALECT)}
    counts = ci_complexity.collect_duplication_counts(parsed)
    assert counts["some_ci__cio"] >= 1


def test_collect_duplication_counts_cross_ci():
    # Upstream CI filters on a.status__c = 'Active' in its WHERE clause.
    # Downstream CI inner-joins it AND duplicates the same filter on the base table 'a'
    # via JOIN ON predicate — this is a redundant filter since the upstream CI
    # already enforces it.
    upstream_sql = "SELECT id FROM a WHERE a.status__c = 'Active'"
    downstream_sql = (
        "SELECT b.id FROM b "
        "INNER JOIN Upstream_CI__cio ON b.upstream_id = Upstream_CI__cio.id "
        "INNER JOIN a ON a.id = b.a_id AND a.status__c = 'Active'"
    )
    parsed = {
        "Upstream_CI__cio.sql": sqlglot.parse(upstream_sql, read=DIALECT),
        "Downstream_CI__cio.sql": sqlglot.parse(downstream_sql, read=DIALECT),
    }
    counts = ci_complexity.collect_duplication_counts(parsed)
    assert counts["downstream_ci__cio"] >= 1


def _metrics(**overrides):
    base = {
        "subquery_depth": 0, "case_nesting_depth": 0, "cte_chain_length": 0,
        "join_count": 0, "when_branch_count": 0, "mixed_type_case_count": 0,
        "boolean_condition_depth": 0, "distinct_field_count": 0,
        "line_count": 1, "duplication_count": 0,
    }
    base.update(overrides)
    return base


def test_normalize_and_score_ranks_worst_ci_highest():
    all_metrics = {
        "simple__cio": _metrics(),
        "complex__cio": _metrics(subquery_depth=3, case_nesting_depth=3, join_count=5,
                                  when_branch_count=10, duplication_count=5),
        "medium__cio": _metrics(join_count=2, when_branch_count=3),
    }
    scores = ci_complexity.normalize_and_score(all_metrics)
    assert scores["complex__cio"]["score"] > scores["medium__cio"]["score"]
    assert scores["medium__cio"]["score"] > scores["simple__cio"]["score"]


def test_normalize_and_score_buckets_are_valid():
    all_metrics = {"a__cio": _metrics(), "b__cio": _metrics(join_count=5), "c__cio": _metrics(join_count=10)}
    scores = ci_complexity.normalize_and_score(all_metrics)
    for result in scores.values():
        assert result["bucket"] in ("Low", "Medium", "High", "Severe")
        assert 0 <= result["score"] <= 100


def test_normalize_and_score_drivers_reflect_dominant_signal():
    all_metrics = {
        "flat__cio": _metrics(),
        "deep__cio": _metrics(subquery_depth=5, case_nesting_depth=5),
    }
    scores = ci_complexity.normalize_and_score(all_metrics)
    assert scores["deep__cio"]["drivers"][0] == "depth"


def test_normalize_and_score_single_ci_does_not_crash():
    scores = ci_complexity.normalize_and_score({"only__cio": _metrics(join_count=3)})
    assert scores["only__cio"]["score"] >= 0
