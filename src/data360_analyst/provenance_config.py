#!/usr/bin/env python3
"""Validation and traversal rules for provenance report configurations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterable


SCHEMA_VERSION = "1.0"
NODE_ROLES = {
    "source",
    "dlo",
    "dmo",
    "ci",
    "dpe",
    "writeback",
    "target_object",
    "target_field",
    "derived_field",
    "consumer",
}
EDGE_TYPES = {"data_flow", "grouping", "derivation", "relationship"}
LINEAGE_BOUNDARIES = {"strict_writeback", "consumer_complete"}
CLAIM_STATUSES = {"verified", "documented", "inferred", "unverified"}
EVIDENCE_KINDS = {
    "dpe_body",
    "ci_sql",
    "ci_metadata",
    "dlo_metadata",
    "dlo_dmo_mapping",
    "dmo_metadata",
    "dmo_relationships",
    "sobject_describe",
    "ui_capture",
    "automation_metadata",
    "approved_design",
    "approved_domain_reference",
    "platform_documentation",
}

_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_EVENT_ATTR_RE = re.compile(r"^on", re.IGNORECASE)
_UNSAFE_URI_RE = re.compile(r"^\s*(?:javascript|data):", re.IGNORECASE)
_OUTBOUND_PATTERNS = (
    (re.compile(r"\[\["), "memory-link syntax"),
    (re.compile(r"(?:^|[\\/])\.claude[\\/]", re.IGNORECASE), "local .claude path"),
    (re.compile(r"(?:^|[\\/])tickets[\\/]", re.IGNORECASE), "local tickets path"),
    (re.compile(r"(?:^|[\\/])daily-notes[\\/]", re.IGNORECASE), "local daily-notes path"),
    (re.compile(r"\bmcp__", re.IGNORECASE), "MCP tool name"),
    (re.compile(r"\bvia\s+MCP\b", re.IGNORECASE), "MCP mechanism reference"),
)
_CUSTOMER_DATA_KEYS = {
    "customerdata",
    "customerrows",
    "samplerecord",
    "samplerecords",
    "samplerow",
    "samplerows",
    "valuepreview",
    "valuespreview",
}

_ALLOWED_HTML_TAGS = {
    "p",
    "strong",
    "em",
    "code",
    "ul",
    "ol",
    "li",
    "h3",
    "h4",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "br",
    "div",
}
_VOID_HTML_TAGS = {"br"}
_ALLOWED_DIV_CLASSES = {"anchor"}


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    path: str
    message: str


@dataclass
class ValidationResult:
    issues: list[ValidationIssue]

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def valid(self) -> bool:
        return not self.errors


class _SafeHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self._open_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in _ALLOWED_HTML_TAGS:
            self.errors.append(f"element <{tag}> is not allowed")
            return
        self._check_attrs(tag, attrs)
        if tag not in _VOID_HTML_TAGS:
            self._open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag not in _ALLOWED_HTML_TAGS:
            self.errors.append(f"element <{tag}> is not allowed")
            return
        self._check_attrs(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag not in _ALLOWED_HTML_TAGS:
            self.errors.append(f"closing element </{tag}> is not allowed")
            return
        if tag in _VOID_HTML_TAGS:
            self.errors.append(f"void element <{tag}> must not have a closing tag")
            return
        if not self._open_tags or self._open_tags[-1] != tag:
            self.errors.append(f"closing element </{tag}> is not properly nested")
            return
        self._open_tags.pop()

    def handle_decl(self, decl: str) -> None:
        self.errors.append("HTML declarations are not allowed")

    def handle_pi(self, data: str) -> None:
        self.errors.append("processing instructions are not allowed")

    def handle_comment(self, data: str) -> None:
        self.errors.append("HTML comments are not allowed")

    def unknown_decl(self, data: str) -> None:
        self.errors.append("unknown HTML declarations are not allowed")

    def finish(self) -> list[str]:
        if self._open_tags:
            self.errors.append(
                "unclosed element(s): " + ", ".join(f"<{tag}>" for tag in self._open_tags)
            )
        return self.errors

    def _check_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            name = name.lower()
            value = value or ""
            if _EVENT_ATTR_RE.match(name):
                self.errors.append(f"event-handler attribute {name} is not allowed")
            elif name == "style":
                self.errors.append("inline style attributes are not allowed")
            elif name in {"href", "src"}:
                # No allowlisted element needs a URI attribute. Keep the scheme-specific
                # message for diagnostics, but reject every href/src fail-closed.
                if _UNSAFE_URI_RE.match(value):
                    self.errors.append(f"unsafe {name} URI is not allowed")
                else:
                    self.errors.append(f"attribute {name} is not allowed")
            elif name == "class" and tag == "div":
                classes = set(value.split())
                if not classes or not classes.issubset(_ALLOWED_DIV_CLASSES):
                    self.errors.append(f"div class {value!r} is not allowed")
            else:
                self.errors.append(f"attribute {name} on <{tag}> is not allowed")


def validate_safe_html(value: str) -> list[str]:
    """Return fail-closed allowlist violations for a reader-facing HTML fragment."""
    parser = _SafeHTMLParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception as exc:  # HTMLParser can raise on malformed declarations.
        return [f"invalid HTML: {exc}"]
    return parser.finish()


def edge_is_traversable(edge_type: str, lineage_boundary: str) -> bool:
    """Return whether an edge participates in provenance traversal."""
    if edge_type == "data_flow":
        return True
    if edge_type == "derivation":
        return lineage_boundary == "consumer_complete"
    return False


def collect_upstream(config: dict[str, Any], start_node_id: str) -> set[str]:
    """Collect upstream node IDs using the config's explicit edge semantics."""
    boundary = config.get("report", {}).get("lineageBoundary", "strict_writeback")
    parents: dict[str, list[str]] = {}
    for edge in config.get("edges", []):
        if not isinstance(edge, dict) or not edge_is_traversable(edge.get("type", ""), boundary):
            continue
        source, target = edge.get("from"), edge.get("to")
        if isinstance(source, str) and isinstance(target, str):
            parents.setdefault(target, []).append(source)
    seen = {start_node_id}
    pending = [start_node_id]
    while pending:
        current = pending.pop()
        for parent in parents.get(current, []):
            if parent not in seen:
                seen.add(parent)
                pending.append(parent)
    return seen


