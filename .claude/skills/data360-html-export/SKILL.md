---
name: data360-html-export
description: Generate a single-page HTML dashboard from data360-analyst output — tabbed, self-contained, client-shippable. Use when user asks to "generate the data 360 dashboard", "build a data 360 dashboard", "export data 360 as HTML", "client-shippable Data 360 HTML", or "data 360 html export".
triggers:
  - "generate data 360 dashboard"
  - "build data 360 dashboard"
  - "create data 360 dashboard"
  - "data 360 html export"
  - "export data 360 as HTML"
  - "data 360 HTML report"
  - "client-shippable data 360 HTML"
---

# Data360 HTML Dashboard

Generate a single-page, tabbed HTML dashboard from data360-analyst output. Reads YAML sidecars, markdown reports, and SQL files — produces one self-contained `.html` file.

## Design principles

- **Self-contained** — single `.html` file, no external dependencies
- **CSS-only tabs** — radio-button pattern, no JavaScript
- **System fonts** — no web font downloads
- **Dark/light** — `prefers-color-scheme` media query
- **Print-friendly** — all tabs expand when printed

## Tabs generated

| Tab | Source | Shows when |
|---|---|---|
| Overview | `object-model/index.yaml` + CI/DMO/segment sidecars | Always (requires intake) |
| Architecture | `reports/dmo-graph.md` + DMO sidecars | `dmo-graph.md` exists or DMOs present |
| CI Audit | `reports/ci-audit.md` | `ci-audit.md` exists |
| CI Clusters | `reports/cis-on-*.md` | Any cluster report exists |
| Diagram Gaps | `reports/diagram-crosscheck.md` | Crosscheck report exists |
| Transforms | `object-model/transforms/*.yaml` | Any transform sidecars exist |

## Usage

```bash
# From the toolkit virtualenv
data360 dashboard --data-dir ~/Projects/clients/<Client>/Data360 \
                    --client "<Client Name>"

# Output defaults to <data-dir>/reports/dashboard.html
# Override with --output <path>
```

## Prerequisites

Run `intake.py` first to populate `object-model/` and `queries/`. Then run whichever analysis scripts you want tabs for (`dmo_graph.py`, `ci_audit.py`, `cluster_cis_by_dmo.py`, `diagram_crosscheck.py`). The dashboard reads whatever exists — missing reports simply skip that tab.

## Workflow

1. Run intake: `data360 intake --org <alias> --output-dir ~/Projects/clients/<Client>/Data360`
2. Run analyses (any/all): `data360 dmo-graph ...`, `data360 ci-audit ...`, etc.
3. Generate dashboard: `data360 dashboard --data-dir ~/Projects/clients/<Client>/Data360 --client "<Client>"`
4. Open `reports/dashboard.html` in browser, share with client, or print to PDF

## Output

Writes `reports/dashboard.html` in the client's Data360 folder. Both markdown reports and the HTML dashboard are kept — markdown for version control, HTML for delivery.
