"""Answer-first analysis over a Data 360 snapshot.

`data360 analyze <org>` (wired in cli.py) snapshots a live org — or reuses an
existing snapshot via --snapshot — and answers high-value questions in the
terminal instead of only writing report files. This module holds the analysis
logic; it reads the same on-disk snapshot the other tools produce.

Backbone / orphan / flow answers come from the in-memory lineage graph
(`lineage_graph.build_graph`); suspect CIs come from `ci_audit`. `dmo_graph` is
deliberately not used here — it fetches CIs live from the org and can't run
against an offline snapshot, so backbone DMOs are ranked by lineage fan-in
instead.
"""

from collections import defaultdict
from pathlib import Path

from data360_analyst import ci_audit, lineage_graph


# Finding keys in a ci_audit result that represent an actual problem (not
# informational context like row-number windows or CURRENT_DATE counts).
_PROBLEM_KEYS = (
    # correctness
    "single_day_trigger", "leap_year", "hardcoded_record_type_ids",
    # CI-editor compliance
    "unsupported_functions", "count_distinct", "count_star",
    "try_convert_currency_arity", "dlo_in_from", "dmo_table_aliases",
    "self_joins", "top_level_distinct", "top_level_order_by", "top_level_cte",
    "exists_subquery", "in_subquery_unaliased", "alias_equals_field_name",
    "concat_operator", "double_quoted_identifiers", "datediff_in_case",
    "avg_case_nesting", "case_mixed_types", "ntile_alias_reuse",
    "cdp_in_aggregate", "concat_aggregate_provenance",
    # redundancy
    "join_where_duplicate_filter", "repeated_derived_expressions",
    "cross_ci_redundant_filter",
)


def _adjacency(graph):
    """out_adj[name] -> list of edges, in_adj[name] -> list of edges."""
    out_adj = defaultdict(list)
    in_adj = defaultdict(list)
    for e in graph.get("edges", []):
        out_adj[e["from"]].append(e)
        in_adj[e["to"]].append(e)
    return out_adj, in_adj


def backbone_dmos(graph, top=5):
    """DMOs ranked by fan-in — how many downstream artifacts read them. In the
    lineage graph a DMO→CI `read_by` edge is outgoing from the DMO, so a DMO's
    out-degree is its consumer count. High fan-in = load-bearing DMO."""
    out_adj, _ = _adjacency(graph)
    dmos = [n for n in graph.get("nodes", []) if n["type"] == "DMO"]
    ranked = []
    for n in dmos:
        consumers = [e["to"] for e in out_adj.get(n["name"], [])]
        ranked.append({
            "name": n["name"],
            "label": n.get("label"),
            "consumerCount": len(consumers),
            "consumers": sorted(consumers)[:8],
        })
    ranked.sort(key=lambda d: -d["consumerCount"])
    return ranked[:top]


# What node types can legitimately consume a given type's output. A downstream
# orphan ("nothing consumes it") is only meaningful when such a consumer type is
# actually present in the snapshot — otherwise every terminal node (e.g. every
# CI in a CI-only org) would be flagged, which is noise, not a finding.
_DOWNSTREAM_CONSUMERS = {
    "DMO": ("CI", "Segment", "Activation"),
    "Stream": ("DMO", "CI", "Segment", "Activation"),
    "CI": ("Segment", "Activation"),
    "Segment": ("Activation",),
    "Activation": (),  # terminal — never flagged for lacking downstream
}


def find_orphans(graph):
    """Nodes not connected the way you'd expect. Upstream orphans (a CI/Segment/
    Activation with no sources) always flag — that's misconfiguration. Downstream
    orphans only flag when the graph contains a type that could consume the node,
    so a CI-only snapshot doesn't report every CI as abandoned.

    Same intent as mcp_server.find_orphans, but consumer-aware to stay useful in
    partial snapshots."""
    out_adj, in_adj = _adjacency(graph)
    present = {n["type"] for n in graph.get("nodes", [])}
    orphans = []
    for n in graph.get("nodes", []):
        name, ntype = n["name"], n["type"]
        outgoing, incoming = len(out_adj.get(name, [])), len(in_adj.get(name, []))
        reasons = []
        if ntype in ("CI", "Segment", "Activation") and incoming == 0:
            reasons.append("no upstream sources")
        consumers = _DOWNSTREAM_CONSUMERS.get(ntype, ())
        if outgoing == 0 and any(c in present for c in consumers):
            reasons.append("nothing downstream consumes it")
        if reasons:
            orphans.append({"name": name, "type": ntype, "reasons": reasons})
    return orphans


def audit_snapshot(data_dir):
    """Run the CI SQL audit over <data_dir>/queries and return the raw results
    dict (filename -> findings), matching ci_audit.main's 3-pass driver."""
    q_dir = Path(data_dir).expanduser() / "queries"
    if not q_dir.is_dir():
        return {}
    parsed, raw_sql, parse_errors = {}, {}, {}
    for sql in sorted(q_dir.glob("*.sql")):
        trees, raw, err = ci_audit.parse_file(sql)
        raw_sql[sql.name] = raw
        if err is not None:
            parse_errors[sql.name] = err
        else:
            parsed[sql.name] = trees
    ci_filter_index = ci_audit.build_ci_filter_index(parsed)
    results = {name: {"parse_error": err} for name, err in parse_errors.items()}
    for name, trees in parsed.items():
        results[name] = ci_audit.audit_file(trees, raw_sql[name], ci_filter_index=ci_filter_index)
    return results


