"""Tests for compatible legacy provenance extraction and live-evidence enrichment."""

import json

from data360_analyst.enrich_provenance_evidence import enrich
from data360_analyst.extract_legacy_provenance_config import (
    _convert_node,
    _endpoint_header,
    _legacy_lane_labels,
    _sanitize_legacy_html,
)
from data360_analyst.provenance_config import validate_config, validate_safe_html


def test_legacy_help_markup_is_normalized_to_allowlist():
    source = (
        '<div class="lane-block"><span class="lane-tag">Lane</span>'
        '<p style="margin-top:8px">Description</p></div>'
    )
    normalized = _sanitize_legacy_html(source)
    assert normalized == (
        '<div class="anchor"><strong>Lane</strong>'
        '<p>Description</p></div>'
    )
    assert validate_safe_html(normalized) == []


def test_enrichment_copies_exact_writeback_mappings():
    config = {
        "schemaVersion": "1.0",
        "report": {"id": "example", "title": "Example", "environment": "TEST", "verifiedDate": "2026-08-01", "groupSelectorLabel": "Group", "leftPaneTitle": "Outputs", "endpointHeader": "Output", "graphDescription": "Example", "lineageBoundary": "strict_writeback", "initialGroupId": "g"},
        "layers": [{"id": "l", "label": "Layer", "color": "#123456", "order": 1}],
        "groups": [{"id": "g", "label": "Group", "endpointIds": ["ep"]}],
        "endpoints": [{"id": "ep", "groupId": "g", "nodeId": "wb", "label": "Write"}],
        "nodes": [{"id": "wb", "label": "Wb1", "role": "writeback", "layerId": "l", "groups": ["g"], "description": "Write", "properties": {"sequence": 1, "sourceName": "placeholder", "targetObject": "placeholder", "operation": "placeholder", "upsertKey": "placeholder"}}],
        "edges": [],
        "sources": []
    }
    dpe = {"result": {"records": [{"DeveloperName": "Example_DPE", "Metadata": {"datasources": [{"name": "CI_Source", "sourceName": "Source__cio"}], "writebacks": [{"writebackSequence": 1, "sourceName": "CI_Source", "targetObjectName": "Target__c", "operationType": "Upsert", "externalIdFieldName": "Key__c", "fields": [{"sourceFieldName": "Value_c", "targetFieldName": "Value__c", "parentName": None, "relationshipName": None}]}]}}]}}
    enriched = enrich(config, dpe, {}, None)
    props = enriched["nodes"][0]["properties"]
    assert props["sourceName"] == "Source__cio"
    assert props["targetObject"] == "Target__c"
    assert props["upsertKey"] == "Key__c"
    assert props["fieldMappings"] == [{"source": "Value_c", "target": "Value__c", "parent": None, "relationship": None}]
    display = {row["label"]: row for row in enriched["nodes"][0]["displayProperties"]}
    assert display["Source CI"]["value"] == "Source__cio"
    assert display["Target"]["value"] == "Target__c"
    assert display["Fields"] == {
        "label": "Fields",
        "value": "Value_c → Value__c",
        "multiline": True,
    }
    assert "fieldMappings" not in display
    assert [row["label"] for row in enriched["nodes"][0]["displayProperties"]] == [
        "Source CI",
        "Target",
        "Fields",
    ]
    assert validate_config(enriched).valid


def test_legacy_lane_labels_preserve_reader_facing_detail():
    html = '''
    <select id="laneFilter">
      <option value="all">All Lanes</option>
      <option value="plan">Plan (AOP + S&amp;OP)</option>
      <option value="actpln">ActPln (Sales Projection)</option>
    </select>
    '''
    assert _legacy_lane_labels(html) == {
        "plan": "Plan (AOP + S&amp;OP)",
        "actpln": "ActPln (Sales Projection)",
    }


def test_legacy_node_level_is_preserved():
    node = {
        "id": "dpe_parent",
        "label": "Example DPE",
        "level": 5,
        "layer": "dpe",
        "description": "Example",
        "lanes": ["one"],
    }
    assert _convert_node(node, [])["level"] == 5


def test_legacy_per_node_color_override_is_preserved():
    node = {
        "id": "dpe_parent",
        "label": "Example DPE",
        "level": 5,
        "layer": "dpe",
        "color": "#2e7d32",
        "description": "Example",
    }
    assert _convert_node(node, [])["style"] == {"color": "#2e7d32"}


def test_endpoint_header_is_derived_from_terminal_roles():
    assert _endpoint_header({"consumer"}) == "Measure"
    assert _endpoint_header({"consumer", "derived_field"}) == "Measure"
    assert _endpoint_header({"target_object", "target_field"}) == "CRM Field"
    assert _endpoint_header({"writeback"}) == "Writeback"
    assert _endpoint_header({"ci", "target_field"}) == "Trace Endpoint"