def validate_config(config: Any) -> ValidationResult:
    """Validate a provenance report config and return all errors and warnings."""
    issues: list[ValidationIssue] = []

    def error(path: str, message: str) -> None:
        issues.append(ValidationIssue("error", path, message))

    def warning(path: str, message: str) -> None:
        issues.append(ValidationIssue("warning", path, message))

    if not isinstance(config, dict):
        return ValidationResult([ValidationIssue("error", "$", "config must be an object")])

    required_root = ("schemaVersion", "report", "layers", "groups", "endpoints", "nodes", "edges")
    for key in required_root:
        if key not in config:
            error(f"$.{key}", "required field is missing")

    if config.get("schemaVersion") != SCHEMA_VERSION:
        error("$.schemaVersion", f"must equal {SCHEMA_VERSION!r}")

    report = config.get("report")
    if not isinstance(report, dict):
        error("$.report", "must be an object")
        report = {}
    _validate_report(report, error)

    collections: dict[str, list[Any]] = {}
    for name in ("layers", "groups", "endpoints", "nodes", "edges", "filters", "sources"):
        value = config.get(name, [])
        if not isinstance(value, list):
            error(f"$.{name}", "must be an array")
            value = []
        collections[name] = value

    ids: dict[str, set[str]] = {}
    for name, entries in collections.items():
        ids[name] = _validate_collection_ids(name, entries, error)

    layer_ids = ids["layers"]
    group_ids = ids["groups"]
    endpoint_ids = ids["endpoints"]
    node_ids = ids["nodes"]
    filter_ids = ids["filters"]
    source_ids = ids["sources"]
    evidence_kinds = {
        source.get("id"): source.get("kind")
        for source in collections["sources"]
        if isinstance(source, dict) and isinstance(source.get("id"), str)
    }

    _validate_layers(collections["layers"], error, warning)
    _validate_groups(
        collections["groups"], collections["endpoints"], endpoint_ids, group_ids, error, warning
    )
    _validate_endpoints(collections["endpoints"], group_ids, node_ids, source_ids, error)
    _validate_nodes(
        collections["nodes"], layer_ids, group_ids, source_ids, evidence_kinds, error, warning
    )
    _validate_edges(
        collections["edges"],
        node_ids,
        group_ids,
        source_ids,
        report.get("lineageBoundary"),
        error,
    )
    _validate_filters(collections["filters"], error)
    _validate_sources(collections["sources"], error)
    _validate_help(config.get("help"), source_ids, error)

    initial_group = report.get("initialGroupId")
    if isinstance(initial_group, str) and initial_group not in group_ids:
        error("$.report.initialGroupId", f"references unknown group {initial_group!r}")
    initial_filter = report.get("initialFilterId")
    if isinstance(initial_filter, str) and initial_filter not in filter_ids:
        error("$.report.initialFilterId", f"references unknown filter {initial_filter!r}")

    _validate_filter_tags(config, error)
    _validate_customer_data_keys(config, "$", error)
    _validate_script_termination(config, "$", error)
    _validate_reader_content(config, report.get("outbound", True), error)

    if not any(issue.severity == "error" for issue in issues):
        _warn_graph_quality(config, warning)

    return ValidationResult(issues)


