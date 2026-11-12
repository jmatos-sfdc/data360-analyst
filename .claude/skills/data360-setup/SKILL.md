---
name: data360-setup
description: Set up the current folder as a Data 360 analysis project for one org — check auth, write the project CLAUDE.md and permissions, register the data360 MCP server for this folder, snapshot the org, and report what it found. Use when the user asks to "set up data360 analyst for this project", "set up data360", "start a Data 360 project", "add a new client", "register the MCP server", or "install data360".
triggers:
  - "set up data360"
  - "setup data360"
  - "set up data360 analyst"
  - "start a data 360 project"
  - "add new client"
  - "register MCP"
  - "install data360"
---

# Set up a Data 360 project

The project folder is the current working directory unless the user names another. One folder per org. For client
work the convention is `~/Projects/clients/<client>/Data360`; if the current folder is somewhere else and looks
unrelated (a home directory, a repo of other code), ask before writing into it.

You need the **org alias**. If the user didn't give one, run `sf org list --json` and ask which alias to use. Never
log in for them; if the org isn't authenticated, tell them to run `! sf org login web --alias <alias>` and stop.

## 1. Check the toolkit and auth

```bash
data360 doctor --org <alias>
```

- `data360: command not found`: the toolkit isn't installed. Tell the user to clone
  `https://github.com/jmatos-sfdc/data360-analyst` and run its `setup.sh` (which also links these skills), then stop.
- Any FAIL line on auth or the Data Cloud API: show it and stop. Don't continue on a broken org connection.

## 2. Write the project files and register the MCP server

```bash
data360 init-project . --org <alias>
```

This writes `CLAUDE.md` (where the snapshot lives, answer from the snapshot first, go live only when asked) and
`.claude/settings.json` (pre-approves `data360` commands and the MCP tools, blocks hand edits to `object-model/`), and
registers the `data360` MCP server for this folder only (`claude mcp add --scope local`, with `--data-dir` set to this
folder so the lineage tools work). Existing `CLAUDE.md` or settings files are kept; pass `--force` only if the user
asks to overwrite them. If the folder already has a `CLAUDE.md` of its own, offer to append the Data 360 section rather
than replacing it.

## 3. Snapshot the org

```bash
data360 intake --org <alias> --output-dir .
```

Takes a few minutes on a small org (streams and lineage are the slow part); tell the user that before starting. Add
`--incremental` on a re-run. `--skip-streams` makes it faster at the cost of stream lineage.

## 4. Run the static checks and report

Intake already ran the CI audit. Add the complexity scores, the lineage graph (the dashboard's Lineage tab and the
MCP lineage tools read `object-model/lineage.yaml`), and the dashboard, then the answers:

```bash
data360 ci-complexity --output-dir .
data360 lineage-graph --output-dir .
data360 dashboard --data-dir .
data360 analyze --snapshot .
```

Report back in a few lines, not a dump:

- Counts from `object-model/index.yaml` (DMOs, CIs, segments, activations, IR rulesets).
- The top two or three findings from `analyze` (suspect CIs, over-merging match rules, orphans), each in one line.
- Where things are: `reports/dashboard.html`, `reports/ci-audit.md`.
- **The MCP server loads on the next Claude Code start.** Questions answered from the snapshot work now; live
  questions (row counts, `run_sql`, job status) need a restart (`/exit`, then `claude` in this folder).

## MCP tools (after restart)

All read-only. Object model (`list_dmos`, `get_dmo`, `get_dmo_relationships`), DLOs and mappings, Calculated Insights
(`list_cis`, `get_ci_sql`, `get_ci_rows`), transforms, DPEs, streams, segments, activations, `list_ir_rulesets`, SQL
(`run_sql`, `get_sql_rows`; counts and totals only, record-level queries come back as Query Editor SQL), and lineage (`lineage_summary`, `get_upstream`, `get_downstream`, `shortest_path`,
`find_orphans`). A second org gets its own project folder; don't register two orgs in one folder.

## Containment rule

Everything for this org stays in this project folder. Never write snapshot output into the toolkit repo.
