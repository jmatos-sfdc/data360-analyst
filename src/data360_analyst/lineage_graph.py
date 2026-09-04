#!/usr/bin/env python3
"""Build a lineage graph of a Salesforce Data 360 org.

Walks the YAML sidecars + raw CI SQL produced by intake.py and emits a single
`object-model/lineage.yaml` capturing every node (Stream / DLO / DMO / CI /
Segment / Activation) and every edge between them.

Edge relations:
    populates       Stream → DLO     (from stream sidecar `dataLakeObjectName`)
    maps_to         DLO → DMO        (from `mappings/<name>.yaml`)
    read_by         DMO → CI         (parsed from CI SQL FROM / JOIN)
    read_by         CI → CI          (CI joins another CI)
    criteria_uses   DMO/CI → Segment (decoded from segment criteria JSON)
    activates       Segment → Activation

`unresolved` records edges that *should* exist but couldn't be derived (e.g.
streams whose DLO-to-DMO link is API-invisible, activations pointing at a
segment that's been deleted). They surface in `find_orphans` and the lineage
report so the gap is explicit, not hidden.

Usage:
    python3 lineage_graph.py --output-dir <path-to-Data360-folder>
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install -r requirements.txt")
    sys.exit(1)


DMO_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__dlm)\b")
CIO_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__cio)\b")


def load_sidecar_dir(om_dir, subdir):
    """Yield (filename, doc) for every YAML under object-model/<subdir>/."""
    p = om_dir / subdir
    if not p.exists():
        return
    for f in sorted(p.glob("*.yaml")):
        try:
            yield f.name, yaml.safe_load(f.read_text()) or {}
        except yaml.YAMLError as e:
            print(f"  WARN: failed to parse {f}: {e}", file=sys.stderr)


def parse_ci_dependencies(queries_dir, ci_apinames):
    """For each CI's SQL file, extract referenced DMOs and other CIs.

    Returns `{ci_apiname: (dmos_referenced, cis_referenced)}`. CI references
    only count if they hit another CI we know about (filters out spurious
    `*__cio` substring matches in comments / column aliases).
    """
    deps = {}
    if not queries_dir.exists():
        return deps
    known_cis = {n.lower() for n in ci_apinames}
    for sql_file in sorted(queries_dir.glob("*.sql")):
        sql = sql_file.read_text()
        ci_name = sql_file.stem
        dmos = set(DMO_PATTERN.findall(sql))
        cis = {c for c in CIO_PATTERN.findall(sql) if c.lower() in known_cis}
        # Don't list a CI as referencing itself (its own apiName appears in
        # FROM<output> clauses on some orgs).
        cis.discard(ci_name)
        deps[ci_name] = (dmos, cis)
    return deps


def build_graph(output_dir):
    om = Path(output_dir).expanduser() / "object-model"
    queries = Path(output_dir).expanduser() / "queries"
    if not om.exists():
        raise FileNotFoundError(f"{om} not found — run intake.py first")

    nodes = []  # list of dicts (preserves insertion order for diff stability)
    edges = []
    unresolved = []
    seen_nodes = set()  # (name, type) tuples

    def add_node(name, type_, **extra):
        if not name or (name, type_) in seen_nodes:
            return
        seen_nodes.add((name, type_))
        nodes.append({"name": name, "type": type_, **{k: v for k, v in extra.items() if v is not None}})

    def add_edge(frm, to, relation, **extra):
        edges.append({"from": frm, "to": to, "relation": relation,
                      **{k: v for k, v in extra.items() if v is not None}})

    # ── Pass 1: load every artifact as a node ────────────────────────────────
    dmos = {}
    for fname, d in load_sidecar_dir(om, "dmos"):
        name = d.get("name")
        if not name:
            continue
        dmos[name] = d
        ns = name.split("__", 1)[0] if "__" in name else None
        add_node(name, "DMO", label=d.get("label"), namespace=ns, category=d.get("category"))

    cis = {}
    for fname, d in load_sidecar_dir(om, "cis"):
        name = d.get("apiName")
        if not name:
            continue
        cis[name] = d
        add_node(name, "CI", label=d.get("displayName"), status=d.get("status"))

    streams = {}
    for fname, d in load_sidecar_dir(om, "streams"):
        name = d.get("name")
        if not name:
            continue
        streams[name] = d
        add_node(name, "Stream", connector=d.get("connectorType"),
                 streamType=d.get("dataStreamType"), status=d.get("status"))

    dlos = {}
    for fname, d in load_sidecar_dir(om, "dlos"):
        name = d.get("name")
        if not name:
            continue
        dlos[name] = d
        add_node(name, "DLO", label=d.get("label"), category=d.get("category"))

    mappings = []
    for fname, d in load_sidecar_dir(om, "mappings"):
        if d:
            mappings.append(d)

    segments = {}
    for fname, d in load_sidecar_dir(om, "segments"):
        name = d.get("apiName")
        if not name:
            continue
        segments[name] = d
        add_node(name, "Segment", label=d.get("displayName"),
                 status=d.get("status"), rootObject=d.get("segmentOnApiName"))

    activations = {}
    for fname, d in load_sidecar_dir(om, "activations"):
        name = d.get("name")
        if not name:
            continue
        activations[name] = d
        add_node(name, "Activation", label=d.get("label"),
                 targetType=d.get("activationType"), status=d.get("status"))

    # ── Pass 2: edges ────────────────────────────────────────────────────────

    # Stream → DLO from the stream sidecar's `dataLakeObjectName`. If the field
    # is missing on the response, fall back to a name-prefix heuristic
    # (DLOs are often `<StreamName>__dll`).
    for sname, s in streams.items():
        dlo_name = s.get("dataLakeObjectName")
        if not dlo_name:
            # Heuristic match: DLO whose name starts with the stream name.
            candidates = [n for n in dlos if n.startswith(sname)]
            if len(candidates) == 1:
                dlo_name = candidates[0]
        if dlo_name and dlo_name in dlos:
            add_edge(sname, dlo_name, "populates")
        else:
            unresolved.append({
                "from": sname,
                "to": dlo_name or "<unknown>",
                "relation": "populates",
                "reason": "stream sidecar has no dataLakeObjectName and no unique DLO match",
            })

    # DLO → DMO from mappings/<name>.yaml.
    for m in mappings:
        dlo_ref = m.get("dloDeveloperName")
        dmo_ref = m.get("dmoDeveloperName")
        if not dlo_ref or not dmo_ref:
            continue
        if dlo_ref in dlos and dmo_ref in dmos:
            add_edge(dlo_ref, dmo_ref, "maps_to",
                     mapping=m.get("developerName"))
        else:
            unresolved.append({
                "from": dlo_ref,
                "to": dmo_ref,
                "relation": "maps_to",
                "reason": "mapping references DLO or DMO not in inventory",
            })

    # CI → DMO and CI → CI from parsed SQL.
    deps = parse_ci_dependencies(queries, cis.keys())
    for ci_name, (referenced_dmos, referenced_cis) in deps.items():
        if ci_name not in cis:
            # SQL on disk for a CI we don't have a sidecar for — record as a
            # synthetic CI node so the edges resolve.
            add_node(ci_name, "CI", note="sidecar missing")
        for dmo in sorted(referenced_dmos):
            if dmo in dmos:
                add_edge(dmo, ci_name, "read_by")
            else:
                unresolved.append({"from": dmo, "to": ci_name, "relation": "read_by",
                                   "reason": f"DMO {dmo} not in inventory"})
        for upstream_ci in sorted(referenced_cis):
            add_edge(upstream_ci, ci_name, "read_by")

    # DMO/CI → Segment via segmentOnApiName + criteriaObjects.
    for sname, seg in segments.items():
        root = seg.get("segmentOnApiName")
        if root:
            if root in dmos:
                add_edge(root, sname, "criteria_uses", role="root")
            elif root in cis:
                add_edge(root, sname, "criteria_uses", role="root")
            else:
                unresolved.append({"from": root, "to": sname, "relation": "criteria_uses",
                                   "reason": "segmentOnApiName not in inventory"})
        for obj in seg.get("criteriaObjects") or []:
            if obj == root:
                continue  # already captured as root
            if obj in dmos or obj in cis:
                add_edge(obj, sname, "criteria_uses")
            else:
                unresolved.append({"from": obj, "to": sname, "relation": "criteria_uses",
                                   "reason": "criteriaObjects entry not in inventory"})

    # Segment → Activation.
    for aname, act in activations.items():
        seg_ref = act.get("segmentApiName")
        if not seg_ref:
            continue
        if seg_ref in segments:
            add_edge(seg_ref, aname, "activates")
        else:
            unresolved.append({"from": seg_ref, "to": aname, "relation": "activates",
                               "reason": "activation references unknown segment"})

    # ── Summary stats ────────────────────────────────────────────────────────
    counts_by_type = {}
    for n in nodes:
        counts_by_type[n["type"]] = counts_by_type.get(n["type"], 0) + 1

    counts_by_relation = {}
    for e in edges:
        counts_by_relation[e["relation"]] = counts_by_relation.get(e["relation"], 0) + 1

    return {
        "version": 1,
        "nodes": nodes,
        "edges": edges,
        "unresolved": unresolved,
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
            "unresolved": len(unresolved),
            "byType": counts_by_type,
            "byRelation": counts_by_relation,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output-dir", required=True,
                        help="Path to the client's Data360 folder (must contain object-model/)")
    args = parser.parse_args()

    graph = build_graph(args.output_dir)

    out_path = Path(args.output_dir).expanduser() / "object-model" / "lineage.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.safe_dump(graph, sort_keys=False, allow_unicode=True))

    c = graph["counts"]
    print(f"Wrote {out_path}")
    print(f"  Nodes: {c['nodes']}  ({', '.join(f'{k}={v}' for k, v in sorted(c['byType'].items()))})")
    print(f"  Edges: {c['edges']}  ({', '.join(f'{k}={v}' for k, v in sorted(c['byRelation'].items()))})")
    print(f"  Unresolved: {c['unresolved']}")


if __name__ == "__main__":
    main()