def _validate_report(report: dict[str, Any], error) -> None:
    required_strings = (
        "id",
        "title",
        "environment",
        "verifiedDate",
        "groupSelectorLabel",
        "leftPaneTitle",
        "endpointHeader",
        "graphDescription",
        "lineageBoundary",
        "initialGroupId",
    )
    for key in required_strings:
        if not isinstance(report.get(key), str) or not report[key].strip():
            error(f"$.report.{key}", "must be a non-empty string")
    if isinstance(report.get("id"), str) and not _KEBAB_RE.fullmatch(report["id"]):
        error("$.report.id", "must be kebab-case")
    namespace = report.get("storageNamespace")
    if namespace is not None and (not isinstance(namespace, str) or not _KEBAB_RE.fullmatch(namespace)):
        error("$.report.storageNamespace", "must be kebab-case")
    if isinstance(report.get("verifiedDate"), str) and not _DATE_RE.fullmatch(report["verifiedDate"]):
        error("$.report.verifiedDate", "must use YYYY-MM-DD")
    if report.get("lineageBoundary") not in LINEAGE_BOUNDARIES:
        error(
            "$.report.lineageBoundary",
            "must be strict_writeback or consumer_complete",
        )
    if "outbound" in report and not isinstance(report["outbound"], bool):
        error("$.report.outbound", "must be a boolean")
    if "layout" in report and not isinstance(report["layout"], dict):
        error("$.report.layout", "must be an object")


def _validate_collection_ids(name: str, entries: list[Any], error) -> set[str]:
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        path = f"$.{name}[{index}]"
        if not isinstance(entry, dict):
            error(path, "must be an object")
            continue
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier:
            error(f"{path}.id", "must be a non-empty string")
        elif not _ID_RE.fullmatch(identifier):
            error(f"{path}.id", "must contain only lowercase letters, digits, _ or -")
        elif identifier in seen:
            error(f"{path}.id", f"duplicate {name} id {identifier!r}")
        else:
            seen.add(identifier)
    return seen


def _validate_layers(entries, error, warning) -> None:
    orders: set[int] = set()
    for index, layer in enumerate(entries):
        if not isinstance(layer, dict):
            continue
        path = f"$.layers[{index}]"
        _require_string(layer, "label", path, error)
        color = layer.get("color")
        if not isinstance(color, str) or not _HEX_RE.fullmatch(color):
            error(f"{path}.color", "must be a six-digit CSS hex color")
        order = layer.get("order")
        if not isinstance(order, int) or isinstance(order, bool):
            error(f"{path}.order", "must be an integer")
        elif order in orders:
            warning(f"{path}.order", f"layer order {order} is shared by another layer")
        else:
            orders.add(order)
        if "legend" in layer and not isinstance(layer["legend"], bool):
            error(f"{path}.legend", "must be a boolean")


