# Data360 Analyst

Reusable toolkit for analyzing Salesforce Data Cloud (Data 360) instances. Supports querying the object model, understanding DMO relationships, writing SQL for Calculated Insights and the Query Editor, and reviewing Data Transforms.

## Project Structure

```
data360-analyst/
├── CLAUDE.md              This file — project reference
├── .claude/skills/        Skill definitions (invoke with /<skill-name>)
│   ├── data360-analyst/   Full org analysis workflow
│   ├── data360-intake/    Snapshot org to disk
│   ├── data360-ci-audit/  CI SQL static analysis
│   ├── data360-ci-find/   Map business concept to DMOs, fields, example CIs
│   ├── data360-ci-author/ CI SQL authoring guide (editor constraints, patterns)
│   ├── data360-sql-convert/ Convert Query Editor SQL to CI editor SQL
│   ├── data360-architecture/ DMO ranking, CI clustering, diagram cross-check
│   ├── data360-compare/   Diff CIs, compare orgs
│   ├── data360-segment-decode/ Decode segment criteria, validate membership
│   ├── data360-lineage/      Trace upstream/downstream lineage across the graph
│   ├── data360-client-report/ Assemble findings into client deliverable
│   ├── data360-transform-audit/ Audit Data Transform DAGs
│   ├── data360-dpe-review/ Review DPE/Data Action configurations
│   ├── data360-provenance-report/ Build/validate interactive DPE provenance reports
│   ├── data360-html-export/ Convert markdown reports to self-contained HTML
│   └── data360-setup/     Install, register MCP, add client
├── pyproject.toml         Package metadata + `data360` console entry point
├── setup.sh               One-command install: create .venv, `pip install -e .`, print next step
├── src/data360_analyst/
│   ├── cli.py             `data360 <subcommand>` dispatcher + `data360 demo` (full offline pipeline on examples/demo-org) + `data360 analyze`
│   ├── analyze.py             Answer-first layer — backbone DMOs, orphans, suspect CIs, flow-to-activation from a snapshot; powers `data360 analyze`
│   ├── intake.py              Generates object-model.md + YAML sidecars from a live org
│   ├── ci_audit.py            sqlglot-based CI SQL audit (AST checks)
│   ├── ci_convert.py          Mechanical Query Editor → CI Editor SQL converter
│   ├── ci_concordance.py      Concept → DMO/field/example-CI index builder + proposer
│   ├── dmo_graph.py           DMO fan-in ranking + architecture analysis
│   ├── lineage_graph.py       Builds the full Stream/DMO/CI/Segment/Activation graph
│   ├── cluster_cis_by_dmo.py  CI clustering by join shape per DMO
│   ├── diagram_crosscheck.py  Lucidchart diagram vs org inventory cross-check
│   ├── dashboard.py           Single-page HTML dashboard from all reports + YAML sidecars
│   ├── ci_visualize.py        CI SQL → offline interactive onboarding report (semantic model + annotated SQL)
│   ├── provenance_config.py   Provenance report config schema (JSON/YAML source of truth)
│   ├── render_provenance_report.py  Renders a validated provenance config into the packaged interactive-graph shell
│   ├── validate_provenance_config.py  Structural validation of a provenance config before render
│   ├── extract_legacy_provenance_config.py  Migrates an older hybrid-graph provenance report into config form
│   ├── enrich_provenance_evidence.py  Adds onboarding explanations/evidence to a provenance config
│   ├── compare_provenance_reports.py  Diffs two provenance configs/reports
│   ├── export_sql_csv.py      Full DC SQL result → CSV, past the QE 1000 / ~90k single-response row caps (offset pagination)
│   ├── mcp_server.py          Local FastMCP server wrapping /ssot/* endpoints
│   └── sf_auth.py             Shared sf CLI auth helper (handles 2026-05-27 credential-redaction change)
├── requirements.txt       sqlglot, pyyaml, fastmcp, markdown (pinned; pyproject.toml is the install source of truth)
├── examples/demo-org/     Synthetic snapshot (fake org) for trying the toolkit without a live org
├── tests/                 pytest suite for the scripts above
└── .venv/                 Bundled virtualenv (not committed — run setup)
```

Every script above is reachable two ways: as a `data360 <subcommand>` (e.g. `data360 ci-audit --output-dir ...`)
after `pip install -e .`, or directly as `python -m data360_analyst.<module>`. Skills invoke the former.

**`data360 analyze`** is the answer-first entry point — it snapshots an org and prints the high-value
answers to the terminal instead of only writing report files:

- `data360 analyze <org>` — live intake into a temp workspace, then answers (no client folder needed)
- `data360 analyze <org> --client <name>` — live intake into `~/Projects/clients/<name>/Data360`
- `data360 analyze --snapshot <dir>` — offline; answers from an existing snapshot (e.g. `examples/demo-org`)
- add `--ask "<question>"` to print only the matching section (backbone DMOs, orphans, suspect CIs, flow-to-activation)

