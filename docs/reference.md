# Reference

Deep reference material for `data360-analyst`. For the overview, value proposition, and getting-started path, see the [README](../README.md).

- [MCP tool catalog](#mcp-tool-catalog) — all 26 read-only tools
- [Using the CLI directly](#using-the-cli-directly) — every subcommand + a command cookbook
- [Output structure](#output-structure) — what intake writes to disk

## MCP tool catalog

The [Skills](../README.md#skills) are the high-level workflows. Underneath, the MCP server exposes 26 read-only tools that Claude calls directly. You normally won't invoke these by name — but if you're writing your own agent or want to check what's available:

| Group | Tools |
|---|---|
| **DMOs** | `list_dmos`, `get_dmo`, `get_dmo_relationships` |
| **DLOs** | `list_dlos`, `get_dlo`, `list_dlo_mappings`, `get_dlo_mapping` |
| **Calculated Insights** | `list_cis`, `get_ci_metadata`, `get_ci_sql`, `get_ci_rows` |
| **Data Transforms** | `list_transforms`, `get_transform` |
| **Data Processing Engine** | `list_dpes`, `get_dpe` |
| **Streams** | `list_streams` |
| **Segments** | `list_segments` |
| **Activations** | `list_activations` |
| **Identity Resolution** | `list_ir_rulesets` |
| **SQL** | `run_sql`, `get_sql_rows` |
| **Lineage** (snapshot-backed) | `get_upstream`, `get_downstream`, `find_orphans`, `shortest_path`, `lineage_summary` |

All tools are read-only — there are no `create_*`, `update_*`, or `delete_*` tools by design. The Data 360 SQL engine itself rejects anything other than `SELECT` on `run_sql`.

## Using the CLI directly

If you prefer running the tools yourself (for batch snapshots, CI pipelines, or non-Claude workflows) — no Claude, no MCP, just the `data360` command after `pip install -e .`.

Every subcommand and the job it does:

| Subcommand | Job | Needs a live org? |
|---|---|---|
| `demo` | Run the full pipeline on the bundled demo snapshot and open the dashboard | no |
| `analyze` | Snapshot an org and print the answers — backbone DMOs, orphans, suspect CIs, flow-to-activation — to the terminal | yes (or `--snapshot`) |
| `intake` | Snapshot a live org to disk (YAML sidecars + raw SQL) | yes |
| `ci-audit` | Audit every CI's SQL — correctness traps, editor compliance, redundancy | no |
| `ci-convert` | Rewrite Query Editor SQL into CI editor-compatible form | no |
| `ci-concordance` | Build a concept → DMO / field / example-CI index | no |
| `ci-visualize` | Render one CI's SQL as an annotated, clickable onboarding report | no |
| `dmo-graph` | Rank DMOs by fan-in (fetches CI SQL from the org) | yes |
| `lineage-graph` | Build the full Stream/DMO/CI/Segment/Activation graph | no |
| `cluster-cis` | Cluster the CIs built on one DMO by naming pattern and join shape | no |
| `diagram-crosscheck` | Match a Lucidchart diagram against the on-disk inventory | no |
| `dashboard` | Assemble reports + sidecars into a single-page HTML dashboard | no |
| `export-sql-csv` | Page a full SQL result to CSV past the display / response caps | yes |
| `mcp-server` | Run the local read-only MCP server | yes |
| `provenance-validate` | Structurally validate a provenance report config | no |
| `provenance-render` | Render a validated provenance config to interactive HTML | no |
| `provenance-extract-legacy` | Migrate an older hybrid-graph provenance report to config form | no |
| `provenance-enrich` | Add onboarding explanations / evidence to a provenance config | no |
| `provenance-compare` | Diff two provenance configs / reports | no |

The subcommands marked "no" read only from an on-disk snapshot (or a local file) and never touch an org — that's why `data360 demo` works with no auth.

```bash
source .venv/bin/activate

# Answer-first: snapshot an org and print the high-value answers to the terminal
data360 analyze <alias>

# Same, but keep the snapshot in the per-client folder and ask one question
data360 analyze <alias> --client <client> --ask "which DMOs matter most?"

# Offline — answer straight from an existing snapshot (or the bundled demo)
data360 analyze --snapshot examples/demo-org

# Export the whole org to disk
data360 intake --org <alias> --output-dir ~/data360/<client>

# Audit every CI's SQL
data360 ci-audit --output-dir ~/data360/<client>

# Audit + auto-fix in one shot (writes queries-converted/ + reports/ci-convert.md)
data360 ci-audit --output-dir ~/data360/<client> --fix

# Convert Query Editor SQL to CI editor-compatible form (single file)
data360 ci-convert --input query.sql --diff

# Or batch-convert a whole snapshot — writes queries-converted/ + reports/ci-convert.md
data360 ci-convert --output-dir ~/data360/<client>

# Rank DMOs by fan-in
data360 dmo-graph --org <alias> --output-dir ~/data360/<client>

# Build the full lineage graph (Stream→DMO→CI→Segment→Activation)
data360 lineage-graph --output-dir ~/data360/<client>

# Cross-check against a Lucidchart diagram
data360 diagram-crosscheck --diagram /path/to/diagram.json \
                            --output-dir ~/data360/<client>

# Generate the HTML dashboard
data360 dashboard --data-dir ~/data360/<client> --client "<client>"

# Render an annotated, clickable onboarding report for one CI
data360 ci-visualize --client-root ~/data360/<client> --name <CI_api_name>

# Render a DPE provenance report from a validated config
data360 provenance-validate <config.json>
data360 provenance-render --config <config.json> \
                           --output ~/data360/<client>/reports/<slug>-provenance/index.html

# Export a full SQL result set to CSV (past the row caps)
data360 export-sql-csv --org <alias> --sql query.sql --out result.csv
```

Every subcommand is also reachable as `python -m data360_analyst.<module>` if you're not using the installed console script.

## Output structure

```
<output-dir>/
├── object-model.md                Narrative summary, human-readable
├── object-model/
│   ├── index.yaml                 Org rollup (alias, instanceUrl, counts)
│   ├── dmos/<name>.yaml           Per-DMO schema
│   ├── dlos/<name>.yaml           Per-DLO schema
│   ├── mappings/<name>.yaml       Per DLO→DMO mapping
│   ├── cis/<name>.yaml            Per-CI metadata
│   ├── transforms/<name>.yaml     Per-transform metadata
│   ├── streams/<name>.yaml        Per-stream metadata
│   ├── segments/<name>.yaml       Per-segment metadata (incl. criteria DMOs)
│   ├── activations/<name>.yaml    Per-activation metadata
│   └── lineage.yaml               Full Stream/DMO/CI/Segment/Activation graph
├── queries/<name>.sql             Per-CI SQL body (HTML-decoded)
├── queries-converted/<name>.sql   Per-CI SQL after ci_convert.py mechanical pass
├── transforms/<name>.json         Per-transform definitions DAG (HTML-decoded)
└── reports/                       Audit findings + dashboard
    ├── dmo-graph.md
    ├── ci-audit.md
    ├── ci-convert.md
    ├── diagram-crosscheck.md
    ├── dashboard.html             Single-page tabbed dashboard with SVG charts
    ├── <ci-name>.html             Per-CI annotated onboarding report (ci_visualize.py)
    └── <dpe-slug>-provenance/     Interactive DPE provenance report
        ├── index.html
        └── provenance.json        Normalized config (maintained source of truth)
```