def _validate_groups(entries, endpoints, endpoint_ids, group_ids, error, warning) -> None:
    endpoint_groups = {
        endpoint.get("id"): endpoint.get("groupId")
        for endpoint in endpoints
        if isinstance(endpoint, dict)
    }
    for index, group in enumerate(entries):
        if not isinstance(group, dict):
            continue
        path = f"$.groups[{index}]"
        _require_string(group, "label", path, error)
        refs = group.get("endpointIds")
        if not isinstance(refs, list) or not refs:
            error(f"{path}.endpointIds", "must be a non-empty array")
        else:
            for ref_index, ref in enumerate(refs):
                if ref not in endpoint_ids:
                    error(f"{path}.endpointIds[{ref_index}]", f"references unknown endpoint {ref!r}")
                elif endpoint_groups.get(ref) != group.get("id"):
                    error(
                        f"{path}.endpointIds[{ref_index}]",
                        f"endpoint {ref!r} declares group {endpoint_groups.get(ref)!r}",
                    )
        _validate_html_fields(group, path, error)

    # Verify the reverse endpoint.groupId relation after all groups exist.
    if not group_ids and entries:
        warning("$.groups", "no valid group IDs were found")


def _validate_endpoints(entries, group_ids, node_ids, source_ids, error) -> None:
    for index, endpoint in enumerate(entries):
        if not isinstance(endpoint, dict):
            continue
        path = f"$.endpoints[{index}]"
        _require_string(endpoint, "label", path, error)
        _validate_ref(endpoint, "groupId", group_ids, "group", path, error)
        _validate_ref(endpoint, "nodeId", node_ids, "node", path, error)
        _validate_evidence_refs(endpoint, path, source_ids, error)
        _validate_html_fields(endpoint, path, error)


def _validate_nodes(entries, layer_ids, group_ids, source_ids, evidence_kinds, error, warning) -> None:
    used_layers: set[str] = set()
    for index, node in enumerate(entries):
        if not isinstance(node, dict):
            continue
        path = f"$.nodes[{index}]"
        _require_string(node, "label", path, error)
        _require_string(node, "description", path, error)
        if node.get("role") not in NODE_ROLES:
            error(f"{path}.role", f"unknown node role {node.get('role')!r}")
        layer_id = node.get("layerId")
        if layer_id not in layer_ids:
            error(f"{path}.layerId", f"references unknown layer {layer_id!r}")
        else:
            used_layers.add(layer_id)
        _validate_ref_array(node, "groups", group_ids, "group", path, error, required=True)
        _validate_evidence_refs(node, path, source_ids, error)
        _validate_claim_status(node, path, error)
        _validate_html_fields(node, path, error)
        _validate_display_properties(node, path, error)
        if node.get("role") == "writeback":
            props = node.get("properties")
            if not isinstance(props, dict):
                error(f"{path}.properties", "writeback nodes require a properties object")
            else:
                for key in ("sequence", "sourceName", "targetObject", "operation", "upsertKey"):
                    if key not in props or props[key] in (None, ""):
                        error(f"{path}.properties.{key}", "required writeback property is missing")
        if "level" in node and (
            not isinstance(node["level"], int)
            or isinstance(node["level"], bool)
            or node["level"] < 0
        ):
            error(f"{path}.level", "must be a non-negative integer")
        if node.get("role") == "ci" and not _has_evidence_kind(node, evidence_kinds, "ci_sql"):
            warning(f"{path}.evidenceIds", "CI node has no CI SQL evidence reference")
        if node.get("role") in {"target_object", "target_field"} and not _has_evidence_kind(
            node, evidence_kinds, "sobject_describe"
        ):
            warning(
                f"{path}.evidenceIds",
                "target node has no sObject describe evidence reference",
            )

    for layer_id in layer_ids - used_layers:
        warning("$.layers", f"layer {layer_id!r} has no nodes")


def _validate_edges(entries, node_ids, group_ids, source_ids, lineage_boundary, error) -> None:
    for index, edge in enumerate(entries):
        if not isinstance(edge, dict):
            continue
        path = f"$.edges[{index}]"
        _validate_ref(edge, "from", node_ids, "node", path, error)
        _validate_ref(edge, "to", node_ids, "node", path, error)
        edge_type = edge.get("type")
        if edge_type not in EDGE_TYPES:
            error(f"{path}.type", f"unknown edge type {edge_type!r}")
        if "traceable" in edge:
            error(f"{path}.traceable", "traceable overrides are not allowed; traversal derives from edge type")
        _validate_ref_array(edge, "groups", group_ids, "group", path, error, required=True)
        _validate_evidence_refs(edge, path, source_ids, error)
        _validate_html_fields(edge, path, error)
        if edge_type == "derivation" and lineage_boundary == "consumer_complete" and not edge.get(
            "evidenceIds"
        ):
            error(
                f"{path}.evidenceIds",
                "consumer-complete derivation edges require evidence",
            )


