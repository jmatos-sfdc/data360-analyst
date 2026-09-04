"""Tests for ci_visualize.py Batch A integrity and safety behavior."""

import json
import subprocess
from pathlib import Path

from data360_analyst import ci_visualize


ROOT = Path(__file__).parent.parent
PYTHON = ROOT / ".venv" / "bin" / "python"


def test_detects_first_aggregations_direct_and_nested():
    model = ci_visualize.build_model(
        "SELECT FIRST(a) AS direct__c, FIRST(HOUR_ADD(b, 0)) AS nested__c FROM x",
        "Test__cio",
    )

    fields = {field["output"]: field for field in model["fields"]}
    assert fields["direct__c"]["aggregation"] == "FIRST"
    assert fields["nested__c"]["aggregation"] == "FIRST"


def test_fuzzy_span_uses_raw_match_end():
    resolver = ci_visualize.SpanResolver("foo\n    bar")
    assert resolver.find("foo bar") == [0, 11]


def test_whitespace_only_span_returns_none():
    assert ci_visualize.SpanResolver("SELECT 1").find("   ") is None


def test_pass_through_external_id_is_marked_unresolved():
    sql = """
    SELECT q.RecordAlertExternalId__c AS RecordAlertExternalId__c
    FROM (
      SELECT CONCAT(CONCAT(x.a, '~'), x.b) AS RecordAlertExternalId__c
      FROM x
    ) q
    GROUP BY q.RecordAlertExternalId__c
    """
    grain = ci_visualize.build_model(sql, "Test__cio")["grain"]["externalIdGrain"]

    assert grain["status"] == "unresolved-pass-through"
    assert grain["expression"] == "q.RecordAlertExternalId__c"
    assert grain["tokens"] == []


def test_direct_external_id_tokens_are_extracted():
    sql = """
    SELECT CONCAT(CONCAT(x.a, '~'), x.b) AS RecordAlertExternalId__c
    FROM x
    GROUP BY CONCAT(CONCAT(x.a, '~'), x.b)
    """
    grain = ci_visualize.build_model(sql, "Test__cio")["grain"]["externalIdGrain"]

    assert grain["status"] == "resolved"
    assert grain["tokens"] == ["x.a", "x.b"]


def test_inner_scope_projections_are_extracted_and_scoped():
    sql = """
    SELECT q.Value__c AS Value__c
    FROM (
      SELECT base.raw__c AS Value__c
      FROM base
    ) q
    """
    model = ci_visualize.build_model(sql, "Test__cio")
    inner = model["innerFields"]
    # the q-scope projection is surfaced (root SELECT is handled by fields, not innerFields)
    q_fields = [f for f in inner if f["boundAlias"] == "q"]
    assert any(f["output"] == "Value__c" for f in q_fields)
    # every inner field carries an owning scopeId that is not the root scope
    scopes = {s["scopeId"]: s for s in model["scopes"]}
    for f in inner:
        assert f["scopeId"] in scopes
        assert scopes[f["scopeId"]]["boundAlias"] != "__root__"


def test_inner_field_spans_anchor_within_their_own_scope_region():
    # Value__c appears as an output name in two scopes; each inner-field span must land on the
    # occurrence in its own scope, not collapse onto the first one in the file.
    sql = """
    SELECT q.Value__c AS Value__c
    FROM (
      SELECT inner_sq.Value__c AS Value__c
      FROM (
        SELECT base.raw__c AS Value__c FROM base
      ) inner_sq
    ) q
    """
    model = ci_visualize.build_model(sql, "Test__cio")
    spans = sorted(f["sourceSpan"][0] for f in model["innerFields"])
    # distinct spans, none overlapping — proves per-scope anchoring, not a single shared match
    assert len(spans) == len(set(spans))
    ordered = [(f["sourceSpan"][0], f["sourceSpan"][1]) for f in
               sorted(model["innerFields"], key=lambda x: x["sourceSpan"][0])]
    for i in range(1, len(ordered)):
        assert ordered[i][0] >= ordered[i - 1][1]


