#!/usr/bin/env python3
"""Compare legacy hybrid provenance data with an extracted schema v1 config."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from data360_analyst.extract_legacy_provenance_config import extract_legacy_data
from data360_analyst.render_provenance_report import load_config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dpe", type=Path)
    args = parser.parse_args(argv)
    try:
        _, legacy = extract_legacy_data(args.legacy)
        config = load_config(args.config)
        expected = {
            "groups": len(legacy["groups"]),
            "endpoints": sum(len(group["measures"]) for group in legacy["groups"].values()),
            "nodes": len(legacy["nodes"]),
            "edges": len(legacy["edges"]),
            "layers": len({node["layer"] for node in legacy["nodes"]}),
        }
        actual = {name: len(config[name]) for name in expected}
        legacy_node_ids = {node["id"] for node in legacy["nodes"]}
        config_node_ids = {node["id"] for node in config["nodes"]}
        legacy_endpoint_ids = {
            endpoint["id"]
            for group in legacy["groups"].values()
            for endpoint in group["measures"]
        }
        config_endpoint_ids = {endpoint["id"] for endpoint in config["endpoints"]}
        sequences = sorted(
            node["properties"]["sequence"]
            for node in config["nodes"]
            if node["role"] == "writeback"
        )
        problems = []
        if actual != expected:
            problems.append(f"count mismatch expected={expected} actual={actual}")
        if legacy_node_ids != config_node_ids:
            problems.append("node ID mismatch")
        if legacy_endpoint_ids != config_endpoint_ids:
            problems.append("endpoint ID mismatch")
        if sequences != list(range(1, len(sequences) + 1)):
            problems.append(f"writeback sequences are not contiguous: {sequences}")
        if args.dpe:
            dpe_payload = json.loads(args.dpe.read_text())
            records = dpe_payload.get("result", dpe_payload).get("records", [])
            if not records:
                problems.append("DPE export contains no records")
            else:
                expected_writebacks = {
                    wb["writebackSequence"]: wb
                    for wb in records[0]["Metadata"].get("writebacks", [])
                }
                actual_writebacks = {
                    node["properties"]["sequence"]: node["properties"]
                    for node in config["nodes"]
                    if node["role"] == "writeback"
                }
                datasource_names = {
                    datasource.get("name"): datasource.get("sourceName")
                    for datasource in records[0]["Metadata"].get("datasources", [])
                }
                for sequence, wb in expected_writebacks.items():
                    props = actual_writebacks.get(sequence)
                    if not props:
                        problems.append(f"missing writeback sequence {sequence}")
                        continue
                    expected_mappings = [
                        {
                            "source": field.get("sourceFieldName"),
                            "target": field.get("targetFieldName"),
                            "parent": field.get("parentName"),
                            "relationship": field.get("relationshipName"),
                        }
                        for field in wb.get("fields", [])
                    ]
                    checks = {
                        "sourceName": datasource_names.get(wb["sourceName"])
                        or wb["sourceName"].removeprefix("CI_"),
                        "targetObject": wb["targetObjectName"],
                        "operation": wb["operationType"],
                        "upsertKey": wb["externalIdFieldName"],
                        "fieldMappings": expected_mappings,
                    }
                    for key, value in checks.items():
                        if props.get(key) != value:
                            problems.append(
                                f"writeback {sequence} {key} mismatch"
                            )
        summary = {
            "expected": expected,
            "actual": actual,
            "writebackSequences": sequences,
            "problems": problems,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 1 if problems else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