def _validate_filters(entries, error) -> None:
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            continue
        path = f"$.filters[{index}]"
        _require_string(item, "label", path, error)
        tags = item.get("tags")
        if not isinstance(tags, list) or not tags or not all(isinstance(tag, str) and tag for tag in tags):
            error(f"{path}.tags", "must be a non-empty array of strings")


def _validate_sources(entries, error) -> None:
    for index, source in enumerate(entries):
        if not isinstance(source, dict):
            continue
        path = f"$.sources[{index}]"
        _require_string(source, "artifact", path, error)
        _require_string(source, "verifiedAt", path, error)
        if source.get("kind") not in EVIDENCE_KINDS:
            error(f"{path}.kind", f"unknown evidence kind {source.get('kind')!r}")
        if source.get("status") not in CLAIM_STATUSES:
            error(f"{path}.status", f"unknown claim status {source.get('status')!r}")


def _validate_help(help_value, source_ids, error) -> None:
    if help_value is None:
        return
    if not isinstance(help_value, dict):
        error("$.help", "must be an object")
        return
    for key in ("title", "buttonLabel", "html"):
        _require_string(help_value, key, "$.help", error)
    _validate_evidence_refs(help_value, "$.help", source_ids, error)
    _validate_html_fields(help_value, "$.help", error)


def _validate_filter_tags(config, error) -> None:
    known_tags = {
        tag
        for item in config.get("filters", [])
        if isinstance(item, dict)
        for tag in item.get("tags", [])
        if isinstance(tag, str)
    }
    for collection in ("nodes", "edges", "groups"):
        for index, item in enumerate(config.get(collection, [])):
            if not isinstance(item, dict) or "filterTags" not in item:
                continue
            tags = item["filterTags"]
            path = f"$.{collection}[{index}].filterTags"
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                error(path, "must be an array of strings")
                continue
            for tag_index, tag in enumerate(tags):
                if tag not in known_tags:
                    error(f"{path}[{tag_index}]", f"references unknown filter tag {tag!r}")


def _validate_customer_data_keys(value: Any, path: str, error) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            child_path = f"{path}.{key}"
            if normalized in _CUSTOMER_DATA_KEYS:
                error(child_path, "customer row/value preview fields are not allowed")
            _validate_customer_data_keys(child, child_path, error)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_customer_data_keys(child, f"{path}[{index}]", error)


def _validate_script_termination(value: Any, path: str, error) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _validate_script_termination(child, f"{path}.{key}", error)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_script_termination(child, f"{path}[{index}]", error)
    elif isinstance(value, str) and re.search(r"</\s*script", value, re.IGNORECASE):
        error(path, "closing script sequences are not allowed in configuration strings")


def _validate_reader_content(config: dict[str, Any], outbound: Any, error) -> None:
    if outbound is not True:
        return
    fields: list[tuple[str, Any]] = []
    report = config.get("report", {})
    if isinstance(report, dict):
        for key, value in report.items():
            if isinstance(value, str) and key not in {"id", "storageNamespace"}:
                fields.append((f"$.report.{key}", value))
    for collection in ("layers", "groups", "endpoints", "nodes", "edges"):
        for index, item in enumerate(config.get(collection, [])):
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if isinstance(value, str) and (
                    key.endswith("Html") or key in {"label", "description", "technicalLabel"}
                ):
                    fields.append((f"$.{collection}[{index}].{key}", value))
            properties = item.get("properties")
            if isinstance(properties, dict):
                for key, value in properties.items():
                    if isinstance(value, str):
                        fields.append((f"$.{collection}[{index}].properties.{key}", value))
    help_value = config.get("help")
    if isinstance(help_value, dict):
        for key, value in help_value.items():
            if isinstance(value, str):
                fields.append((f"$.help.{key}", value))
    for path, value in fields:
        for pattern, label in _OUTBOUND_PATTERNS:
            if pattern.search(value):
                error(path, f"reader-facing content contains {label}")


