---
name: data360-intake
description: Snapshot a Salesforce Data Cloud org to disk — exports every DMO, DLO, DLO→DMO mapping, CI, transform, segment, stream, and activation as YAML sidecars + raw SQL/JSON. Use when user asks to "run intake", "snapshot the org", "refresh intake", "generate object model", "export the data model", or "rebuild sidecars".
triggers:
  - "run intake"
  - "refresh intake"
  - "snapshot org"
  - "snapshot the org"
  - "generate object model"
  - "rebuild object model"
  - "export data model"
  - "run intake for"
  - "incremental intake"
  - "dry run intake"
---

# Data360 Intake — Org Snapshot

Snapshot a Data Cloud org to disk in one command. Produces a diffable, version-controllable, grep-able artifact set plus a full lineage graph.

## Prerequisites

- Salesforce CLI authenticated to the target org (`sf org login web --alias <alias>`)
- Virtualenv set up: `cd ~/data360-analyst && source .venv/bin/activate`

## Output location

If the user hasn't already specified `--output-dir`, ask where the snapshot should be written before running anything — don't assume a path. If the org is client work, suggest `~/Projects/clients/<client>/Data360` (the established convention — see this project's `CLAUDE.md`) as the default, but treat it as a suggestion, not a rule: non-client orgs (internal sandbox, a demo/synthetic org) have no `<client>` to hang it off, and the user may want it elsewhere.

## Run

```bash
source ~/data360-analyst/.venv/bin/activate
data360 intake --org <sf-alias> --output-dir ~/Projects/clients/<client>/Data360
```

Replace `<sf-alias>` with the Salesforce CLI org alias and `<client>` with the client folder name.

## Flags

| Flag | Purpose |
|---|---|
| `--org <alias>` | **Required.** sf CLI org alias. |
| `--output-dir <path>` | Required unless `--dry-run`. Per-client `Data360/` folder. |
| `--dry-run` | Hit the API and print counts; write nothing to disk. Useful for checking auth + sizing before a full pull. |
| `--incremental` | Skip rewriting sidecars whose content hash matches the prior run's `_manifest.yaml`. Cuts re-run time on large orgs. |
| `--skip-streams` | Skip stream metadata (large; rarely needed for SQL/CI work). |
| `--skip-reports` | Skip auto-running downstream reports (`ci_audit.py`, `dmo_graph.py`) after intake. |

## What it produces

```
<output-dir>/
├── object-model.md                 Narrative summary, human-readable
├── object-model/
│   ├── index.yaml                  Org rollup (alias, instanceUrl, counts)
│   ├── _manifest.yaml              Per-artifact content hashes (used by --incremental)
│   ├── dmos/<name>.yaml            Per-DMO schema + field list
│   ├── dlos/<name>.yaml            Per-DLO schema
│   ├── mappings/<name>.yaml        Per DLO→DMO mapping
│   ├── cis/<name>.yaml             Per-CI metadata (sqlPath → queries/<name>.sql)
│   ├── transforms/<name>.yaml      Per-transform metadata (definitionPath → transforms/<name>.json)
│   ├── streams/<name>.yaml         Per-stream config + mappings
│   ├── segments/<name>.yaml        Per-segment criteria
│   └── activations/<name>.yaml     Per-activation metadata
├── queries/<name>.sql              Per-CI SQL body (HTML-decoded `expression`)
└── transforms/<name>.json          Per-transform full definitions DAG (HTML-decoded)
```

`object-model.md` is the human deliverable. `object-model/*.yaml` is the machine-readable layer that downstream skills (`ci-audit`, `architecture`, `lineage`, `compare`, `client-report`, `html-export`) consume.

Note: `object-model/lineage.yaml` is **not** produced by intake — it's a separate step. Run
`/data360-lineage` (or `lineage_graph.py` directly) once intake is done if lineage tracing is needed.

## After running

- Verify counts in `index.yaml` match expectations
- Auto-runs `ci_audit.py` and `dmo_graph.py` unless `--skip-reports` is passed
- Run `/data360-ci-audit` or `/data360-architecture` for follow-up analysis
- Run `/data360-lineage` if lineage tracing (`get_upstream`/`get_downstream`) is needed
- Commit the output to version control for diffing across runs

## Containment rule

All output goes under `~/Projects/clients/<client>/Data360/`. Never write Data 360 artifacts to the client root or sibling folders.
