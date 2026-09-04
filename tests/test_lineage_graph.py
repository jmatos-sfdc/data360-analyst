"""Tests for lineage_graph.py — builds the lineage graph from the committed
demo-org snapshot (no live org) and asserts its shape."""

from pathlib import Path

import pytest

from data360_analyst import lineage_graph


DEMO_ORG = Path(__file__).parent.parent / "examples" / "demo-org"


def test_build_graph_on_demo_org():
    graph = lineage_graph.build_graph(str(DEMO_ORG))
    counts = graph["counts"]
    assert counts["nodes"] == 12
    assert counts["byType"] == {"CI": 8, "DMO": 4}
    assert counts["unresolved"] == 0
    # Every edge in the demo is a CI reading a DMO.
    assert set(counts["byRelation"]) == {"read_by"}


def test_build_graph_edges_point_ci_to_dmo():
    graph = lineage_graph.build_graph(str(DEMO_ORG))
    node_type = {(n["name"], n["type"]): n for n in graph["nodes"]}
    dmo_names = {n["name"] for n in graph["nodes"] if n["type"] == "DMO"}
    ci_names = {n["name"] for n in graph["nodes"] if n["type"] == "CI"}
    for e in graph["edges"]:
        # read_by: DMO is read_by CI — source is a DMO, target is a CI.
        assert e["from"] in dmo_names
        assert e["to"] in ci_names


def test_build_graph_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        lineage_graph.build_graph(str(tmp_path / "nope"))


def test_parse_ci_dependencies_resolves_dmo_refs():
    queries = DEMO_ORG / "queries"
    ci_names = {p.stem for p in queries.glob("*.sql")}
    deps = lineage_graph.parse_ci_dependencies(queries, ci_names)
    assert deps
    # At least one CI query references a DMO from the snapshot.
    assert any(dmos for dmos, cis in deps.values())