def suspect_cis(audit_results, top=5):
    """Rank CI SQL files by how many distinct problem findings fired."""
    ranked = []
    for name, f in audit_results.items():
        if "parse_error" in f:
            ranked.append({"name": name, "problemCount": 1,
                           "problems": [f"parse error: {f['parse_error']}"]})
            continue
        problems = [k for k in _PROBLEM_KEYS if f.get(k)]
        if f.get("hard_limits"):
            problems.append("hard_limits")
        if problems:
            ranked.append({"name": name, "problemCount": len(problems),
                           "problems": problems})
    ranked.sort(key=lambda d: -d["problemCount"])
    return ranked[:top]


def shortest_path(graph, from_name, to_name):
    """BFS over directed edges. Returns the node chain or None if disconnected."""
    out_adj, _ = _adjacency(graph)
    names = {n["name"] for n in graph.get("nodes", [])}
    if from_name not in names or to_name not in names:
        return None
    parent = {from_name: None}
    queue = [from_name]
    while queue:
        node = queue.pop(0)
        if node == to_name:
            break
        for e in out_adj.get(node, []):
            if e["to"] not in parent:
                parent[e["to"]] = node
                queue.append(e["to"])
    if to_name not in parent:
        return None
    chain, cur = [], to_name
    while cur is not None:
        chain.append(cur)
        cur = parent.get(cur)
    chain.reverse()
    return chain


def flow_to_activations(graph):
    """For each Activation, the shortest path from any Stream (else DMO) into
    it — 'how does data reach this activation?'. Empty when the snapshot has no
    activations (common for CI-only orgs)."""
    activations = [n["name"] for n in graph.get("nodes", []) if n["type"] == "Activation"]
    sources = [n["name"] for n in graph.get("nodes", []) if n["type"] == "Stream"] \
        or [n["name"] for n in graph.get("nodes", []) if n["type"] == "DMO"]
    flows = []
    for act in activations:
        best = None
        for src in sources:
            path = shortest_path(graph, src, act)
            if path and (best is None or len(path) < len(best)):
                best = path
        flows.append({"activation": act, "path": best})
    return flows


def analyze_snapshot(data_dir):
    """Build the full set of answers from a snapshot directory."""
    graph = lineage_graph.build_graph(data_dir)
    audit_results = audit_snapshot(data_dir)
    return {
        "dataDir": str(data_dir),
        "counts": graph.get("counts", {}),
        "backboneDmos": backbone_dmos(graph),
        "orphans": find_orphans(graph),
        "suspectCis": suspect_cis(audit_results),
        "flows": flow_to_activations(graph),
    }


# ── Rendering ────────────────────────────────────────────────────────────────

def _render_backbone(answers):
    lines = ["## Backbone DMOs (most-read)"]
    rows = answers["backboneDmos"]
    if not rows:
        return lines + ["- none found", ""]
    for d in rows:
        label = f" ({d['label']})" if d.get("label") else ""
        lines.append(f"- {d['name']}{label} — read by {d['consumerCount']} artifact(s)")
    lines.append("")
    return lines


def _render_orphans(answers):
    lines = ["## Orphans (disconnected / suspect wiring)"]
    rows = answers["orphans"]
    if not rows:
        return lines + ["- none — every node is connected", ""]
    for o in rows:
        lines.append(f"- {o['name']} ({o['type']}) — {'; '.join(o['reasons'])}")
    lines.append("")
    return lines


def _render_suspects(answers):
    lines = ["## Suspect CIs (most audit findings)"]
    rows = answers["suspectCis"]
    if not rows:
        return lines + ["- none — no CI SQL problems detected", ""]
    for c in rows:
        lines.append(f"- {c['name']} — {c['problemCount']} finding(s): {', '.join(c['problems'])}")
    lines.append("")
    return lines


def _render_flows(answers):
    lines = ["## Flow to activation"]
    rows = answers["flows"]
    if not rows:
        return lines + ["- no activations in this snapshot", ""]
    for fl in rows:
        if fl["path"]:
            lines.append(f"- {fl['activation']}: {' -> '.join(fl['path'])}")
        else:
            lines.append(f"- {fl['activation']}: no traced source path")
    lines.append("")
    return lines


# Intent map for --ask: substrings -> renderer. First match wins; order matters.
_INTENTS = [
    (("backbone", "important", "matter", "central", "core dmo", "most read", "most-read"), _render_backbone),
    (("orphan", "unused", "abandoned", "disconnected", "dead"), _render_orphans),
    (("suspect", "audit", "problem", "bug", "risky", "worst ci", "bad ci"), _render_suspects),
    (("flow", "activation", "reach", "path", "downstream to"), _render_flows),
]


def render_answers(answers, question=None):
    """Full report, or — when `question` is given — just the matched section."""
    header = [f"# Data 360 analysis — {answers['dataDir']}",
              f"_{_counts_line(answers['counts'])}_", ""]
    if question:
        section = _match_intent(question)
        if section is None:
            body = ["No matching analysis for that question. Try keywords: "
                    "backbone, orphans, suspect CIs, flow to activation.", ""]
        else:
            body = section(answers)
        return "\n".join(header + body)
    body = (_render_backbone(answers) + _render_orphans(answers)
            + _render_suspects(answers) + _render_flows(answers))
    return "\n".join(header + body)


def _match_intent(question):
    q = question.lower()
    for keywords, renderer in _INTENTS:
        if any(k in q for k in keywords):
            return renderer
    return None


def _counts_line(counts):
    by_type = counts.get("byType", {})
    parts = ", ".join(f"{v} {k}" for k, v in sorted(by_type.items()))
    return f"{counts.get('nodes', 0)} nodes ({parts}), {counts.get('edges', 0)} edges"
