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


def test_normalize_and_score_tied_signal_does_not_all_max_out():
    # duplication_count is 0 for every CI (tied) even though other signals vary.
    # A tied signal has no differentiation and must not degenerate to percentile 1.0
    # for every entry.
    all_metrics = {
        "simple__cio": _metrics(),
        "medium__cio": _metrics(join_count=2),
        "complex__cio": _metrics(join_count=5, subquery_depth=3),
    }
    scores = ci_complexity.normalize_and_score(all_metrics)
    for result in scores.values():
        assert result["breakdown"]["duplication"] == 0.5


def test_suggest_refactor_empty_for_low_bucket():
    result = {"score": 10, "bucket": "Low", "breakdown": {}, "drivers": ["size"]}
    trees = sqlglot.parse("SELECT a FROM t", read=DIALECT)
    assert ci_complexity.suggest_refactor("simple__cio", trees, result) == []


def test_suggest_refactor_depth_driver_mentions_nested_case():
    sql = (
        "SELECT CASE WHEN x=1 THEN (CASE WHEN y=2 THEN 'a' ELSE 'b' END) "
        "ELSE 'c' END AS f FROM t"
    )
    trees = sqlglot.parse(sql, read=DIALECT)
    result = {"score": 80, "bucket": "Severe", "breakdown": {}, "drivers": ["depth"]}
    suggestions = ci_complexity.suggest_refactor("nested__cio", trees, result)
    assert suggestions
    assert any("CASE" in s for s in suggestions)


def test_suggest_refactor_duplication_driver_names_repeated_expression():
    sql = (
        "SELECT IFNULL(a.phone_number__c, 'unknown'), "
        "IFNULL(a.phone_number__c, 'unknown'), "
        "IFNULL(a.phone_number__c, 'unknown') FROM a"
    )
    trees = sqlglot.parse(sql, read=DIALECT)
    result = {"score": 80, "bucket": "Severe", "breakdown": {}, "drivers": ["duplication"]}
    suggestions = ci_complexity.suggest_refactor("dup__cio", trees, result)
    assert suggestions
    assert any("IFNULL" in s for s in suggestions)


def test_suggest_refactor_duplication_driver_falls_back_when_no_local_hits():
    # duplication_count > 0 in score_result, but this CI's own trees have no
    # check_repeated_derived_expressions hits (the duplication signal here is
    # driven entirely by a cross-CI redundant filter, which suggest_refactor
    # has no corpus-wide index to name). Must still emit a non-empty,
    # duplication-related suggestion instead of going silent.
    sql = "SELECT b.id FROM b INNER JOIN a ON a.id = b.a_id AND a.status__c = 'Active'"
    trees = sqlglot.parse(sql, read=DIALECT)
    result = {"score": 80, "bucket": "Severe", "breakdown": {}, "drivers": ["duplication"]}
    suggestions = ci_complexity.suggest_refactor("cross_dup__cio", trees, result)
    assert suggestions
    assert any("duplication" in s.lower() for s in suggestions)
    assert any("ci-audit" in s.lower() for s in suggestions)


def test_suggest_refactor_never_emits_top_level_cte():
    sql = (
        "SELECT CASE WHEN x=1 THEN (CASE WHEN y=2 THEN 'a' ELSE 'b' END) "
        "ELSE 'c' END AS f FROM t"
    )
    trees = sqlglot.parse(sql, read=DIALECT)
    result = {"score": 90, "bucket": "Severe", "breakdown": {}, "drivers": ["depth", "duplication"]}
    suggestions = ci_complexity.suggest_refactor("nested__cio", trees, result)
    for s in suggestions:
        assert "WITH " not in s.upper().replace("WITHIN", "")


def test_suggest_refactor_branch_driver_no_leaked_with():
    sql = "SELECT CASE WHEN x = 1 THEN 1.5 ELSE NULL END AS f FROM t"
    trees = sqlglot.parse(sql, read=DIALECT)
    result = {"score": 80, "bucket": "Severe", "breakdown": {}, "drivers": ["branch"]}
    suggestions = ci_complexity.suggest_refactor("mixedtype__cio", trees, result)
    assert suggestions
    for s in suggestions:
        assert "WITH " not in s.upper().replace("WITHIN", "")


def test_suggest_refactor_skips_below_median_signal():
    # "size" contributes far below the other three (0.05 vs 0.9) — median of
    # the 4 breakdown values is 0.9, so "size" is below-median and must not
    # get a suggestion, while the top contributors still do.
    sql = (
        "SELECT CASE WHEN x=1 THEN (CASE WHEN y=2 THEN 'a' ELSE 'b' END) "
        "ELSE 'c' END AS f FROM t"
    )
    trees = sqlglot.parse(sql, read=DIALECT)
    result = {
        "score": 90,
        "bucket": "Severe",
        "breakdown": {"depth": 0.9, "branch": 0.9, "duplication": 0.9, "size": 0.05},
        "drivers": ["depth", "branch", "duplication", "size"],
    }
    suggestions = ci_complexity.suggest_refactor("mixed__cio", trees, result)
    assert not any("more than one question" in s for s in suggestions)


