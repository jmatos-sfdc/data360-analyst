"""Tests for ci_audit.py — self-verifying confirm queries (Phase B item 2).

Each data-dependent finding should emit a runnable Query-Editor SQL the user
can paste into their own org to confirm the trap is real, not just a pattern
match. These tests parse representative CI SQL and assert the confirm query is
generated, targets the right table/column, and renders into the report.
"""

import sqlglot

from data360_analyst import ci_audit


def _trees(sql):
    return sqlglot.parse(sql, read=ci_audit.DIALECT)


def _confirm(sql):
    out = []
    for tree in _trees(sql):
        out += ci_audit.build_confirm_queries(tree)
    return out


def test_leap_year_confirm_targets_source_column():
    sql = (
        "SELECT ssot__Individual__dlm.ssot__Id__c "
        "FROM ssot__Individual__dlm "
        "WHERE MONTH(ssot__Individual__dlm.ssot__BirthDate__c) = MONTH(CURRENT_DATE()) "
        "AND DAY(ssot__Individual__dlm.ssot__BirthDate__c) = DAY(CURRENT_DATE())"
    )
    queries = [q for q in _confirm(sql) if q["finding"] == "leap_year"]
    assert queries, "expected a leap-year confirm query"
    q = queries[0]
    assert "MONTH(ssot__BirthDate__c) = 2" in q["sql"]
    assert "DAY(ssot__BirthDate__c) = 29" in q["sql"]
    assert "FROM ssot__Individual__dlm" in q["sql"]


def test_single_day_trigger_confirm_lists_recent_days():
    sql = (
        "SELECT ssot__Individual__dlm.ssot__Id__c "
        "FROM ssot__Individual__dlm "
        "WHERE ssot__Individual__dlm.event_date__c = DATE_ADD(CURRENT_DATE(), -1)"
    )
    queries = [q for q in _confirm(sql) if q["finding"] == "single_day_trigger"]
    assert queries
    q = queries[0]
    assert "GROUP BY event_date__c" in q["sql"]
    assert "DATE_ADD(CURRENT_DATE(), -7)" in q["sql"]
    assert "FROM ssot__Individual__dlm" in q["sql"]


def test_record_type_confirm_counts_matching_rows():
    sql = (
        "SELECT ssot__Account__dlm.ssot__Id__c "
        "FROM ssot__Account__dlm "
        "WHERE ssot__Account__dlm.RecordTypeId__c = '012000000000ABCDEF'"
    )
    queries = [q for q in _confirm(sql) if q["finding"] == "hardcoded_record_type_ids"]
    assert queries
    q = queries[0]
    assert "WHERE RecordTypeId__c = '012000000000ABCDEF'" in q["sql"]
    assert "COUNT(*) AS rows_matching" in q["sql"]


def test_syntactic_finding_gets_no_confirm_query():
    # Top-level DISTINCT is certain from the AST — no org data needed to confirm.
    sql = "SELECT DISTINCT ssot__Account__dlm.ssot__Id__c FROM ssot__Account__dlm"
    assert _confirm(sql) == []


def test_confirm_queries_render_in_report():
    sql = (
        "SELECT ssot__Individual__dlm.ssot__Id__c "
        "FROM ssot__Individual__dlm "
        "WHERE MONTH(ssot__Individual__dlm.ssot__BirthDate__c) = MONTH(CURRENT_DATE()) "
        "AND DAY(ssot__Individual__dlm.ssot__BirthDate__c) = DAY(CURRENT_DATE())"
    )
    trees = _trees(sql)
    findings = ci_audit.audit_file(trees, sql)
    report = ci_audit.format_report({"Birthday__cio.sql": findings})
    assert "Confirm against your org" in report
    assert "```sql" in report
    assert "feb29_rows" in report


def test_confirm_query_deduped_when_trap_repeats():
    # Same hardcoded ID compared twice → one confirm query in the report.
    sql = (
        "SELECT a.ssot__Id__c FROM ssot__Account__dlm AS a "
        "WHERE a.RecordTypeId__c = '012000000000ABCDEF' "
        "AND a.RecordTypeId__c = '012000000000ABCDEF'"
    )
    trees = _trees(sql)
    findings = ci_audit.audit_file(trees, sql)
    report = ci_audit.format_report({"Dup__cio.sql": findings})
    # Alias `a` resolves back to the real DMO in the confirm query.
    assert "FROM ssot__Account__dlm" in report
    assert report.count("COUNT(*) AS rows_matching") == 1


# ── Pass 3 compliance checks ─────────────────────────────────────────────────


def _audit(sql, ci_name=None):
    return ci_audit.audit_file(_trees(sql), sql, ci_name=ci_name)


def test_simple_case_flagged_searched_case_not():
    simple = "SELECT CASE a WHEN 1 THEN 'x' ELSE 'y' END AS b FROM t GROUP BY b"
    searched = "SELECT CASE WHEN a = 1 THEN 'x' ELSE 'y' END AS b FROM t GROUP BY b"
    assert _audit(simple)["simple_case"]
    assert not _audit(searched)["simple_case"]


def test_not_compound_flagged_simple_not_ignored():
    assert _audit("SELECT a FROM t WHERE NOT (a = 1 AND b = 2)")["not_compound"]
    assert not _audit("SELECT a FROM t WHERE NOT (a = 1)")["not_compound"]


def test_first_case_flagged_sum_case_not():
    assert _audit("SELECT FIRST(CASE WHEN a = 1 THEN 1 ELSE 0 END) AS x FROM t GROUP BY y")["first_case"]
    assert not _audit("SELECT SUM(CASE WHEN a = 1 THEN 1 ELSE 0 END) AS x FROM t GROUP BY y")["first_case"]


def test_select_subquery_flagged_in_subquery_not():
    assert _audit("SELECT (SELECT MAX(x) FROM u) AS m, COUNT(a) AS c FROM t GROUP BY m")["select_subquery"]
    ok = "SELECT COUNT(a) AS c FROM t WHERE a IN (SELECT x AS x2 FROM u) GROUP BY b"
    assert not _audit(ok)["select_subquery"]


def test_non_ascii_flagged_with_line():
    sql = "SELECT COUNT(a) AS c\nFROM t -- café\nGROUP BY b"
    assert _audit(sql)["non_ascii"] == ["line 2: U+00E9"]
    assert not _audit("SELECT COUNT(a) AS c FROM t GROUP BY b")["non_ascii"]


def test_ci_name_length_excludes_cio_suffix():
    ok = "a" * 36
    long = "a" * 37
    sql = "SELECT COUNT(a) AS c FROM t GROUP BY b"
    assert not _audit(sql, ci_name=ok + "__cio")["ci_name_length"]
    assert _audit(sql, ci_name=long + "__cio")["ci_name_length"] == [(long, 37)]


def test_pass3_findings_render_in_report():
    sql = "SELECT CASE a WHEN 1 THEN 'x' ELSE 'y' END AS b FROM t GROUP BY b"
    report = ci_audit.format_report({"X__cio.sql": _audit(sql)})
    assert "Simple `CASE <operand> WHEN`" in report
