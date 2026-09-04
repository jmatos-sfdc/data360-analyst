---
name: data360-setup
description: Set up the data360-analyst toolkit — install dependencies, register the MCP server, or add a new client. Use when user asks to "set up data360", "register MCP server", "add new client", or "install data360".
triggers:
  - "set up data360"
  - "setup data360"
  - "register MCP"
  - "add new client"
  - "install data360"
  - "add MCP server"
---

# Data360 Setup

## First-time install

```bash
cd ~/data360-analyst
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Authenticate to a Salesforce org

```bash
sf org login web --instance-url https://<instance>.my.salesforce.com --alias <alias>
```

## Register the MCP server

Register once per machine per org. This lets any MCP-aware AI client (Claude Code, Cursor, etc.) call the Data Cloud tools interactively.

```bash
claude mcp add data360 \
    ~/data360-analyst/.venv/bin/python \
    -m data360_analyst.mcp_server \
    --org <alias> \
    --data-dir ~/Projects/clients/<client>/Data360
```

`--data-dir` points at the client's `Data360/` folder (parent of `object-model/`) and is required for the lineage tools (`lineage_summary`, `get_upstream`, `get_downstream`, `shortest_path`, `find_orphans`). Other tools work without it. The flag and the legacy `D360_ANALYST_DATA_DIR` env var are equivalent — pass either.

The server shells out to the sf CLI on every call to get a fresh token (`sf org auth show-access-token` on sf CLI versions released after May 27, 2026; `sf org display` on older versions) — no refresh-token dance, reuses existing `sf` auth. Adds ~200-400ms per call.

For multi-org work, register multiple servers with different aliases and names:
```bash
claude mcp add data360-uat \
    ~/data360-analyst/.venv/bin/python \
    -m data360_analyst.mcp_server \
    --org <uat-alias> \
    --data-dir ~/Projects/clients/<client>/Data360
```

## Available MCP tools

Once registered, the AI client can call:

- **Object model:** `list_dmos`, `get_dmo`, `get_dmo_relationships`
- **DLOs & mappings:** `list_dlos`, `get_dlo`, `list_dlo_mappings`, `get_dlo_mapping`
- **Calculated Insights:** `list_cis`, `get_ci_metadata`, `get_ci_sql`, `get_ci_rows`
- **Data Transforms:** `list_transforms`, `get_transform`
- **DPE / Data Actions:** `list_dpes`, `get_dpe`
- **Streams, Segments, Activations:** `list_streams`, `list_segments`, `list_activations`
- **Identity Resolution:** `list_ir_rulesets`
- **SQL:** `run_sql`, `get_sql_rows`
- **Lineage** (require `--data-dir`): `lineage_summary`, `get_upstream`, `get_downstream`, `shortest_path`, `find_orphans`

All read-only — no create/update/delete tools are registered.

## Add a new client

1. Create the folder structure:
```bash
mkdir -p ~/Projects/clients/<client>/Data360/{queries,reports,transforms,segments}
```

2. Create `config.md` using an existing client's `config.md` as a template. Fill in: org alias, instance URL, known DMOs, join paths, CI inventory.

3. Run intake to generate the object model:
```bash
source ~/data360-analyst/.venv/bin/activate
data360 intake --org <alias> --output-dir ~/Projects/clients/<client>/Data360
```

## Containment rule

Per-client output goes under `~/Projects/clients/<client>/Data360/`. This repo stays tool-only — code + docs, no client data.