Per-client folder layout is opt-in via `--client`; without it, `analyze` writes to a throwaway workspace.
Backbone DMOs are ranked by lineage fan-in (not `dmo_graph`, which is live-only and can't run offline).

**Per-client output lives under `~/Projects/clients/<client>/Data360/`**, not inside this folder. Keep this folder tool-only (code + docs) so it can be packaged as a reusable skill.

## Skills

Use skills by name:

| Skill | Purpose |
|---|---|
| `/data360-analyst` | Full org analysis — orchestrates intake, audit, exploration, and documentation |
| `/data360-intake` | Snapshot a Data Cloud org to disk (YAML sidecars + raw SQL) |
| `/data360-ci-audit` | Audit CI SQL for known correctness traps |
| `/data360-ci-find` | Map a business concept to the DMOs, fields, and example CIs that answer it |
| `/data360-ci-author` | Write CI SQL — editor constraints, supported functions, RecordAlert patterns |
| `/data360-sql-convert` | Convert Query Editor SQL to CI editor-compatible SQL |
| `/data360-architecture` | DMO fan-in ranking, CI clustering, diagram cross-check |
| `/data360-compare` | Diff CIs side-by-side, compare DMO schemas across orgs |
| `/data360-segment-decode` | Decode segment criteria trees, validate membership |
| `/data360-lineage` | Trace upstream/downstream lineage across the graph |
| `/data360-client-report` | Assemble findings into a client-shippable deliverable |
| `/data360-transform-audit` | Audit Data Transform DAGs — dedup grain, formula patterns |
| `/data360-dpe-review` | Review DPE/Data Action configurations — field mappings, upsert keys |
| `/data360-provenance-report` | Build, validate, enrich, or upgrade an interactive DPE provenance report |
| `/data360-html-export` | Convert any markdown report to self-contained HTML |
| `/data360-setup` | Install dependencies, register MCP server, add a new client |

## Per-client folder layout

```
~/Projects/clients/<client>/Data360/
├── config.md              Org connection, DMO map, CI inventory
├── object-model.md        Narrative, human-readable — generated by intake.py
├── object-model/          Per-artifact YAML sidecars (diffable, tool-consumable)
│   ├── index.yaml         Org rollup: alias, instanceUrl, counts
│   ├── _manifest.yaml     Per-artifact content hashes (used by --incremental)
│   ├── dmos/<name>.yaml
│   ├── dlos/<name>.yaml
│   ├── mappings/<name>.yaml   Per DLO→DMO mapping
│   ├── cis/<name>.yaml
│   ├── transforms/<name>.yaml
│   ├── streams/<name>.yaml
│   ├── segments/<name>.yaml
│   ├── activations/<name>.yaml
│   └── lineage.yaml          Full Stream/DMO/CI/Segment/Activation graph
├── queries/               SQL files for CIs and ad-hoc analysis
├── queries-converted/     ci_convert.py output — CI editor-compatible SQL
├── reports/               Audit reports (markdown)
├── transforms/            Data Transform JSON exports
└── segments/              Per-segment artifacts
```

**Containment rule:** everything this toolkit produces for a client stays under that client's `Data360/` folder. Never write Data 360 artifacts to the client root or sibling folders.

## Data Cloud Concepts

- DMOs use the `ssot__` namespace (e.g. `ssot__Individual__dlm`, `ssot__Account__dlm`)
- Custom DMOs use client-defined names (e.g. `Purchase_History__dlm`)
- CI output objects use the `__cio` suffix (e.g. `Customer_Lifetime_Value__cio`)
- Segments are typically defined on `ssot__Individual__dlm` as the root object

## API Reference

- **Minimum API version: v64.0** — Data 360 Connect API endpoints only exist on v64.0+
- CI SQL: returned in full in the `expression` field on `/ssot/calculated-insights` (HTML-entity-encoded)
- CI output objects expose **measures** (not `metrics`) on `/ssot/calculated-insights` — match the API field name in sidecars
- Data Transform definitions: returned under `definitions[].nodes[].parameters` on `/ssot/data-transforms` (HTML-entity-encoded)
- DPE definitions: returned from Tooling API `BatchCalcJobDefinition` metadata; `writebacks[]` contains exact mappings and sequence
- SQL queries (async): `POST /ssot/query-sql` → `GET /ssot/query-sql/:id/rows`
- Manual Setup export is **not required** for CI SQL or Transform definitions

## Auth

All scripts and the MCP server resolve the access token through `sf_auth.py`. It tries the new `sf org auth show-access-token --json --no-prompt` command first and falls back to parsing `accessToken` from `sf org display --json` for older sf CLI versions. This is to bridge the [2026-05-27 sf CLI credential-redaction breaking change](https://github.com/forcedotcom/cli/issues/3560), which removes `accessToken` from `sf org display` output. Don't add new direct calls to `sf org display` for tokens — import `get_token_and_url` (or `get_token_and_url_or_exit` for CLI scripts) from `data360_analyst.sf_auth` instead.

## Answering Questions: Local First, Live on Request

When a question can be answered from the intake snapshot on disk (`object-model/` sidecars, `queries/*.sql`, `lineage.yaml`, reports), answer from those local files first. Do not reach for the MCP tools by default.

- **Say so explicitly.** Tell the user the answer comes from the local intake snapshot, and note when it was last generated if known (check `object-model/index.yaml` or file timestamps).
- **Then offer a live search.** After answering, ask whether the user wants a live search against the org via the MCP tools to confirm or refresh — don't run it unprompted.
- **Exceptions:** go live without asking only when (a) the user explicitly asks for live/current data, (b) the needed artifact isn't in the snapshot, or (c) the question is inherently live (row counts, `run_sql`, current job/stream status). Even then, state that the result is live.

This is a precedence rule for interactive Q&A, not a ban on MCP. The Python static-analysis scripts always read from disk by design; that's unchanged.

## Working Conventions

- SQL queries: one file per CI or analysis topic, named after the CI or subject
- Reports: markdown, one file per audit or analysis run
- Never commit access tokens or credentials

## Development

When editing the toolkit itself (not client analysis), run the test suite from repo root: `.venv/bin/pytest tests/`. `examples/demo-org/` is a synthetic snapshot for exercising scripts (`ci_audit.py`, `dashboard.py`, etc.) without a live org or real client data — prefer it over inventing fixtures inline. `docs/plans/` holds design docs for in-flight features (e.g. `ci_visualize.py`'s phased build); it's gitignored, so check it locally for the "why" behind an unfinished tool before assuming a script is done.
