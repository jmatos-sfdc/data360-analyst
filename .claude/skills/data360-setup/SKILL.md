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
cd ~/Projects/salesforce/data360-analyst
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Authenticate to a Salesforce org

```bash
sf org login web --instance-url https://<instance>.my.salesforce.com --alias <alias>
```

## Register the MCP server

Register once per machine per org. This lets any MCP-aware AI client (Claude Code, Cursor, etc.) call the Data Cloud tools interactively.

```bash
claude mcp add data360 \
    ~/Projects/salesforce/data360-analyst/.venv/bin/python \
    ~/Projects/salesforce/data360-analyst/mcp_server.py \
    --org <alias>
```

The server shells out to `sf org display --json` on every call to get a fresh token — no refresh-token dance, reuses existing `sf` auth. Adds ~200-400ms per call.

For multi-org work, register multiple servers with different aliases and names:
```bash
claude mcp add data360-uat \
    ~/Projects/salesforce/data360-analyst/.venv/bin/python \
    ~/Projects/salesforce/data360-analyst/mcp_server.py \
    --org <uat-alias>
```

## Available MCP tools

Once registered, the AI client can call:

- `list_dmos`, `get_dmo`, `get_dmo_relationships`
- `list_cis`, `get_ci_metadata`, `run_ci`
- `list_transforms`, `get_transform`
- `list_streams`
- `list_segments`, `list_activations`
- `list_ir_rulesets`
- `run_sql`, `get_sql_rows`

All read-only — no create/update/delete tools are registered.

## Add a new client

1. Create the folder structure:
```bash
mkdir -p ~/Projects/clients/<client>/Data360/{queries,reports,transforms,segments}
```

2. Create `config.md` using an existing client's `config.md` as a template. Fill in: org alias, instance URL, known DMOs, join paths, CI inventory.

3. Run intake to generate the object model:
```bash
source ~/Projects/salesforce/data360-analyst/.venv/bin/activate
python ~/Projects/salesforce/data360-analyst/intake.py --org <alias> --output-dir ~/Projects/clients/<client>/Data360
```

## Containment rule

Per-client output goes under `~/Projects/clients/<client>/Data360/`. This repo stays tool-only — code + docs, no client data.
