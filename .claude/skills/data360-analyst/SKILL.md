---
name: data360-analyst
description: Analyze and document a Salesforce Data Cloud (Data 360) org — object model, CI SQL review, Data Transform audit, segment analysis, and architectural findings. Use when user asks to "analyze Data 360 for <client>", "start a Data 360 session", "review CI SQL", "audit data transforms", or "explore the data model".
triggers:
  - "analyze data 360"
  - "start data 360 session"
  - "review CI SQL"
  - "audit data transforms"
  - "explore data model"
  - "decode a segment"
  - "data 360 session for"
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

With the MCP server running, use the tools to answer the user's questions:

- **Object model:** `list_dmos` → `get_dmo` for field details → `get_dmo_relationships` for join paths
- **CI review:** `list_cis` → pull SQL from `expression` field → decode HTML entities → analyze joins, filters, aggregations
- **Data Transforms:** `list_transforms` → `get_transform` for the node DAG → decode `formulaExpression` fields
- **Live SQL:** `run_sql` for ad-hoc queries against DMOs and CIs → `get_sql_rows` for results
- **Segments:** `list_segments` for criteria trees
- **Streams:** `list_streams` for connector mappings and row counts

### 4. Run static analysis

Use `/data360-ci-audit` for batch SQL checks. For architecture ranking:

```bash
python ~/Projects/salesforce/data360-analyst/dmo_graph.py --org <alias> --output-dir ~/Projects/clients/<client>/Data360
python ~/Projects/salesforce/data360-analyst/cluster_cis_by_dmo.py --dmo <dmo_name> --output-dir ~/Projects/clients/<client>/Data360
```

### 5. Document findings

Write findings to `~/Projects/clients/<client>/Data360/reports/`. Use markdown. Each report should be self-contained and client-shippable.

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
- SQL queries (async): `POST /ssot/query-sql` → `GET /ssot/query-sql/:id/rows`

## Containment rule

All client output stays under `~/Projects/clients/<client>/Data360/`. This repo stays tool-only.
