"""Tests for sql_guard: only aggregate-only queries return rows to the conversation."""

import pytest

from data360_analyst import mcp_server, sql_guard


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM ssot__Individual__dlm",
    "SELECT COUNT(*) AS n, SUM(ssot__GrandTotalAmount__c) AS total FROM ssot__SalesOrder__dlm",
    "SELECT AVG(a.amount__c), MIN(a.ssot__CreatedDate__c), MAX(a.order_amount__c), 1 AS one FROM t a",
    "SELECT APPROX_COUNT_DISTINCT(ssot__Email__c) FROM ssot__ContactPointEmail__dlm",
    "SELECT COUNT(*) FROM t WHERE ssot__Email__c = 'x@y.com'",
    "SELECT COUNT(*) FROM (SELECT ssot__Id__c, ssot__Email__c FROM t) s",
    "SELECT COUNT(*) FROM a UNION ALL SELECT COUNT(*) FROM b",
    "SELECT COUNT(*) FROM t GROUP BY ssot__AccountId__c",
])
def test_aggregate_only_is_allowed(sql):
    assert sql_guard.check(sql) is None


@pytest.mark.parametrize("sql, fragment", [
    ("SELECT * FROM ssot__Individual__dlm", "SELECT *"),
    ("SELECT t.* FROM t", "SELECT *"),
    ("SELECT ssot__Email__c FROM t", "field value"),
    ("SELECT ssot__AccountId__c, COUNT(*) FROM t GROUP BY ssot__AccountId__c", "field value"),
    ("SELECT UPPER(ssot__FirstName__c) AS n FROM t", "field value"),
    ("SELECT COUNT(*) OVER (PARTITION BY a) FROM t", "window"),
    ("SELECT COUNT(*) FROM a UNION ALL SELECT ssot__Email__c FROM b", "field value"),
    ("SELECT CONCAT(COUNT(*), ssot__Email__c) FROM t", "field value"),
    ("SELECT MAX(ssot__Email__c) FROM t", "field value"),
    ("SELECT MIN(UPPER(ssot__LastName__c)) FROM t", "field value"),
    ("SELECT COLLECT_LIST(ssot__Email__c) FROM t", "field value"),
    ("SELECT FIRST(ssot__Email__c) FROM t", "field value"),
    ("SELECT (SELECT ssot__Email__c FROM t LIMIT 1) AS e", "field value"),
])
def test_row_level_is_refused(sql, fragment):
    reason = sql_guard.check(sql)
    assert reason and fragment in reason


@pytest.mark.parametrize("sql", ["SELEC nonsense FROM", "", "DELETE FROM t", "SHOW TABLES"])
def test_unparseable_or_non_select_fails_closed(sql):
    assert sql_guard.check(sql) is not None


def test_refusal_carries_query_editor_sql():
    sql = "  SELECT ssot__Email__c FROM t  "
    out = sql_guard.refusal(sql, "why")
    assert out["_blocked"] == "customer_data"
    assert out["query_editor_sql"] == "SELECT ssot__Email__c FROM t"
    assert "Query Editor" in out["message"]


# ── wired into the MCP tools ─────────────────────────────────────────────────

def _fn(tool):
    return getattr(tool, "fn", tool)


def test_run_sql_refuses_without_calling_the_org(monkeypatch):
    monkeypatch.setattr(mcp_server, "_post", lambda *a, **k: pytest.fail("org was called"))
    monkeypatch.setattr(mcp_server, "ORG_ALIAS", "demo")
    out = _fn(mcp_server.run_sql)("SELECT ssot__Email__c FROM t")
    assert out["_blocked"] == "customer_data"
    assert "export-sql-csv --org demo" in out["csv_export"]
    assert out["query_editor_sql"] == "SELECT ssot__Email__c FROM t"


def test_run_sql_allows_aggregate_and_pages_only_its_own_ids(monkeypatch):
    monkeypatch.setattr(mcp_server, "_ALLOWED_QUERY_IDS", set())
    monkeypatch.setattr(mcp_server, "_post", lambda path, body: {"data": [[3]], "status": {"queryId": "q1"}})
    monkeypatch.setattr(mcp_server, "_get", lambda path, params=None: {"data": [], "path": path})
    assert _fn(mcp_server.run_sql)("SELECT COUNT(*) FROM t")["data"] == [[3]]
    assert "q1" in _fn(mcp_server.get_sql_rows)("q1", 1)["path"]
    assert _fn(mcp_server.get_sql_rows)("q-other", 0)["_blocked"] == "unknown_query"


def test_get_ci_rows_returns_query_editor_sql(monkeypatch):
    monkeypatch.setattr(mcp_server, "_get", lambda *a, **k: pytest.fail("org was called"))
    out = _fn(mcp_server.get_ci_rows)("Customer_Health_Score", 50)
    assert out["_blocked"] == "customer_data"
    assert out["query_editor_sql"] == "SELECT * FROM Customer_Health_Score__cio LIMIT 50"
