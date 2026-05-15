---
name: data360-architecture
description: Analyze Data Cloud architecture — rank DMOs by fan-in, cluster CIs by join shape, cross-check Lucidchart diagrams against the org inventory. Use when user asks to "find backbone DMOs", "rank DMOs", "what CIs use this DMO", "cross-check diagram", or "architecture review".
triggers:
  - "find backbone DMOs"
  - "rank DMOs"
  - "what CIs use"
  - "cross-check diagram"
  - "architecture review"
  - "which DMOs matter"
  - "DMO fan-in"
---

# Data360 Architecture Analysis

Understand the structure of a Data Cloud org — which objects are load-bearing, how CIs cluster, and whether the canonical diagram matches reality.

## Prerequisites

- Intake must have been run first (needs `object-model/` YAML sidecars on disk)
- MCP server registered for live queries (optional but recommended for ad-hoc follow-up)
- Virtualenv activated: `source ~/Projects/salesforce/data360-analyst/.venv/bin/activate`

## Tools

### 1. DMO Fan-In Ranking (`dmo_graph.py`)

Ranks DMOs by how many CIs and segments read from them. Identifies backbone DMOs (high fan-in, many downstream consumers) vs leaf detail objects (used by 1-2 CIs).

```bash
~/Projects/salesforce/data360-analyst/.venv/bin/python \
  ~/Projects/salesforce/data360-analyst/dmo_graph.py \
  --org <alias> \
  --output-dir ~/Projects/clients/<Client>/Data360
```

Writes `reports/dmo-graph.md`. Key output:
- Top-N DMOs by CI edge count
- Dimensional vs transactional classification
- Orphan DMOs (no CI reads from them)

**When to use:** First thing when inheriting an unfamiliar org. Answers "which 5 DMOs should I understand first?"

### 2. CI Clustering per DMO (`cluster_cis_by_dmo.py`)

Zooms into one DMO and clusters all CIs that read from it by naming pattern, join shape, and output measures.

```bash
~/Projects/salesforce/data360-analyst/.venv/bin/python \
  ~/Projects/salesforce/data360-analyst/cluster_cis_by_dmo.py \
  --dmo <dmo_api_name> \
  --output-dir ~/Projects/clients/<Client>/Data360
```

Writes `reports/cis-on-<dmo>.md`. Key output:
- CIs grouped by naming prefix (e.g., `Monthly_Revenue_*`, `Churn_Risk_*`)
- Join path comparison across CIs in the same cluster
- Divergent WHERE clauses that might indicate copy-paste drift

**When to use:** After `dmo_graph.py` identifies a backbone DMO. Answers "what's been built on top of this DMO and is it consistent?"

### 3. Diagram Cross-Check (`diagram_crosscheck.py`)

Matches a Lucidchart "canonical pipeline" diagram (Document JSON export) against the on-disk inventory. Flags orphans on both sides.

```bash
~/Projects/salesforce/data360-analyst/.venv/bin/python \
  ~/Projects/salesforce/data360-analyst/diagram_crosscheck.py \
  --diagram /path/to/diagram.json \
  --output-dir ~/Projects/clients/<Client>/Data360
```

Writes `reports/diagram-crosscheck.md`. Key output:
- Items in diagram but NOT on disk (broken references, renamed objects)
- Items on disk but NOT in diagram (undocumented CIs, dev-trail clutter)
- Match confidence scores

**When to use:** When a team says "here's our architecture diagram" and you want to verify it's current.

**Limitation:** Name-matching is heuristic. Handles `__cio`/`__dlm` suffixes and `CI:`/`DMO:` prefixes, but abbreviation-style aliases (e.g., `CustLTV` ↔ `Customer_Lifetime_Value`) are NOT handled and produce false positives. Treat "not in diagram" as a starting point, not authoritative.

## Common architectural findings

- **CIs bypassing a master transform** — duplicated join logic across CIs that should share a common source
- **Multiple join paths to the same entity** — e.g., two different ways to reach Account across CIs in the same org
- **Naming divergence** — developer-initial prefixes, `POC_*`, `Test_*`, `*_v2`, `*_old` mixed with production CIs
- **Orphan DMOs** — mapped and ingesting data but no CI or segment consumes them
- **DPE-managed transforms** — `DPE_*` prefixed transforms are Auto Cloud package-managed, excluded from audits by default

## Output

All reports go to `~/Projects/clients/<Client>/Data360/reports/`. One markdown file per analysis run.
