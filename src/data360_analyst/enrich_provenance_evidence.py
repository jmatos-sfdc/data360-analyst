#!/usr/bin/env python3
"""Attach live DPE, CI, and sObject evidence to an extracted provenance config."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data360_analyst.provenance_config import format_issues, validate_config
from data360_analyst.render_provenance_report import load_config, normalize_config


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result", payload)
    return result.get("records", []) if isinstance(result, dict) else []


def _dpe_metadata(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    records = _records(payload)
    if not records:
        raise ValueError("DPE export contains no records")
    record = records[0]
    metadata = record.get("Metadata")
    if not isinstance(metadata, dict):
        raise ValueError("DPE export does not contain Metadata")
    return record, metadata


def _add_source(config, source):
    if not any(item["id"] == source["id"] for item in config.setdefault("sources", [])):
        config["sources"].append(source)


def _field_map(describe_payload):
    result = describe_payload.get("result", describe_payload)
    return {field["name"]: field for field in result.get("fields", [])}


def enrich(config, dpe_payload, describes, ci_sql_dir: Path | None):
    record, metadata = _dpe_metadata(dpe_payload)
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    dpe_name = record.get("DeveloperName") or record.get("FullName") or "DPE"
    dpe_id = "evidence-dpe-body"
    _add_source(config, {"id": dpe_id, "kind": "dpe_body", "artifact": dpe_name, "environment": config["report"]["environment"], "verifiedAt": timestamp, "status": "verified"})

    writebacks = {wb["writebackSequence"]: wb for wb in metadata.get("writebacks", [])}
    datasource_names = {
        datasource.get("name"): datasource.get("sourceName")
        for datasource in metadata.get("datasources", [])
    }
    for node in config["nodes"]:
        if node["role"] == "dpe":
            node["evidenceIds"] = [dpe_id]
            node.setdefault("properties", {}).update({"developerName": dpe_name, "status": record.get("Status", "Active"), "modifiedDate": record.get("LastModifiedDate", "")})
            _upsert_display(node, "Developer Name", dpe_name)
            _upsert_display(node, "Status", record.get("Status", "Active"))
            _upsert_display(node, "Modified Date", record.get("LastModifiedDate", ""))
        elif node["role"] == "writeback":
            sequence = node.get("properties", {}).get("sequence")
            wb = writebacks.get(sequence)
            if not wb:
                raise ValueError(f"no DPE writeback found for sequence {sequence}")
            node["evidenceIds"] = [dpe_id]
            mappings = [
                {
                    "source": field.get("sourceFieldName"),
                    "target": field.get("targetFieldName"),
                    "parent": field.get("parentName"),
                    "relationship": field.get("relationshipName"),
                }
                for field in wb.get("fields", [])
            ]
            source_name = datasource_names.get(wb["sourceName"]) or wb["sourceName"].removeprefix("CI_")
            node["properties"].update({"sourceName": source_name, "targetObject": wb["targetObjectName"], "operation": wb["operationType"], "upsertKey": wb["externalIdFieldName"], "mappedFieldCount": len(mappings), "fieldMappings": mappings})
            _upsert_display(node, "Source CI", source_name)
            _upsert_display(node, "Target", wb["targetObjectName"])
            _upsert_display(
                node,
                "Fields",
                "\n".join(
                    f"{mapping['source']} → {mapping['target']}"
                    + (
                        f" ({mapping['relationship']} → {mapping['parent']})"
                        if mapping["relationship"] or mapping["parent"]
                        else ""
                    )
                    for mapping in mappings
                ),
                multiline=True,
            )

    for path, payload in describes.items():
        object_name = payload.get("result", payload).get("name")
        if not object_name:
            continue
        evidence_id = "evidence-sobject-" + re.sub(r"[^a-z0-9]+", "-", object_name.lower()).strip("-")
        _add_source(config, {"id": evidence_id, "kind": "sobject_describe", "artifact": object_name, "environment": config["report"]["environment"], "verifiedAt": timestamp, "status": "verified"})
        fields = _field_map(payload)
        for node in config["nodes"]:
            if node["role"] not in {"target_object", "target_field", "derived_field"}:
                continue
            api_name = node.get("properties", {}).get("apiName") or node["label"].split(" ")[0]
            if api_name not in fields and node["role"] == "derived_field":
                candidates = [
                    name for name, field in fields.items()
                    if field.get("label") == node["label"]
                ]
                if len(candidates) == 1:
                    api_name = candidates[0]
            if api_name in fields:
                field = fields[api_name]
                node["evidenceIds"] = sorted(set(node.get("evidenceIds", []) + [evidence_id]))
                node.setdefault("properties", {}).update({"apiName": api_name, "dataType": field.get("type", ""), "externalId": field.get("externalId", False), "formula": field.get("calculatedFormula") or ""})
                _upsert_display(node, "API Name", api_name)
                _upsert_display(node, "Type", field.get("type", ""))
                if field.get("externalId"):
                    _upsert_display(node, "External ID", "Yes")
                if field.get("calculatedFormula"):
                    _upsert_display(node, "Formula", field["calculatedFormula"])
            elif object_name in node["label"]:
                node["evidenceIds"] = sorted(set(node.get("evidenceIds", []) + [evidence_id]))

    if ci_sql_dir:
        for node in config["nodes"]:
            if node["role"] != "ci":
                continue
            sql_path = ci_sql_dir / f"{node['label']}.sql"
            if not sql_path.exists():
                continue
            evidence_id = "evidence-ci-" + re.sub(r"[^a-z0-9]+", "-", node["label"].lower()).strip("-")
            _add_source(config, {"id": evidence_id, "kind": "ci_sql", "artifact": node["label"], "environment": config["report"]["environment"], "verifiedAt": timestamp, "status": "verified"})
            node["evidenceIds"] = sorted(set(node.get("evidenceIds", []) + [evidence_id]))
    for node in config["nodes"]:
        _sort_display_properties(node)
    return config


def _upsert_display(
    node: dict[str, Any], label: str, value: Any, multiline: bool = False
) -> None:
    rows = node.setdefault("displayProperties", [])
    rows[:] = [row for row in rows if row.get("label") != label]
    if value in (None, "", [], {}):
        return
    rows.append(
        {
            "label": label,
            "value": value,
            **({"multiline": True} if multiline else {}),
        }
    )


def _sort_display_properties(node: dict[str, Any]) -> None:
    preferred = {
        "writeback": ["Source CI", "Target", "Lanes", "Fields"],
        "dpe": ["Developer Name", "Status", "Modified Date", "Target", "Lanes"],
        "ci": ["Grain", "Lanes", "Fields"],
        "source": ["Grain", "Lanes", "Fields"],
        "dlo": ["Grain", "Lanes", "Fields"],
        "dmo": ["Grain", "Lanes", "Fields"],
        "target_object": ["API Name", "Type", "External ID", "Formula", "Lanes"],
        "target_field": ["API Name", "Type", "External ID", "Formula", "Lanes"],
        "derived_field": ["API Name", "Type", "External ID", "Formula", "Lanes"],
    }.get(node.get("role"), ["Lanes"])
    order = {label: index for index, label in enumerate(preferred)}
    node.get("displayProperties", []).sort(
        key=lambda row: (order.get(row.get("label"), len(order)), row.get("label", ""))
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dpe", required=True, type=Path)
    parser.add_argument("--describe", action="append", default=[], type=Path)
    parser.add_argument("--ci-sql-dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        dpe = json.loads(args.dpe.read_text())
        describes = {path: json.loads(path.read_text()) for path in args.describe}
        enriched = enrich(config, dpe, describes, args.ci_sql_dir)
        result = validate_config(enriched)
        if not result.valid:
            raise ValueError(format_issues(result.errors))
        args.output.write_text(normalize_config(enriched))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if result.warnings:
        print(format_issues(result.warnings), file=sys.stderr)
    print(f"WROTE: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
