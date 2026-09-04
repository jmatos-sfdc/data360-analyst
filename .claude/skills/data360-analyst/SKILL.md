---
name: data360-analyst
description: Orchestrate a full Salesforce Data Cloud (Data 360) org analysis — runs intake, CI audit, architecture analysis, and assembles findings. Use when the user asks for a holistic Data 360 review (not a single specialist task — for those, use the targeted skills like data360-ci-audit, data360-transform-audit, data360-segment-decode).
triggers:
  - "analyze data 360"
  - "start data 360 session"
  - "data 360 session for"
  - "data 360 review for"
  - "full data 360 analysis"
  - "analyze the org"
  - "explore the data model"
---

# Data360 Analyst — Full Org Analysis

Master workflow for analyzing a Data Cloud org. Orchestrates intake, CI audit, architecture analysis, and ad-hoc exploration into a documented set of findings.

## Workflow

### 1. Verify access

Confirm the org alias is authenticated and the MCP server is registered:
```bash
sf org display --target-org <alias> --json
```

If the MCP server isn't registered, use `/data360-setup` first.

### 2. Snapshot the org (if not already done)

Use `/data360-intake` to export the full org to disk. Skip if intake was run recently and no major changes are expected.

### 3. Explore interactively

Answer from the local intake snapshot first (`object-model/` sidecars, `queries/*.sql`, `lineage.yaml`). Tell the user the answer is from the on-disk snapshot, then ask whether they want a live search via the MCP tools to confirm or refresh — don't run it unprompted. Go live without asking only when the user asks for current data, the artifact isn't in the snapshot, or the question is inherently live (row counts, `run_sql`, job/stream status).

When answering from the snapshot doesn't apply, use the MCP tools:

- **Object model:** `list_dmos` → `get_dmo` for field details → `get_dmo_relationships` for join paths
- **CI review:** `list_cis` → pull SQL from `expression` field → decode HTML entities → analyze joins, filters, aggregations
- **Data Transforms:** `list_transforms` → `get_transform` for the node DAG → decode `formulaExpression` fields
- **DPEs:** `list_dpes` → `get_dpe` for source CIs, writeback sequence, target objects, upsert keys, relationships, and exact field mappings
- **Live SQL:** `run_sql` for ad-hoc queries against DMOs and CIs → `get_sql_rows` for results
- **Segments:** `list_segments` for criteria trees
- **Streams:** `list_streams` for connector mappings and row counts

### 4. Run static analysis

Use `/data360-ci-audit` for batch SQL checks. For architecture ranking:

```bash
data360 dmo-graph --org <alias> --output-dir ~/Projects/clients/<client>/Data360
data360 cluster-cis --dmo <dmo_name> --output-dir ~/Projects/clients/<client>/Data360
```

### 5. Document findings

Write findings to `~/Projects/clients/<client>/Data360/reports/`. Use markdown. Each report should be self-contained and client-shippable.

When the user needs an interactive explanation of one DPE's end-to-end writeback
chain, use `/data360-provenance-report`. Keep the generated HTML and normalized
config under the client's `Data360/reports/<slug>-provenance/` directory.

## Key Data Cloud concepts

- DMOs use the `ssot__` namespace (e.g. `ssot__Individual__dlm`, `ssot__Account__dlm`)
- Custom DMOs use client-defined names (e.g. `Purchase_History__dlm`, `Store_Location__dlm`)
- CI output objects use the `__cio` suffix (e.g. `Customer_Lifetime_Value__cio`)
- Segments are typically defined on `ssot__Individual__dlm` as the root object

## SQL conventions (Query Editor and CI editor)

- Reference DMOs by full qualified name — no aliases in CI SQL, no double-quoted table names
- CI editor does not support: `REPLACE`, `SPLIT_PART`, `COUNT(DISTINCT ...)` — use `REGEXP_REPLACE` and `APPROX_COUNT_DISTINCT` instead
- Date functions: `MONTH()`, `DAY()`, `YEAR()`, `CURRENT_DATE()`, `DATE_TRUNC()`, `DATE_ADD()`
- String comparisons are case-sensitive
- JOINs follow the DMO relationship paths defined in the data model

## API reference

- Minimum API version: **v64.0** for Data 360 Connect API endpoints
- CI SQL: returned in `expression` field on `/ssot/calculated-insights` (HTML-entity-encoded)
- Transform definitions: returned under `definitions[].nodes[].parameters` on `/ssot/data-transforms` (HTML-entity-encoded)
- DPE definitions: returned through Tooling API `BatchCalcJobDefinition`; use the live body rather than inferring mappings from names
- SQL queries (async): `POST /ssot/query-sql` → `GET /ssot/query-sql/:id/rows`

## Containment rule

All client output stays under `~/Projects/clients/<client>/Data360/`. This repo stays tool-only.
