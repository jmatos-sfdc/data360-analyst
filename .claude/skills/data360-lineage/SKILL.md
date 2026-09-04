---
name: data360-lineage
description: Trace lineage across a Data Cloud org — what feeds a CI, what's downstream of a DMO, which CIs/segments are orphaned, and how data flows from a stream through to an activation. Use when user asks "what's upstream of X", "what's downstream of Y", "what feeds this CI", "what activations depend on this segment", "how does data get from X to Y", "find orphan CIs", "find unused DMOs", "what's abandoned", or "trace the lineage".
triggers:
  - "what's upstream of"
  - "what's downstream of"
  - "what feeds"
  - "what reads"
  - "what depends on"
  - "trace lineage"
  - "find orphan"
  - "find unused"
  - "what's abandoned"
  - "how does data flow"
  - "lineage graph"
---

# Data360 Lineage — Tracing Object Dependencies

The lineage tools answer reachability questions across the org's full graph: Stream → DLO → DMO → CI → Segment → Activation. The graph itself is built once by `lineage_graph.py` after intake; the MCP tools query it without making live API calls.

## Prerequisites

- `intake.py` has been run (sidecars on disk)
- `lineage_graph.py` has been run (writes `object-model/lineage.yaml`)
- The MCP server has `D360_ANALYST_DATA_DIR` set to the client's `Data360/` folder, or `--data-dir` was passed at launch

If `lineage.yaml` doesn't exist yet, run:

```bash
data360 lineage-graph \
    --output-dir ~/Projects/clients/<Client>/Data360
```

## MCP tools

| Tool | Use it for |
|---|---|
| `get_upstream(name, depth=3, edge_types=...)` | "What feeds X?" — returns ancestors |
| `get_downstream(name, depth=3, edge_types=...)` | "What does X affect?" — returns descendants |
| `find_orphans(node_type)` | "What's abandoned?" — surfaces dead-end CIs/segments/DMOs |
| `shortest_path(from_name, to_name)` | "How does data flow from A to B?" |
| `lineage_summary()` | Org rollup: counts, top fan-out, top fan-in, unresolved-edge count |

`edge_types` filters the walk to specific relations:
- `["read_by"]` — SQL lineage only (DMO→CI and CI→CI)
- `["criteria_uses"]` — segment criteria only
- `["activates"]` — segment→activation only
- `["populates", "maps_to"]` — ingestion path only

## Visualizing slices

For small subgraphs (~5–15 nodes), render a mermaid block. Conventions:
- `[(...)]` for DMOs, `[/.../]` for DLOs, `[...]` for CIs, `((...))` for streams, `>...]` for segments, `{{...}}` for activations
- Solid arrows for resolved edges, `-.->|reason|` for unresolved
- Always `flowchart LR`

For anything larger, return a table (rows = nodes, columns = type / inDegree / outDegree) instead. Don't try to mermaid 50+ nodes — it renders but no one can read it.

## Edge cases

- **Stream → DMO is always unresolved.** The DLO→DMO mapping isn't on the public `/ssot/*` API. The graph records this explicitly in the `unresolved` list. When asked about stream lineage, surface this gap rather than guessing.
- **`find_orphans("CI")` lists CIs with no inputs OR no consumers.** A CI with SQL but no segment/activation reading it is the abandoned-development pattern. A CI with no inputs likely has a parse error in its SQL — investigate before flagging as abandoned.
- **`shortest_path` is directed.** It walks `from → to` along the data-flow direction, so asking "path from activation to stream" returns null even if the reverse path exists.
- **Cycles in CI→CI joins** are valid (a CI reading two CIs that share a common upstream) but watch for true cycles that indicate misconfiguration. `lineage_summary` doesn't surface these explicitly today; if a downstream walk doesn't terminate, look for one.

## Common patterns

**"What does this CI depend on?"** → `get_upstream("My_CI__cio", depth=3, edge_types=["read_by"])`. Returns the DMOs and CIs in its SQL.

**"If I deprecate this DMO, what breaks?"** → `get_downstream("ssot__Account__dlm", depth=5)`. Walk the full graph; surface every CI, segment, and activation transitively affected.

**"Which CIs aren't actually used?"** → `find_orphans("CI")`, then narrow by `outDegree == 0` (CI exists but nothing reads its output).

**"How does data from this stream end up in Marketing Cloud?"** → `shortest_path("MyStream", "MC_Activation")`. May return null if the DLO→DMO gap blocks the walk; in that case explain the gap and walk from the DMO instead.

## After running

- Cross-reference orphan findings with `git log` on the org's CI definitions — a CI last modified 18+ months ago with no consumers is a cleanup candidate.
- Pair `find_orphans` with `ci_audit` findings: an orphan CI with a leap-year bug is lower priority than a heavily-consumed one.
- Lineage does not capture *intent*. Two CIs with identical join shapes but different names may be two attempts at the same logic — the lineage tool flags the duplication; the human decides which to keep.