def test_inner_field_prefix_alias_not_confused_by_substring():
    # `AS Foo` must not anchor inside `AS Foobar` regardless of source order (word boundary).
    sql = "SELECT q.Foo AS Foo FROM (SELECT x.a AS Foobar, x.b AS Foo FROM x) q"
    model = ci_visualize.build_model(sql, "T")
    by = {f["output"]: f for f in model["innerFields"]}
    assert sql[slice(*by["Foobar"]["sourceSpan"])] == "AS Foobar"
    assert sql[slice(*by["Foo"]["sourceSpan"])] == "AS Foo"
    # distinct, non-overlapping spans
    assert by["Foobar"]["sourceSpan"] != by["Foo"]["sourceSpan"]


def test_inner_field_no_duplicate_spans_across_scopes():
    # A scalar subquery reuses the alias `Foo`; no two inner fields may share the same span.
    sql = "SELECT q.Foo AS Foo FROM (SELECT (SELECT x.a AS Foo FROM x) AS Foo, y.b AS Bar FROM y) q"
    model = ci_visualize.build_model(sql, "T")
    spans = [tuple(f["sourceSpan"]) for f in model["innerFields"]]
    assert len(spans) == len(set(spans)), f"duplicate inner-field spans: {spans}"


def test_explanation_labels_outer_projection_and_has_no_markdown():
    model = ci_visualize.build_model("SELECT q.Value__c AS Value__c FROM q", "Test__cio")
    report = ci_visualize.render_onboarding_html(model)

    assert "Outer projection (lineage not yet traced): q.Value__c" in report
    assert "**Value__c**" not in report


def test_join_explanation_discloses_scope_limit():
    model = ci_visualize.build_model(
        "SELECT a.id AS id FROM a INNER JOIN b ON a.id = b.id",
        "Test__cio",
    )
    report = ci_visualize.render_onboarding_html(model)

    assert "Join scope and left-side ownership are not modeled in this phase." in report


def test_hardcoded_setup_id_finding_is_narrowly_labeled():
    model = ci_visualize.build_model(
        "SELECT a AS a FROM x WHERE RecordTypeId = '012000000000001AAA'",
        "Test__cio",
    )

    assert model["findings"][0]["rule"] == "hardcoded-setup-id"
    assert "setup ID" in model["findings"][0]["detail"]


def test_embedded_json_cannot_terminate_script():
    sql = "SELECT '</script><script>alert(1)</script>' AS Value__c"
    report = ci_visualize.render_onboarding_html(ci_visualize.build_model(sql, "Test__cio"))

    assert "</script><script>alert(1)</script>" not in report
    assert "\\u003c/script\\u003e" in report


def test_embedded_json_escapes_js_line_separators():
    sql = "SELECT 'line\u2028separator\u2029end' AS Value__c"
    report = ci_visualize.render_onboarding_html(ci_visualize.build_model(sql, "Test__cio"))

    assert "\u2028" not in report
    assert "\u2029" not in report
    assert "\\u2028" in report
    assert "\\u2029" in report


def test_parse_failure_returns_nonzero_and_writes_no_report(tmp_path):
    source = tmp_path / "bad.sql"
    output = tmp_path / "bad.html"
    source.write_text("SELECT FROM")

    result = subprocess.run(
        [str(PYTHON), "-m", "data360_analyst.ci_visualize", "--input", str(source), "--out", str(output)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "parse failed" in result.stderr
    assert not output.exists()


def test_input_html_requires_explicit_output_path(tmp_path):
    source = tmp_path / "ok.sql"
    source.write_text("SELECT a AS a FROM x")

    result = subprocess.run(
        [str(PYTHON), "-m", "data360_analyst.ci_visualize", "--input", str(source)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "--out is required" in result.stderr


def test_client_root_defaults_output_under_reports(tmp_path):
    client = tmp_path / "Data360"
    (client / "queries").mkdir(parents=True)
    (client / "queries" / "Test__cio.sql").write_text("SELECT a AS a FROM x")

    result = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "data360_analyst.ci_visualize",
            "--client-root",
            str(client),
            "--name",
            "Test__cio",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (client / "reports" / "Test__cio-onboarding.html").exists()


def test_model_only_does_not_require_output_path(tmp_path):
    source = tmp_path / "ok.sql"
    source.write_text("SELECT a AS a FROM x")

    result = subprocess.run(
        [str(PYTHON), "-m", "data360_analyst.ci_visualize", "--input", str(source), "--model-only"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["ci"]["apiName"] == "ok"