def _warn_graph_quality(config, warning) -> None:
    nodes = {node["id"]: node for node in config.get("nodes", []) if isinstance(node, dict)}
    connected: set[str] = set()
    for edge in config.get("edges", []):
        if isinstance(edge, dict):
            connected.update((edge.get("from"), edge.get("to")))
    for index, node in enumerate(config.get("nodes", [])):
        if isinstance(node, dict) and node.get("id") not in connected:
            warning(f"$.nodes[{index}]", "node has no incoming or outgoing edge")

    for index, endpoint in enumerate(config.get("endpoints", [])):
        if not isinstance(endpoint, dict) or endpoint.get("nodeId") not in nodes:
            continue
        upstream = collect_upstream(config, endpoint["nodeId"])
        if len(upstream) == 1 and nodes[endpoint["nodeId"]].get("role") not in {"source", "dlo", "dmo"}:
            warning(f"$.endpoints[{index}].nodeId", "endpoint has no traversable upstream path")

    if len(nodes) > 100:
        warning("$.nodes", "graph has more than 100 nodes; hierarchical layout may be hard to read")
    if len(config.get("edges", [])) > 180:
        warning("$.edges", "graph has more than 180 edges; hierarchical layout may be hard to read")


def _validate_html_fields(item: dict[str, Any], path: str, error) -> None:
    for key, value in item.items():
        if not key.endswith("Html") and key != "html":
            continue
        field_path = f"{path}.{key}"
        if not isinstance(value, str):
            error(field_path, "must be a string")
            continue
        for violation in validate_safe_html(value):
            error(field_path, violation)


def _validate_claim_status(item: dict[str, Any], path: str, error) -> None:
    if "claimStatus" in item and item["claimStatus"] not in CLAIM_STATUSES:
        error(f"{path}.claimStatus", f"unknown claim status {item['claimStatus']!r}")


def _validate_display_properties(item: dict[str, Any], path: str, error) -> None:
    rows = item.get("displayProperties")
    if rows is None:
        return
    if not isinstance(rows, list):
        error(f"{path}.displayProperties", "must be an array")
        return
    for index, row in enumerate(rows):
        row_path = f"{path}.displayProperties[{index}]"
        if not isinstance(row, dict):
            error(row_path, "must be an object")
            continue
        _require_string(row, "label", row_path, error)
        if "value" not in row:
            error(f"{row_path}.value", "required field is missing")
        elif isinstance(row["value"], (dict, list)):
            error(f"{row_path}.value", "must be a display-ready scalar string or number")
        if "multiline" in row and not isinstance(row["multiline"], bool):
            error(f"{row_path}.multiline", "must be a boolean")


def _validate_evidence_refs(item, path, source_ids, error) -> None:
    _validate_ref_array(item, "evidenceIds", source_ids, "evidence", path, error, required=False)


def _validate_ref(item, key, known_ids, noun, path, error) -> None:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        error(f"{path}.{key}", "must be a non-empty string")
    elif value not in known_ids:
        error(f"{path}.{key}", f"references unknown {noun} {value!r}")


def _validate_ref_array(item, key, known_ids, noun, path, error, required) -> None:
    value = item.get(key)
    if value is None and not required:
        return
    if not isinstance(value, list) or (required and not value):
        qualifier = "non-empty " if required else ""
        error(f"{path}.{key}", f"must be a {qualifier}array")
        return
    for index, ref in enumerate(value):
        if not isinstance(ref, str) or ref not in known_ids:
            error(f"{path}.{key}[{index}]", f"references unknown {noun} {ref!r}")


def _require_string(item, key, path, error) -> None:
    if not isinstance(item.get(key), str) or not item[key].strip():
        error(f"{path}.{key}", "must be a non-empty string")


def _has_evidence_kind(item, evidence_kinds, kind) -> bool:
    return any(evidence_kinds.get(ref) == kind for ref in item.get("evidenceIds", []))


def format_issues(issues: Iterable[ValidationIssue]) -> str:
    return "\n".join(
        f"{issue.severity.upper()}: {issue.path}: {issue.message}" for issue in issues
    )
