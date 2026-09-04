"""Tests for analyze.py — the answer-first layer (Phase A).

Exercises analyze_snapshot end-to-end against the committed demo-org snapshot
(offline, no live org) plus the intent router and consumer-aware orphan rule.
"""

from pathlib import Path

from data360_analyst import analyze


DEMO_ORG = str(Path(__file__).parent.parent / "examples" / "demo-org")


def test_analyze_snapshot_answers_all_sections():
    a = analyze.analyze_snapshot(DEMO_ORG)
    # 4/4 canned questions answerable from the demo snapshot.
    assert a["backboneDmos"]           # ranked DMOs
    assert a["suspectCis"]             # CI SQL with findings
    assert a["counts"]["nodes"] == 12
    assert "orphans" in a and "flows" in a


def test_backbone_ranked_by_fan_in():
    a = analyze.analyze_snapshot(DEMO_ORG)
    counts = [d["consumerCount"] for d in a["backboneDmos"]]
    assert counts == sorted(counts, reverse=True)
    # Account is the most-read DMO in the demo org.
    assert a["backboneDmos"][0]["name"] == "Account__dlm"


def test_suspect_cis_ranked_by_finding_count():
    a = analyze.analyze_snapshot(DEMO_ORG)
    top = a["suspectCis"][0]
    assert top["problemCount"] >= a["suspectCis"][-1]["problemCount"]
    assert top["problems"]


def test_orphans_empty_when_no_consumer_type_present():
    # Demo has only CIs + DMOs (no Segment/Activation), so no CI is a downstream
    # orphan and every CI/DMO is wired — the consumer-aware rule reports none.
    a = analyze.analyze_snapshot(DEMO_ORG)
    assert a["orphans"] == []


def test_orphan_flags_ci_with_no_upstream():
    graph = {
        "nodes": [{"name": "Lonely__cio", "type": "CI"}],
        "edges": [],
    }
    orphans = analyze.find_orphans(graph)
    assert orphans == [{"name": "Lonely__cio", "type": "CI",
                        "reasons": ["no upstream sources"]}]


def test_orphan_flags_downstream_only_when_consumer_present():
    # CI feeds nothing, but an Activation exists that could consume it → flagged.
    graph = {
        "nodes": [{"name": "C__cio", "type": "CI"},
                  {"name": "A", "type": "Activation"}],
        "edges": [{"from": "src", "to": "C__cio", "relation": "read_by"}],
    }
    reasons = analyze.find_orphans(graph)[0]["reasons"]
    assert "nothing downstream consumes it" in reasons


def test_flow_traces_stream_to_activation():
    graph = {
        "nodes": [{"name": "S", "type": "Stream"},
                  {"name": "D", "type": "DMO"},
                  {"name": "A", "type": "Activation"}],
        "edges": [{"from": "S", "to": "D", "relation": "feeds"},
                  {"from": "D", "to": "A", "relation": "feeds"}],
    }
    flows = analyze.flow_to_activations(graph)
    assert flows == [{"activation": "A", "path": ["S", "D", "A"]}]


def test_ask_routes_to_matching_section():
    a = analyze.analyze_snapshot(DEMO_ORG)
    out = analyze.render_answers(a, question="which DMOs are most important?")
    assert "Backbone DMOs" in out
    assert "Suspect CIs" not in out


def test_ask_unmatched_question_explains():
    a = analyze.analyze_snapshot(DEMO_ORG)
    out = analyze.render_answers(a, question="what is the weather")
    assert "No matching analysis" in out


def test_suspect_cis_reports_parse_error():
    ranked = analyze.suspect_cis({"Broken__cio.sql": {"parse_error": "boom"}})
    assert ranked[0]["name"] == "Broken__cio.sql"
    assert "parse error" in ranked[0]["problems"][0]
