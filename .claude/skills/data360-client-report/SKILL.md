---
name: data360-client-report
description: Generate a client-shippable summary report from existing Data 360 analysis — assembles findings from object model, CI audit, architecture analysis, and ad-hoc investigation into a single deliverable. Use when user asks to "generate a report", "client report", "summary report", "create deliverable", or "compile findings".
triggers:
  - "generate a report"
  - "client report"
  - "summary report"
  - "create deliverable"
  - "compile findings"
  - "write up findings"
---

# Data360 Client Report

Assemble analysis findings into a single, client-shippable deliverable.

## Source material

Gather from the client's Data360 folder:

| Source | Path | Contains |
|---|---|---|
| Object model | `object-model.md` | Org overview: DMO count, CI count, stream count, transform count |
| CI audit | `reports/ci-audit.md` | SQL correctness findings (leap-year, single-day trigger, hard-coded IDs) |
| DMO graph | `reports/dmo-graph.md` | Backbone DMOs ranked by fan-in |
| CI clusters | `reports/cis-on-<dmo>.md` | Per-DMO CI groupings and join pattern analysis |
| Diagram cross-check | `reports/diagram-crosscheck.md` | Diagram vs org discrepancies |
| Transform audit | `reports/transform-audit.md` | Transform DAG findings |
| Segment notes | `segments/<name>/notes.md` | Per-segment decoded criteria and validation |
| Ad-hoc queries | `queries/*.sql` | CI SQL files for reference |
| Design notes | Any ticket-level design notes | Architecture decisions, assumptions, open items |

Not all sources will exist for every engagement. Use what's available.

## Report structure

```markdown
# Data 360 Analysis — <Client>

_Prepared <date> by <author>_
_Org: <alias> (<instance URL>)_

## Executive Summary
<!-- 3-5 bullet points: what we found, what matters, what to do next -->

## Org Overview
<!-- From object-model.md: counts, data spaces, key DMOs -->

## Architecture
<!-- From dmo-graph.md: backbone DMOs, fan-in rankings -->
<!-- From diagram-crosscheck.md: diagram vs reality gaps -->

## Calculated Insights
<!-- Total count, breakdown by type/status -->
<!-- From ci-audit.md: correctness findings with severity -->
<!-- From CI cluster reports: consistency analysis -->

## Data Transforms
<!-- From transform-audit.md: dedup grain, formula patterns -->

## Segments & Activations
<!-- From segment notes: key segments, criteria summaries, activation status -->

## Findings & Recommendations
<!-- Prioritized list: critical → medium → low -->
<!-- Each finding: what, where, why it matters, recommended action -->

## Appendix
<!-- Reference: DMO inventory, CI inventory, query files -->
```

## Writing guidelines

- **Lead with findings, not process.** The client doesn't need to know which script found the issue.
- **Quantify.** "3 of 172 CIs have a leap-year bug" is better than "some CIs have issues."
- **Severity matters.** Critical = breaks in production. Medium = data quality risk. Low = cleanup/hygiene.
- **Actionable recommendations.** "Replace `MONTH()/DAY()` pattern with `DATE_TRUNC` comparison in CIs X, Y, Z" — not "fix the date logic."
- **No internal tooling references.** Don't mention intake.py, ci_audit.py, MCP server, AI tools, or local file paths. Frame as direct analysis work.
- **Screenshots optional.** If the user wants to include Setup UI screenshots, they can add them. The report works without them.

## Output

Write to `~/Projects/clients/<Client>/Data360/reports/client-report-<date>.md`.

If the user wants a different format (Google Doc, Slides, PDF), generate the markdown first, then assist with conversion.

## After generating

- Ask the user to review before sharing externally
- Flag any findings that reference sensitive data (account names, IDs) that might need redaction for certain audiences
- Offer to create an executive-only version (summary + recommendations, no technical appendix)
