"""Tests for ci_visualize.py SQL syntax coloring.

The coloring lives in client-side JS (`highlight()` + `buildSql()`) inside the HTML
template. To test the *shipped* code rather than a reimplementation, we extract those
functions verbatim from the template and run them under node with a minimal `document`
stub. Node-backed tests skip when node is unavailable; the template-wiring tests always
run (no external dependency).
"""

import json
import re
import shutil
import subprocess

import pytest

from data360_analyst import ci_visualize


NODE = shutil.which("node")
TEMPLATE = ci_visualize._HTML_TEMPLATE


def _extract_js():
    """Slice the contiguous esc/SQL_KW/highlight/buildSql block out of the template."""
    start = TEMPLATE.index("function esc(s)")
    end = TEMPLATE.index("function byId(id)")
    block = TEMPLATE[start:end]
    assert "function highlight(text)" in block
    assert "function buildSql()" in block
    return block


def _embedded_model_json(sql, name="Test__cio"):
    """Return the exact MODEL JSON string that render_onboarding_html embeds in the page."""
    html = ci_visualize.render_onboarding_html(ci_visualize.build_model(sql, name))
    start = html.index("const MODEL = ") + len("const MODEL = ")
    end = html.index(";\nconst SQL", start)
    return html[start:end]


def _run_js(driver, model_json=None):
    """Run the extracted JS plus a driver snippet under node; return parsed JSON stdout."""
    model_json = model_json or '{"sql": "", "elements": []}'
    harness = (
        _extract_js()
        + f"\nconst MODEL = {model_json};\n"
        + "const SQL = MODEL.sql, ELS = MODEL.elements;\n"
        + "let CAPTURED = null;\n"
        + "const document = {getElementById: () => ({set innerHTML(v){ CAPTURED = v; }})};\n"
        + driver
    )
    result = subprocess.run([NODE, "-e", harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _assert_well_nested(html):
    """span/i tags must be balanced and properly nested (no interleaving)."""
    stack = []
    for tag in re.findall(r"</?(?:span|i)\b[^>]*>", html):
        name = re.match(r"</?(\w+)", tag).group(1)
        if tag.startswith("</"):
            assert stack and stack.pop() == name, f"unbalanced close {tag!r} in {html!r}"
        else:
            stack.append(name)
    assert not stack, f"unclosed tags {stack} in {html!r}"


# --- template wiring (always run, no node) ---------------------------------

def test_template_defines_token_css_dark_and_light():
    for cls, color in [("k", "#c792ea"), ("fn", "#82aaff"), ("s", "#c3e88d"),
                       ("n", "#f78c6c"), ("c", "#637777")]:
        assert f"pre.sql .{cls}{{color:{color};}}" in TEMPLATE
    for cls in ["k", "fn", "s", "n", "c"]:
        assert f'[data-theme="light"] pre.sql .{cls}{{color:' in TEMPLATE


def test_template_routes_text_through_highlight():
    # buildSql must flush plain-text runs through highlight(), not raw esc().
    assert "h+=highlight(buf)" in TEMPLATE
    assert "function highlight(text)" in TEMPLATE


# --- highlight() behavior (node) -------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node not available")
def test_highlight_classifies_token_types():
    out = _run_js(
        "console.log(JSON.stringify({"
        "kw: highlight('SELECT'),"
        "fn: highlight('SUM(x)'),"
        "str: highlight(\"'active'\"),"
        "num: highlight('42'),"
        "com: highlight('-- note'),"
        "}));"
    )
    assert out["kw"] == '<i class="k">SELECT</i>'
    assert out["fn"].startswith('<i class="fn">SUM</i>')
    assert out["str"] == '<i class="s">\'active\'</i>'
    assert out["num"] == '<i class="n">42</i>'
    assert out["com"] == '<i class="c">-- note</i>'


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_highlight_escapes_html_metacharacters():
    out = _run_js("console.log(JSON.stringify({h: highlight('a >= b & c < d')}));")
    assert "&gt;=" in out["h"]
    assert "&amp;" in out["h"]
    assert "&lt;" in out["h"]
    assert "<script" not in out["h"]


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_highlight_keyword_needs_word_boundary_and_plain_identifier():
    # 'SELECTED' is an identifier, not the SELECT keyword; a bare identifier stays uncolored.
    out = _run_js(
        "console.log(JSON.stringify({sel: highlight('SELECTED'), id: highlight('customer__c')}));"
    )
    assert 'class="k"' not in out["sel"]
    assert out["id"] == "customer__c"


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_highlight_identifier_only_a_function_when_followed_by_paren():
    out = _run_js("console.log(JSON.stringify({f: highlight('foo(x)'), n: highlight('foo bar')}));")
    assert out["f"].startswith('<i class="fn">foo</i>')
    assert 'class="fn"' not in out["n"]


# --- buildSql() end-to-end (node): coloring + annotation-span integrity ------

@pytest.mark.skipif(NODE is None, reason="node not available")
def test_buildsql_colors_and_keeps_spans_well_nested():
    sql = "SELECT COALESCE(a.Name__c, 'unknown') AS customer__c FROM Account__dlm AS a"
    out = _run_js(
        "buildSql(); console.log(JSON.stringify({html: CAPTURED}));",
        model_json=_embedded_model_json(sql),
    )
    html = out["html"]
    # syntax tokens present
    assert 'class="k">SELECT</i>' in html
    assert 'class="fn">COALESCE</i>' in html
    assert "class=\"s\">'unknown'</i>" in html
    # clickable annotation spans still emitted
    assert 'class="span field"' in html
    # a function token nests inside a clickable span (boundary not crossed)
    assert re.search(r'<span class="span field"[^>]*><i class="fn">COALESCE</i>', html)
    # nothing interleaves
    _assert_well_nested(html)
