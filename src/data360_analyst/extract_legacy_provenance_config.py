#!/usr/bin/env python3
"""Extract a compatible legacy hybrid provenance HTML report into schema v1 config."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from data360_analyst.provenance_config import format_issues, validate_config
from data360_analyst.render_provenance_report import normalize_config


_BLOCK_RE = re.compile(
    r"const groups\s*=.*?const edgeData\s*=\s*\[.*?\n\];",
    re.DOTALL,
)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_H1_RE = re.compile(r'<h1[^>]*class="title"[^>]*>(.*?)</h1>', re.DOTALL | re.IGNORECASE)
_BADGE_RE = re.compile(r'<span[^>]*class="badge[^>]*>(.*?)</span>', re.DOTALL | re.IGNORECASE)
_GROUP_LABEL_RE = re.compile(r'<label[^>]*for="groupSelect"[^>]*>(.*?)</label>', re.DOTALL | re.IGNORECASE)
_GRID_TITLE_RE = re.compile(r'<div[^>]*class="pane-header"[^>]*id="gridPaneHeading"[^>]*>\s*(?:<button.*?</button>\s*)?([^<]+)', re.DOTALL | re.IGNORECASE)
_GRAPH_SUBTITLE_RE = re.compile(r'<span class="subtitle">(—\s*[^<]+)</span>', re.IGNORECASE)
_GRAPH_ARIA_RE = re.compile(r'<div id="network"[^>]*aria-label="([^"]+)"', re.DOTALL | re.IGNORECASE)
_HELP_RE = re.compile(r'<div class="help-body">(.*?)</div>\s*</div>\s*</div>\s*<main', re.DOTALL | re.IGNORECASE)
_HELP_TITLE_RE = re.compile(r'<h2 id="helpTitle">(.*?)</h2>', re.DOTALL | re.IGNORECASE)
_HELP_BUTTON_RE = re.compile(r'<button[^>]*onclick="openHelp\(\)"[^>]*aria-label="([^"]+)"', re.DOTALL | re.IGNORECASE)
_ENV_RE = re.compile(r"^\s*([^·]+?)\s*·\s*verified\s+(\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE)
_LANE_SELECT_RE = re.compile(
    r'<select[^>]*id="laneFilter"[^>]*>(.*?)</select>',
    re.DOTALL | re.IGNORECASE,
)
_OPTION_RE = re.compile(
    r'<option[^>]*value="([^"]+)"[^>]*>(.*?)</option>',
    re.DOTALL | re.IGNORECASE,
)


ROLE_BY_LAYER = {
    "snowflake": "source",
    "dlm": "dmo",
    "cat_ci": "ci",
    "dpsect_ci": "ci",
    "dpe": "dpe",
    "crm": "target_object",
    "ui": "consumer",
    "crm_formula": "derived_field",
}


def _text(match: re.Match[str] | None, default: str) -> str:
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else default


def extract_legacy_data(html_path: Path) -> tuple[str, dict[str, Any]]:
    """Use Node's parser to evaluate only the three legacy data literals."""
    html = html_path.read_text()
    block_match = _BLOCK_RE.search(html)
    if not block_match:
        raise ValueError("compatible groups/nodeData/edgeData block was not found")
    block = block_match.group(0)
    block = re.sub(r"const groups\s*=", "globalThis.groups =", block, count=1)
    block = re.sub(r"const nodeData\s*=", "globalThis.nodeData =", block, count=1)
    block = re.sub(r"const edgeData\s*=", "globalThis.edgeData =", block, count=1)
    node_script = """
const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync(process.argv[1], 'utf8');
const context = {};
vm.createContext(context);
vm.runInContext(code, context, { timeout: 5000 });
process.stdout.write(JSON.stringify({groups: context.groups, nodes: context.nodeData, edges: context.edgeData}));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(block)
        block_path = Path(handle.name)
    try:
        result = subprocess.run(
            ["node", "-e", node_script, str(block_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    finally:
        block_path.unlink(missing_ok=True)
    if result.returncode:
        raise ValueError(f"could not parse legacy data literals: {result.stderr.strip()}")
    return html, json.loads(result.stdout)


def convert_legacy_report(html_path: Path, report_id: str) -> dict[str, Any]:
    html, legacy = extract_legacy_data(html_path)
    layer_names = []
    for node in legacy["nodes"]:
        if node["layer"] not in layer_names:
            layer_names.append(node["layer"])
    layer_labels = _legacy_layer_labels(html, layer_names)
    layer_colors = _legacy_layer_colors(html, layer_names)

    title = _text(_H1_RE.search(html), "Provenance Report")
    document_title = _text(_TITLE_RE.search(html), title)
    badge = _text(_BADGE_RE.search(html), "ENV · verified 1970-01-01")
    env_match = _ENV_RE.match(badge)
    environment, verified = (
        (env_match.group(1).strip(), env_match.group(2))
        if env_match
        else ("ENV", "1970-01-01")
    )
    group_label = _text(_GROUP_LABEL_RE.search(html), "Group")
    left_title = _text(_GRID_TITLE_RE.search(html), "Trace Endpoints")
    graph_subtitles = _GRAPH_SUBTITLE_RE.findall(html)
    graph_subtitle = next((value for value in graph_subtitles if "layer" in value.lower()), "")
    graph_description = _text(_GRAPH_ARIA_RE.search(html), f"Provenance graph for {title}.")

    groups = []
    endpoints = []
    for group_id, group in legacy["groups"].items():
        endpoint_ids = []
        for measure in group["measures"]:
            endpoint_id = measure["id"]
            endpoint_ids.append(endpoint_id)
            endpoint = {
                "id": endpoint_id,
                "groupId": group_id,
                "nodeId": measure["uiNode"],
                "label": measure["name"],
            }
            if measure.get("crmField"):
                endpoint["technicalLabel"] = measure["crmField"]
            if measure.get("chain"):
                endpoint["summaryHtml"] = f"<p>{measure['chain']}</p>"
            if measure.get("note"):
                endpoint["noteHtml"] = f"<p>{measure['note']}</p>"
            endpoints.append(endpoint)
        converted_group = {"id": group_id, "label": group["label"], "endpointIds": endpoint_ids}
        if group.get("note"):
            converted_group["noteHtml"] = f"<p>{group['note']}</p>"
        groups.append(converted_group)

    derived_evidence_id = "evidence-derived-fields"
    endpoint_roles = {
        node["id"]: _legacy_node_role(node, legacy["edges"])
        for node in legacy["nodes"]
    }
    endpoint_role_values = {
        endpoint_roles.get(endpoint["nodeId"]) for endpoint in endpoints
    }
    config: dict[str, Any] = {
        "schemaVersion": "1.0",
        "report": {
            "id": report_id,
            "title": title,
            "documentTitle": document_title,
            "environment": environment,
            "verifiedDate": verified,
            "groupSelectorLabel": group_label,
            "leftPaneTitle": left_title,
            "endpointHeader": _endpoint_header(endpoint_role_values),
            "endpointInstruction": "click an item to trace",
            "graphTitle": "Provenance Graph",
            "graphSubtitle": graph_subtitle,
            "graphDescription": graph_description,
            "filterLabel": "Filter graph by group",
            "lineageBoundary": "consumer_complete" if "crm_formula" in layer_names else "strict_writeback",
            "initialGroupId": next(iter(legacy["groups"])),
        },
        "layers": [
            {
                "id": layer,
                "label": layer_labels.get(layer, layer.replace("_", " ").title()),
                "color": layer_colors.get(layer, "#64748b"),
                "order": index + 1,
            }
            for index, layer in enumerate(layer_names)
        ],
        "groups": groups,
        "endpoints": endpoints,
        "nodes": [_convert_node(node, legacy["edges"]) for node in legacy["nodes"]],
        "edges": [
            _convert_edge(edge, index, derived_evidence_id)
            for index, edge in enumerate(legacy["edges"], 1)
        ],
        "sources": [
            {
                "id": derived_evidence_id,
                "kind": "sobject_describe",
                "artifact": "Derived target fields",
                "environment": environment,
                "verifiedAt": f"{verified}T00:00:00Z",
                "status": "verified",
            }
        ] if "crm_formula" in layer_names else [],
    }
    lane_values = sorted({lane for node in legacy["nodes"] for lane in node.get("lanes", [])})
    if lane_values:
        lane_labels = _legacy_lane_labels(html)
        config["filters"] = [
            {
                "id": f"filter-{lane}",
                "label": lane_labels.get(lane, lane.replace("_", " ").title()),
                "tags": [lane],
            }
            for lane in lane_values
        ]
        for collection in ("nodes", "edges"):
            for item in config[collection]:
                tags = item.pop("_legacyLanes", [])
                if tags:
                    item["filterTags"] = tags
    help_match = _HELP_RE.search(html)
    if help_match:
        config["help"] = {
            "title": _text(_HELP_TITLE_RE.search(html), "Help"),
            "buttonLabel": _text(_HELP_BUTTON_RE.search(html), "Help"),
            "html": _sanitize_legacy_html(help_match.group(1).strip()),
        }
    return config


def _convert_node(node: dict[str, Any], edges: list[dict[str, Any]]) -> dict[str, Any]:
    role = _legacy_node_role(node, edges)
    converted: dict[str, Any] = {
        "id": node["id"],
        "label": node["label"],
        "role": role,
        "layerId": node["layer"],
        "groups": _node_groups(node),
        "description": node.get("description") or node["label"],
        "_legacyLanes": node.get("lanes", []),
    }
    if isinstance(node.get("level"), int):
        converted["level"] = node["level"]
    color = node.get("color")
    if (
        isinstance(color, str)
        and re.fullmatch(r"#[0-9a-fA-F]{6}", color)
        and color.lower() != "#64748b"
    ):
        converted["style"] = {"color": color.lower()}
    if node.get("onboarding"):
        converted["onboardingHtml"] = node["onboarding"]
    properties = {}
    for key in ("grain", "source", "target", "type", "fields", "sequence", "operation"):
        if key in node:
            properties[key] = node[key]
    if role == "writeback":
        properties.setdefault("sequence", _sequence_from_node(node))
        properties.setdefault("sourceName", node.get("source") or "See DPE metadata")
        properties.setdefault("targetObject", node.get("target") or "See DPE metadata")
        properties.setdefault("operation", node.get("operation") or "Upsert")
        properties.setdefault("upsertKey", "See DPE metadata")
    if properties:
        converted["properties"] = properties
    if node.get("lanes"):
        converted.setdefault("properties", {})["Lanes"] = node["lanes"]
    converted["displayProperties"] = _legacy_display_properties(
        role, properties, node.get("lanes", [])
    )
    if not converted["displayProperties"]:
        converted.pop("displayProperties")
    return converted


def _legacy_node_role(node: dict[str, Any], edges: list[dict[str, Any]]) -> str:
    role = ROLE_BY_LAYER.get(node["layer"], "ci")
    if node["layer"] == "dpe":
        return "dpe" if node["id"] == "dpe_parent" else "writeback"
    if node["layer"] == "crm":
        return (
            "target_object"
            if node["label"].startswith(("RecordAlert (", "Record_Alert_Item__c ("))
            else "target_field"
        )
    return role


def _endpoint_header(roles: set[str | None]) -> str:
    clean = roles - {None}
    if clean and clean.issubset({"consumer", "derived_field"}):
        return "Measure"
    if clean and clean.issubset({"target_object", "target_field"}):
        return "CRM Field"
    if clean == {"writeback"}:
        return "Writeback"
    return "Trace Endpoint"


def _convert_edge(
    edge: dict[str, Any], index: int, derived_evidence_id: str
) -> dict[str, Any]:
    edge_type = "grouping" if edge.get("grouping") else "derivation" if edge.get("dashes") else "data_flow"
    if edge.get("label") == "master-detail":
        edge_type = "relationship"
    converted: dict[str, Any] = {
        "id": f"edge-{index}",
        "from": edge["from"],
        "to": edge["to"],
        "type": edge_type,
        "groups": [],
        "_legacyLanes": edge.get("lanes", []),
    }
    if edge.get("label"):
        converted["label"] = edge["label"]
    if edge.get("explanation"):
        converted["explanationHtml"] = _sanitize_legacy_html(
            f"<p>{edge['explanation']}</p>"
        )
    if edge_type == "derivation":
        converted["evidenceIds"] = [derived_evidence_id]
    return converted


def _node_groups(node: dict[str, Any]) -> list[str]:
    groups = []
    value = node.get("group")
    if isinstance(value, str) and value not in {"shared", "all"}:
        groups.append(value)
    return groups


def _sequence_from_node(node: dict[str, Any]) -> int:
    match = re.search(r"(?:wb|writeback)\s*(\d+)|seq(?:uence)?\s*(\d+)", node["label"], re.IGNORECASE)
    if not match:
        match = re.search(r"dpe_wb(\d+)", node["id"], re.IGNORECASE)
    return int(next(value for value in match.groups() if value)) if match else 0


def _legacy_layer_labels(html: str, layers: list[str]) -> dict[str, str]:
    labels = {}
    for layer, label in re.findall(r'<div class="legend-row" data-layer="([^"]+)">.*?</div>([^<]+)</div>', html):
        labels[layer] = label.strip()
    return {layer: labels.get(layer, layer.replace("_", " ").title()) for layer in layers}


def _legacy_layer_colors(html: str, layers: list[str]) -> dict[str, str]:
    variables = dict(re.findall(r"--([a-zA-Z0-9_-]+):\s*(#[0-9a-fA-F]{6})", html))
    mapping = {
        "snowflake": "snowflake", "dlm": "dlm", "cat_ci": "catci",
        "dpsect_ci": "dpsectci", "dpe": "dpe", "crm": "crm",
        "ui": "ui", "crm_formula": "crmformula",
    }
    return {layer: variables.get(mapping.get(layer, ""), "#64748b") for layer in layers}


def _legacy_lane_labels(html: str) -> dict[str, str]:
    match = _LANE_SELECT_RE.search(html)
    if not match:
        return {}
    return {
        value: re.sub(r"\s+", " ", label).strip()
        for value, label in _OPTION_RE.findall(match.group(1))
        if value != "all"
    }


def _legacy_display_properties(
    role: str, properties: dict[str, Any], lanes: list[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(label: str, value: Any, multiline: bool = False) -> None:
        if value in (None, "", [], {}):
            return
        if isinstance(value, list):
            value = "\n".join(str(item) for item in value) if multiline else ", ".join(
                str(item) for item in value
            )
        rows.append({"label": label, "value": value, **({"multiline": True} if multiline else {})})

    if role == "writeback":
        add("Source CI", properties.get("source") or properties.get("sourceName"))
        add("Target", properties.get("target") or properties.get("targetObject"))
        add("Lanes", lanes)
        add("Fields", properties.get("fields"), multiline=True)
    elif role == "dpe":
        add("Developer Name", properties.get("developerName"))
        add("Status", properties.get("status"))
        add("Modified Date", properties.get("modifiedDate"))
        add("Target", properties.get("target"))
        add("Lanes", lanes)
    elif role in {"ci", "source", "dlo", "dmo"}:
        add("Grain", properties.get("grain"))
        add("Lanes", lanes)
        add("Fields", properties.get("fields"), multiline=True)
    elif role in {"target_object", "target_field", "derived_field"}:
        add("API Name", properties.get("apiName"))
        add("Type", properties.get("dataType") or properties.get("type"))
        add("External ID", properties.get("externalId"))
        add("Formula", properties.get("formula"))
        add("Lanes", lanes)
    else:
        add("Lanes", lanes)
    return rows


def _sanitize_legacy_html(value: str) -> str:
    """Normalize trusted legacy reader markup to the schema v1 HTML allowlist."""
    value = re.sub(r"\s+style=(?:\"[^\"]*\"|'[^']*')", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r'<div\s+class=(?:"lane-block"|\'lane-block\')>',
        '<div class="anchor">',
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r'<span\s+class=(?:"lane-tag"|\'lane-tag\')>', "<strong>", value, flags=re.IGNORECASE)
    value = re.sub(r"</span>", "</strong>", value, flags=re.IGNORECASE)
    return value


def _assign_groups(config: dict[str, Any]) -> None:
    endpoint_groups = {endpoint["nodeId"]: endpoint["groupId"] for endpoint in config["endpoints"]}
    group_ids = [group["id"] for group in config["groups"]]
    for node in config["nodes"]:
        if not node["groups"]:
            node["groups"] = [endpoint_groups[node["id"]]] if node["id"] in endpoint_groups else group_ids
    node_groups = {node["id"]: set(node["groups"]) for node in config["nodes"]}
    for edge in config["edges"]:
        shared = node_groups[edge["from"]] & node_groups[edge["to"]]
        edge["groups"] = sorted(shared or node_groups[edge["from"]] | node_groups[edge["to"]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = convert_legacy_report(args.input, args.report_id)
        _assign_groups(config)
        result = validate_config(config)
        if not result.valid:
            raise ValueError(format_issues(result.errors))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(normalize_config(config))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if result.warnings:
        print(format_issues(result.warnings), file=sys.stderr)
    print(f"WROTE: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
