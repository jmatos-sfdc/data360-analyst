---
name: data360-intake
description: Snapshot a Salesforce Data Cloud org to disk — exports every DMO, CI, transform, segment, stream, and activation as YAML sidecars + raw SQL/JSON. Use when user asks to "run intake", "snapshot the org", "generate object model", or "export the data model".
triggers:
  - "run intake"
  - "snapshot org"
  - "generate object model"
  - "export data model"
  - "run intake for"
---

# Data360 Intake — Org Snapshot

Snapshot a Data Cloud org to disk in one command. Produces a diffable, version-controllable, grep-able artifact set.

## Prerequisites

- Salesforce CLI authenticated to the target org (`sf org login web --alias <alias>`)
- Virtualenv set up: `cd ~/Projects/salesforce/data360-analyst && source .venv/bin/activate`

## Run

```bash
source ~/Projects/salesforce/data360-analyst/.venv/bin/activate
python ~/Projects/salesforce/data360-analyst/intake.py --org <sf-alias> --output-dir ~/Projects/clients/<client>/Data360
```

Replace `<sf-alias>` with the Salesforce CLI org alias and `<client>` with the client folder name.

## What it produces

```
<output-dir>/
├── object-model.md              Narrative summary, human-readable
├── object-model/
│   ├── index.yaml               Org rollup (alias, instanceUrl, counts)
│   ├── dmos/<name>.yaml         Per-DMO schema + field list
│   ├── cis/<name>.yaml          Per-CI metadata + SQL expression
│   ├── transforms/<name>.yaml   Per-transform DAG definition
│   ├── streams/<name>.yaml      Per-stream config + mappings
│   └── segments/<name>.yaml     Per-segment criteria
├── queries/<name>.sql           Per-CI SQL body (HTML-decoded)
└── transforms/<name>.json       Per-transform full definitions DAG
```

`object-model.md` is the human deliverable. `object-model/*.yaml` is for diffing runs and feeding downstream tools.

## After running

- Verify row counts in `index.yaml` match expectations
- Run `ci_audit.py` next to check CI SQL for known traps
- Commit the output to version control for diffing across runs

## Containment rule

All output goes under `~/Projects/clients/<client>/Data360/`. Never write Data 360 artifacts to the client root or sibling folders.