def test_build_report_ranks_worst_first():
    scores = {
        "good__cio": {"score": 10, "bucket": "Low", "breakdown": {}, "drivers": []},
        "bad__cio": {"score": 90, "bucket": "Severe", "breakdown": {}, "drivers": ["depth"]},
    }
    metrics = {"good__cio": _metrics(), "bad__cio": _metrics(subquery_depth=5)}
    report = ci_complexity.build_report(metrics, scores, {}, {}, low_n_warning=False)
    assert report.index("bad__cio") < report.index("good__cio")


def test_build_report_lists_excluded_parse_errors():
    scores = {"ok__cio": {"score": 5, "bucket": "Low", "breakdown": {}, "drivers": []}}
    metrics = {"ok__cio": _metrics()}
    excluded = {"broken__cio": "syntax error near SELEC"}
    report = ci_complexity.build_report(metrics, scores, {}, excluded, low_n_warning=False)
    assert "broken__cio" in report
    assert "parse error" in report.lower()


def test_build_report_includes_low_n_warning():
    scores = {"only__cio": {"score": 5, "bucket": "Low", "breakdown": {}, "drivers": []}}
    metrics = {"only__cio": _metrics()}
    report = ci_complexity.build_report(metrics, scores, {}, {}, low_n_warning=True)
    assert "too few" in report.lower()


def test_build_report_includes_suggestions_for_flagged_ci():
    scores = {"bad__cio": {"score": 90, "bucket": "Severe", "breakdown": {}, "drivers": ["depth"]}}
    metrics = {"bad__cio": _metrics(subquery_depth=5)}
    suggestions = {"bad__cio": ["Nested CASE expressions are hard to read..."]}
    report = ci_complexity.build_report(metrics, scores, suggestions, {}, low_n_warning=False)
    assert "Nested CASE expressions" in report


from pathlib import Path
from data360_analyst import ci_audit

DEMO_ORG_QUERIES = Path(__file__).parent.parent / "examples" / "demo-org" / "queries"


def _run_pipeline(query_dir):
    parsed, raw_sql, excluded = {}, {}, {}
    for sql_path in sorted(query_dir.glob("*.sql")):
        trees, raw, err = ci_audit.parse_file(sql_path)
        if err is not None:
            excluded[sql_path.stem.lower()] = err
        else:
            parsed[sql_path.name] = trees
            raw_sql[sql_path.name] = raw

    duplication_counts = ci_complexity.collect_duplication_counts(parsed)
    all_metrics = {}
    for fname, trees in parsed.items():
        name = Path(fname).stem.lower()
        m = ci_complexity.aggregate_ci_metrics(trees, raw_sql[fname])
        m["duplication_count"] = duplication_counts.get(name, 0)
        all_metrics[name] = m

    scores = ci_complexity.normalize_and_score(all_metrics)
    trees_by_name = {Path(f).stem.lower(): t for f, t in parsed.items()}
    suggestions = {n: ci_complexity.suggest_refactor(n, trees_by_name[n], scores[n]) for n in scores}
    report = ci_complexity.build_report(
        all_metrics, scores, suggestions, excluded, low_n_warning=len(parsed) < 3
    )
    return all_metrics, scores, suggestions, excluded, report


def test_full_pipeline_on_demo_org_scores_every_ci():
    all_metrics, scores, _, excluded, report = _run_pipeline(DEMO_ORG_QUERIES)
    assert not excluded
    assert len(scores) == len(list(DEMO_ORG_QUERIES.glob("*.sql")))
    for result in scores.values():
        assert result["bucket"] in ("Low", "Medium", "High", "Severe")
    assert "# CI Complexity" in report
    assert "too few" not in report.lower()  # 8 CIs in demo-org, well above the low-N floor


def test_full_pipeline_tolerates_unparseable_file(tmp_path):
    good_dir = tmp_path / "queries"
    good_dir.mkdir()
    for sql_path in list(DEMO_ORG_QUERIES.glob("*.sql"))[:3]:
        (good_dir / sql_path.name).write_text(sql_path.read_text())
    (good_dir / "Broken__cio.sql").write_text("SELEC this is not valid sql (((")

    _, scores, _, excluded, report = _run_pipeline(good_dir)
    assert "broken__cio" in excluded
    assert "broken__cio" not in scores
    assert "parse error" in report.lower()


def test_full_pipeline_warns_on_low_n(tmp_path):
    small_dir = tmp_path / "queries"
    small_dir.mkdir()
    for sql_path in list(DEMO_ORG_QUERIES.glob("*.sql"))[:2]:
        (small_dir / sql_path.name).write_text(sql_path.read_text())

    _, _, _, _, report = _run_pipeline(small_dir)
    assert "too few" in report.lower()
