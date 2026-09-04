"""Tests for provenance report configuration validation and traversal."""

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest



from data360_analyst.provenance_config import collect_upstream, validate_config, validate_safe_html


FIXTURES = Path(__file__).parent / "fixtures" / "provenance"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    "name",
    [
        "simple-one-ci.json",
        "parent-child.json",
        "multi-branch.json",
        "consumer-complete.json",
    ],
)
def test_synthetic_fixtures_are_valid(name):
    result = validate_config(load_fixture(name))
    assert result.errors == []


def test_duplicate_ids_fail():
    config = load_fixture("simple-one-ci.json")
    config["nodes"].append(copy.deepcopy(config["nodes"][0]))
    result = validate_config(config)
    assert any("duplicate nodes id" in issue.message for issue in result.errors)


def test_missing_edge_node_fails():
    config = load_fixture("simple-one-ci.json")
    config["edges"][0]["from"] = "missing-node"
    result = validate_config(config)
    assert any(issue.path.endswith(".from") and "unknown node" in issue.message for issue in result.errors)


def test_missing_endpoint_node_fails():
    config = load_fixture("simple-one-ci.json")
    config["endpoints"][0]["nodeId"] = "missing-node"
    result = validate_config(config)
    assert any(issue.path.endswith(".nodeId") and "unknown node" in issue.message for issue in result.errors)


def test_group_endpoint_membership_must_be_consistent():
    config = load_fixture("multi-branch.json")
    config["groups"][0]["endpointIds"].append("retention-score")
    result = validate_config(config)
    assert any("declares group" in issue.message for issue in result.errors)


def test_invalid_role_and_edge_type_fail():
    config = load_fixture("simple-one-ci.json")
    config["nodes"][0]["role"] = "category_ci"
    config["edges"][0]["type"] = "annotation"
    result = validate_config(config)
    messages = [issue.message for issue in result.errors]
    assert any("unknown node role" in message for message in messages)
    assert any("unknown edge type" in message for message in messages)


@pytest.mark.parametrize("edge_type", ["grouping", "relationship"])
def test_traceable_override_is_rejected(edge_type):
    config = load_fixture("parent-child.json")
    edge = next(edge for edge in config["edges"] if edge["type"] == edge_type)
    edge["traceable"] = True
    result = validate_config(config)
    assert any("traceable overrides are not allowed" in issue.message for issue in result.errors)


def test_grouping_and_relationship_edges_are_not_traversed():
    config = load_fixture("parent-child.json")
    upstream = collect_upstream(config, "target-child")
    assert {"target-child", "wb-child", "ci-child"}.issubset(upstream)
    assert "target-parent" not in upstream
    assert "dpe-main" not in upstream


def test_derivation_traversal_depends_on_lineage_boundary():
    config = load_fixture("consumer-complete.json")
    assert "field-score" in collect_upstream(config, "ui-index")
    strict = copy.deepcopy(config)
    strict["report"]["lineageBoundary"] = "strict_writeback"
    assert "field-score" not in collect_upstream(strict, "ui-index")
    assert "field-index" in collect_upstream(strict, "ui-index")


def test_consumer_complete_derivation_requires_evidence():
    config = load_fixture("consumer-complete.json")
    edge = next(edge for edge in config["edges"] if edge["type"] == "derivation")
    edge.pop("evidenceIds")
    result = validate_config(config)
    assert any("derivation edges require evidence" in issue.message for issue in result.errors)


@pytest.mark.parametrize(
    "html",
    [
        "<script>alert(1)</script>",
        "<p onclick=\"alert(1)\">Bad</p>",
        "<a href=\"javascript:alert(1)\">Bad</a>",
        "<div class=\"unknown\">Bad</div>",
        "<p style=\"color:red\">Bad</p>",
    ],
)
def test_safe_html_rejects_unlisted_markup(html):
    assert validate_safe_html(html)


def test_safe_html_accepts_allowlisted_markup():
    html = (
        '<div class="anchor">Summary</div><h4>Details</h4>'
        "<p><strong>Verified</strong> from <code>Example__cio</code>.</p>"
        "<ul><li>One</li></ul>"
    )
    assert validate_safe_html(html) == []


def test_closing_script_sequence_fails_even_as_text():
    config = load_fixture("simple-one-ci.json")
    config["nodes"][0]["description"] = "unsafe </script><script>alert(1)</script>"
    result = validate_config(config)
    assert any("closing script sequences" in issue.message for issue in result.errors)


def test_customer_value_preview_keys_fail():
    config = load_fixture("simple-one-ci.json")
    config["nodes"][0]["sampleRows"] = [{"account": "Example"}]
    result = validate_config(config)
    assert any("customer row/value preview" in issue.message for issue in result.errors)


def test_outbound_content_scrub_fails_on_internal_references():
    config = load_fixture("simple-one-ci.json")
    config["nodes"][0]["description"] = "See [[internal-note]] via MCP."
    result = validate_config(config)
    messages = [issue.message for issue in result.errors]
    assert any("memory-link syntax" in message for message in messages)
    assert any("MCP mechanism reference" in message for message in messages)


def test_unknown_filter_tag_fails():
    config = load_fixture("multi-branch.json")
    config["nodes"][0]["filterTags"] = ["not-defined"]
    result = validate_config(config)
    assert any("unknown filter tag" in issue.message for issue in result.errors)


def test_writeback_requires_standard_properties():
    config = load_fixture("simple-one-ci.json")
    writeback = next(node for node in config["nodes"] if node["role"] == "writeback")
    writeback["properties"].pop("upsertKey")
    result = validate_config(config)
    assert any(issue.path.endswith(".properties.upsertKey") for issue in result.errors)


def test_node_level_must_be_non_negative_integer():
    config = load_fixture("simple-one-ci.json")
    config["nodes"][0]["level"] = "1"
    result = validate_config(config)
    assert any(issue.path.endswith(".level") for issue in result.errors)


def test_display_properties_require_display_ready_scalars():
    config = load_fixture("simple-one-ci.json")
    config["nodes"][0]["displayProperties"] = [
        {"label": "Bad", "value": {"raw": "object"}}
    ]
    result = validate_config(config)
    assert any("display-ready scalar" in issue.message for issue in result.errors)


def test_cli_returns_zero_for_valid_config():
    result = subprocess.run(
        [sys.executable, "-m", "data360_analyst.validate_provenance_config",
         str(FIXTURES / "simple-one-ci.json")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "VALID:" in result.stdout


def test_cli_returns_nonzero_for_invalid_config(tmp_path):
    config = load_fixture("simple-one-ci.json")
    config["edges"][0]["to"] = "missing-node"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(config))
    result = subprocess.run(
        [sys.executable, "-m", "data360_analyst.validate_provenance_config", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "ERROR:" in result.stdout
