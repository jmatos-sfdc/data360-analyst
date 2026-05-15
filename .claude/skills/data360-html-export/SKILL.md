---
name: data360-html-export
description: Generate a single-page HTML dashboard from data360-analyst output — tabbed, self-contained, client-shippable. Use when user asks to "generate dashboard", "build dashboard", "export as HTML", "HTML report", "client-shippable HTML", or "create dashboard".
triggers:
  - "generate dashboard"
  - "build dashboard"
  - "export as HTML"
  - "HTML report"
  - "client-shippable HTML"
  - "html export"
  - "create dashboard"
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
python dashboard.py --data-dir ~/Projects/clients/<Client>/data360 \
                    --client "<Client Name>"

# Output defaults to <data-dir>/reports/dashboard.html
# Override with --output <path>
```

## Prerequisites

Run `intake.py` first to populate `object-model/` and `queries/`. Then run whichever analysis scripts you want tabs for (`dmo_graph.py`, `ci_audit.py`, `cluster_cis_by_dmo.py`, `diagram_crosscheck.py`). The dashboard reads whatever exists — missing reports simply skip that tab.

## Workflow

1. Run intake: `python intake.py --org <alias> --output-dir ~/Projects/clients/<Client>/data360`
2. Run analyses (any/all): `python dmo_graph.py ...`, `python ci_audit.py ...`, etc.
3. Generate dashboard: `python dashboard.py --data-dir ~/Projects/clients/<Client>/data360 --client "<Client>"`
4. Open `reports/dashboard.html` in browser, share with client, or print to PDF

## Output

Writes `reports/dashboard.html` in the client's data360 folder. Both markdown reports and the HTML dashboard are kept — markdown for version control, HTML for delivery.
