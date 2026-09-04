#!/usr/bin/env python3
"""Generate a single-page HTML dashboard from data360-analyst output.

Reads the YAML sidecars, markdown reports, and SQL files produced by
intake.py / dmo_graph.py / ci_audit.py / cluster_cis_by_dmo.py /
diagram_crosscheck.py and assembles a tabbed, self-contained HTML page
with no external dependencies.

Usage:
    python dashboard.py --data-dir ~/Projects/clients/Acme/Data360 \
                        --output   ~/Projects/clients/Acme/Data360/reports/dashboard.html
"""

import argparse
import glob
import html
import json
import math
import os
import re
import sys
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# YAML loaders
# ---------------------------------------------------------------------------

def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def load_all_yaml(directory):
    items = []
    if not os.path.isdir(directory):
        return items
    for p in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
        try:
            items.append(load_yaml(p))
        except Exception:
            pass
    return items

# ---------------------------------------------------------------------------
# Markdown report reader (returns raw text or None)
# ---------------------------------------------------------------------------

def read_report(data_dir, filename):
    path = os.path.join(data_dir, "reports", filename)
    if os.path.isfile(path):
        with open(path) as f:
            return f.read()
    return None


def find_cluster_reports(data_dir):
    pattern = os.path.join(data_dir, "reports", "cis-on-*.md")
    return sorted(glob.glob(pattern))

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def esc(text):
    if text is None:
        return ""
    return html.escape(str(text))


def html_table(headers, rows, col_classes=None):
    """Build an HTML table string from headers and rows of strings."""
    parts = ['<table><thead><tr>']
    for i, h in enumerate(headers):
        cls = f' class="{col_classes[i]}"' if col_classes and i < len(col_classes) else ""
        parts.append(f"<th{cls}>{esc(h)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        for i, cell in enumerate(row):
            cls = f' class="{col_classes[i]}"' if col_classes and i < len(col_classes) else ""
            parts.append(f"<td{cls}>{cell}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def badge(text, variant="default"):
    colors = {
        "green": "#059669",
        "yellow": "#d97706",
        "red": "#dc2626",
        "blue": "#2563eb",
        "default": "#6b7280",
    }
    bg = colors.get(variant, colors["default"])
    return (
        f'<span class="badge" style="background:{bg};color:#fff;'
        f'padding:2px 8px;border-radius:10px;font-size:0.8em;">'
        f"{esc(text)}</span>"
    )


def status_badge(status):
    s = (status or "").upper()
    if s == "ACTIVE":
        return badge("Active", "green")
    if s == "INACTIVE":
        return badge("Inactive", "yellow")
    if s in ("ERROR", "FAILED"):
        return badge(s.title(), "red")
    return badge(s.title() if s else "Unknown", "default")

# ---------------------------------------------------------------------------
# SVG chart generators
# ---------------------------------------------------------------------------

CHART_PALETTE = [
    "#2563eb",  # blue
    "#059669",  # green
    "#d97706",  # amber
    "#dc2626",  # red
    "#7c3aed",  # purple
    "#0891b2",  # cyan
    "#ea580c",  # orange
    "#4f46e5",  # indigo
    "#be185d",  # pink
    "#65a30d",  # lime
]

STATUS_COLORS = {
    "ACTIVE": "#059669",
    "INACTIVE": "#d97706",
    "ERROR": "#dc2626",
    "FAILED": "#dc2626",
}

SEVERITY_COLORS = {
    "high": "#dc2626",
    "medium": "#d97706",
    "low": "#059669",
    "info": "#2563eb",
}


def _arc_path(cx, cy, outer_r, inner_r, start_angle, end_angle):
    """Return an SVG path `d` string for a donut arc segment."""
    sa, ea = start_angle, end_angle
    large = 1 if (ea - sa) > math.pi else 0

    ox1 = cx + outer_r * math.cos(sa)
    oy1 = cy + outer_r * math.sin(sa)
    ox2 = cx + outer_r * math.cos(ea)
    oy2 = cy + outer_r * math.sin(ea)
    ix1 = cx + inner_r * math.cos(ea)
    iy1 = cy + inner_r * math.sin(ea)
    ix2 = cx + inner_r * math.cos(sa)
    iy2 = cy + inner_r * math.sin(sa)

    return (
        f"M {ox1:.2f} {oy1:.2f} "
        f"A {outer_r} {outer_r} 0 {large} 1 {ox2:.2f} {oy2:.2f} "
        f"L {ix1:.2f} {iy1:.2f} "
        f"A {inner_r} {inner_r} 0 {large} 0 {ix2:.2f} {iy2:.2f} Z"
    )


def svg_donut(slices, size=200, hole=0.6, title=None):
    """Render a donut chart as inline SVG using arc paths.

    slices: list of (label, value, color)
    Returns an HTML string with the SVG and a legend.
    """
    total = sum(v for _, v, _ in slices)
    if total == 0:
        return ""

    cx = cy = size / 2
    outer_r = (size / 2) * 0.85
    inner_r = outer_r * hole

    paths = []
    angle = -math.pi / 2  # start at 12 o'clock
    non_zero = [(l, v, c) for l, v, c in slices if v > 0]

    for label, value, color in non_zero:
        pct = value / total
        sweep = pct * 2 * math.pi

        if len(non_zero) == 1:
            # Full circle — arc path can't represent 360 degrees, use two circles
            paths.append(
                f'<circle cx="{cx}" cy="{cy}" r="{outer_r:.2f}" fill="{color}"/>'
                f'<circle cx="{cx}" cy="{cy}" r="{inner_r:.2f}" fill="var(--bg)"/>'
            )
        else:
            end_angle = angle + sweep
            d = _arc_path(cx, cy, outer_r, inner_r, angle, end_angle)
            paths.append(f'<path d="{d}" fill="{color}"/>')
            angle = end_angle

    center_text = (
        f'<text x="{cx}" y="{cy - 8}" text-anchor="middle" '
        f'fill="var(--fg)" font-size="24" font-weight="700">{total}</text>'
        f'<text x="{cx}" y="{cy + 12}" text-anchor="middle" '
        f'fill="var(--muted)" font-size="12">{title or "total"}</text>'
    )

    svg = (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        + "".join(paths)
        + center_text
        + "</svg>"
    )

    legend_items = []
    for label, value, color in slices:
        if value == 0:
            continue
        pct = value / total * 100
        legend_items.append(
            f'<span class="legend-item">'
            f'<span class="legend-dot" style="background:{color}"></span>'
            f'{esc(label)}: {value} ({pct:.0f}%)</span>'
        )

    legend = f'<div class="legend">{"".join(legend_items)}</div>'
    return f'<div class="chart-container">{svg}{legend}</div>'


def svg_hbar(items, max_width=500, bar_height=24, label_width=220, title=None):
    """Render a horizontal bar chart as inline SVG.

    items: list of (label, value) — already sorted descending.
    Returns an HTML string.
    """
    if not items:
        return ""

    max_val = max(v for _, v in items)
    if max_val == 0:
        return ""

    bar_area = max_width - label_width - 60
    row_height = bar_height + 8
    chart_height = len(items) * row_height + 10

    bars = []
    for i, (label, value) in enumerate(items):
        y = i * row_height + 5
        w = (value / max_val) * bar_area if max_val else 0
        color = CHART_PALETTE[i % len(CHART_PALETTE)]

        display_label = label if len(label) <= 30 else label[:28] + ".."
        bars.append(
            f'<text x="{label_width - 8}" y="{y + bar_height * 0.7}" '
            f'text-anchor="end" fill="var(--fg)" font-size="12">'
            f'{esc(display_label)}</text>'
            f'<rect x="{label_width}" y="{y + 2}" width="{w:.1f}" '
            f'height="{bar_height - 4}" rx="3" fill="{color}" opacity="0.85"/>'
            f'<text x="{label_width + w + 6}" y="{y + bar_height * 0.7}" '
            f'fill="var(--muted)" font-size="12">{value:,}</text>'
        )

    heading = f"<h3>{esc(title)}</h3>" if title else ""
    svg = (
        f'{heading}'
        f'<div class="chart-scroll">'
        f'<svg width="{max_width}" height="{chart_height}" '
        f'viewBox="0 0 {max_width} {chart_height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        + "".join(bars)
        + "</svg></div>"
    )
    return svg


# ---------------------------------------------------------------------------
# Tab builders — each returns (tab_label, html_content) or None
# ---------------------------------------------------------------------------

def build_overview_tab(data_dir, index_data, dmos, cis, transforms, segments):
    counts = index_data.get("counts", {})
    org = index_data.get("orgAlias", "Unknown")
    url = index_data.get("instanceUrl", "")
    ts = index_data.get("generatedAt", "")
    api = index_data.get("apiVersion", "")

    stat_cards = [
        ("DMOs", counts.get("dmos", 0)),
        ("Calculated Insights", counts.get("cis", 0)),
        ("Data Transforms", counts.get("transforms", 0)),
        ("Segments", counts.get("segments", 0)),
        ("Data Streams", counts.get("streams", 0)),
        ("IR Rulesets", counts.get("identityResolutionRulesets", 0)),
    ]

    cards_html = '<div class="stat-grid">'
    for label, count in stat_cards:
        cards_html += (
            f'<div class="stat-card">'
            f'<div class="stat-number">{count}</div>'
            f'<div class="stat-label">{esc(label)}</div>'
            f"</div>"
        )
    cards_html += "</div>"

    meta = (
        f'<p class="meta">Org: <strong>{esc(org)}</strong>'
        f" &middot; API v{esc(api)}"
        f" &middot; Snapshot: {esc(ts)}</p>"
    )

    # CI status breakdown
    ci_statuses = {}
    ci_types = {}
    for ci in cis:
        st = ci.get("status", "UNKNOWN")
        ci_statuses[st] = ci_statuses.get(st, 0) + 1
        dt = ci.get("definitionType", "UNKNOWN")
        ci_types[dt] = ci_types.get(dt, 0) + 1

    # CI status donut
    ci_status_donut = ""
    if ci_statuses:
        slices = []
        for st in sorted(ci_statuses.keys()):
            color = STATUS_COLORS.get(st, "#6b7280")
            slices.append((st.title(), ci_statuses[st], color))
        ci_status_donut = svg_donut(slices, title="CIs")

    # CI type donut
    ci_type_donut = ""
    if ci_types:
        sorted_types = sorted(ci_types.items(), key=lambda x: -x[1])
        slices = []
        for i, (t, c) in enumerate(sorted_types):
            slices.append((t.replace("_", " ").title(), c, CHART_PALETTE[i % len(CHART_PALETTE)]))
        ci_type_donut = svg_donut(slices, title="by type")

    charts_row = ""
    if ci_status_donut or ci_type_donut:
        charts_row = f'<div class="chart-row"><div>{ci_status_donut}</div><div>{ci_type_donut}</div></div>'

    # DMO categories bar chart
    dmo_cats = {}
    for d in dmos:
        cat = d.get("category", "UNKNOWN")
        dmo_cats[cat] = dmo_cats.get(cat, 0) + 1
    dmo_cat_chart = ""
    if dmo_cats:
        sorted_cats = sorted(dmo_cats.items(), key=lambda x: -x[1])
        dmo_cat_chart = svg_hbar(sorted_cats, title="DMO Categories")

    content = meta + cards_html + charts_row + dmo_cat_chart
    return ("Overview", content)


def build_architecture_tab(data_dir, dmos, cis, segments):
    report = read_report(data_dir, "dmo-graph.md")
    if not report and not dmos:
        return None

    content = ""

    # Parse the ranking table from dmo-graph.md
    ranking_data = []  # (name, ci_count, seg_count, total) as raw values
    if report:
        ranking_rows = []
        in_table = False
        for line in report.splitlines():
            if "| DMO" in line and "CIs" in line:
                in_table = True
                continue
            if in_table and line.startswith("|--"):
                continue
            if in_table and line.startswith("|"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) >= 4:
                    name = cells[0].strip("`").strip()
                    ci_count = cells[1].strip()
                    seg_count = cells[2].strip()
                    total = cells[3].replace("**", "").strip()
                    ranking_data.append((name, int(ci_count or 0), int(seg_count or 0), int(total or 0)))
                    ranking_rows.append([
                        f"<code>{esc(name)}</code>",
                        ci_count,
                        seg_count,
                        f"<strong>{total}</strong>",
                    ])
            elif in_table and not line.startswith("|"):
                break

        # Fan-in bar chart (top 15)
        if ranking_data:
            chart_items = [(name, total) for name, _, _, total in ranking_data[:15]]
            content += svg_hbar(chart_items, title="Top DMOs by Fan-in (CIs + Segments)")

        if ranking_rows:
            content += "<h3>Backbone DMOs (by fan-in)</h3>"
            content += html_table(
                ["DMO", "CIs", "Segments", "Total"],
                ranking_rows[:20],
                ["", "num", "num", "num"],
            )
            if len(ranking_rows) > 20:
                rest_tbl = html_table(
                    ["DMO", "CIs", "Segments", "Total"],
                    ranking_rows[20:],
                    ["", "num", "num", "num"],
                )
                content += (
                    '<details class="collapsible"><summary>'
                    f"Show all {len(ranking_rows)} DMOs</summary>"
                    f"{rest_tbl}</details>"
                )

    # CI-on-CI dependencies (parse from report)
    if report and "## CI" in report:
        dep_rows = []
        in_dep = False
        for line in report.splitlines():
            if re.match(r"^##\s+CI.*dependen", line, re.IGNORECASE):
                in_dep = True
                continue
            if in_dep and line.startswith("##"):
                break
            if in_dep and line.startswith("|") and not line.startswith("|--") and "Consumer" not in line.split("|")[1] if len(line.split("|")) > 1 else True:
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) >= 2 and cells[0].strip("`") != "CI":
                    dep_rows.append([f"<code>{esc(cells[0].strip('`'))}</code>"] + [esc(c) for c in cells[1:]])

        if dep_rows:
            headers = ["CI"] + [f"Col {i+1}" for i in range(1, len(dep_rows[0]))]
            if dep_rows and len(dep_rows[0]) == 2:
                headers = ["CI", "Consumed By"]
            elif dep_rows and len(dep_rows[0]) == 3:
                headers = ["CI", "Consumed By", "Count"]
            content += "<h3>CI-on-CI Dependencies</h3>"
            content += html_table(headers, dep_rows)

    if not content:
        # Fallback: just list top DMOs by field count
        dmo_rows = []
        for d in sorted(dmos, key=lambda x: len(x.get("fields", [])), reverse=True)[:20]:
            dmo_rows.append([
                f"<code>{esc(d.get('name', ''))}</code>",
                esc(d.get("category", "")),
                str(len(d.get("fields", []))),
            ])
        if dmo_rows:
            content = "<h3>Top DMOs by Field Count</h3>"
            content += html_table(["DMO", "Category", "Fields"], dmo_rows, ["", "", "num"])

    return ("Architecture", content)


_AUDIT_SUMMARY_LINE = re.compile(r"^- (.+?):\s*\*\*(\d+)\*\*", re.MULTILINE)


def _parse_ci_audit_summary(summary_text):
    """Pull the `- Files with X: **N**` lines out of the ## Summary section.

    Returns a list of (label, count) tuples in source order, plus the count of
    parse_error files (called out separately under "sqlglot could not parse").
    """
    rows = []
    for label, n in _AUDIT_SUMMARY_LINE.findall(summary_text):
        rows.append((label.strip(), int(n)))
    return rows


def _parse_ci_findings(card_body):
    """Parse one CI's bullet-list findings.

    Each top-level `- ...` line is a finding header; nested `  - ...` lines are
    the per-finding instances. Severity is inferred from the header:

      `- ⚠ ...`            → warning  (yellow)
      `- **Hardcoded...**` → high     (red)
      `- **<other>**`      → high     (red, e.g. Single-day trigger, Leap-year)
      `- ` (plain bullet)  → info     (blue, e.g. CURRENT_DATE() count, dedup window)

    A "parse error" line uses the ⚠ marker but is severity high — sqlglot
    couldn't parse the file, so the audit didn't actually run.

    Returns: list of {severity, summary, details: [str, ...]}.
    """
    findings = []
    current = None
    for raw in card_body.splitlines():
        if raw.startswith("- "):
            line = raw[2:].rstrip()
            severity = "info"
            if line.startswith("⚠ sqlglot parse error"):
                severity = "high"
            elif line.startswith("⚠"):
                severity = "warning"
            elif line.startswith("**"):
                severity = "high"
            current = {"severity": severity, "summary": line, "details": []}
            findings.append(current)
        elif current is not None and raw.startswith("  - "):
            current["details"].append(raw[4:].rstrip())
        elif raw.strip() == "":
            current = None
    return findings


_SEVERITY_BADGE = {
    "high": ("High", "red"),
    "warning": ("Warning", "yellow"),
    "info": ("Info", "blue"),
}


def build_ci_audit_tab(data_dir):
    report = read_report(data_dir, "ci-audit.md")
    if not report:
        return None

    # Split on level-2 headers; pull the Summary block out so the per-file
    # block is the only thing we walk for cards.
    summary_match = re.search(r"^## Summary\s*\n(.*?)(?=^## |\Z)", report,
                              flags=re.MULTILINE | re.DOTALL)
    perfile_match = re.search(r"^## Per-file findings\s*\n(.*?)(?=^## |\Z)", report,
                              flags=re.MULTILINE | re.DOTALL)
    if not perfile_match:
        return None

    summary_rows = _parse_ci_audit_summary(summary_match.group(1)) if summary_match else []

    # Each `### <name>` starts a CI card.
    ci_cards = []
    for chunk in re.split(r"^### ", perfile_match.group(1), flags=re.MULTILINE)[1:]:
        head, _, body = chunk.partition("\n")
        ci_name = head.strip().strip("`")
        findings = _parse_ci_findings(body)
        # Treat plain "No issues detected." as clean.
        if len(findings) == 1 and findings[0]["summary"].lower().startswith("no issues detected"):
            findings = []
        ci_cards.append((ci_name, findings))

    if not ci_cards:
        return None

    # ── Summary stat cards (parsed from ## Summary, with derived totals) ──────
    total_files = len(ci_cards)
    cis_with = sum(1 for _, fs in ci_cards if fs)
    cis_clean = total_files - cis_with
    total_findings = sum(len(fs) for _, fs in ci_cards)

    content = (
        f'<div class="stat-grid">'
        f'<div class="stat-card"><div class="stat-number">{total_files}</div>'
        f'<div class="stat-label">CIs Audited</div></div>'
        f'<div class="stat-card"><div class="stat-number">{total_findings}</div>'
        f'<div class="stat-label">Total Findings</div></div>'
        f'<div class="stat-card"><div class="stat-number">{cis_with}</div>'
        f'<div class="stat-label">CIs with Findings</div></div>'
        f'<div class="stat-card"><div class="stat-number">{cis_clean}</div>'
        f'<div class="stat-label">Clean CIs</div></div>'
        f"</div>"
    )

    # Aggregate counts straight from the report's ## Summary — these are the
    # numbers `ci_audit.py` already computes (per check, deduped per file), so
    # we preserve them rather than re-deriving.
    if summary_rows:
        content += "<h3>Summary by check</h3>"
        content += html_table(
            ["Check", "Files affected"],
            [[esc(label), str(n)] for label, n in summary_rows],
            ["", "num"],
        )

    # ── Severity donut across findings ────────────────────────────────────────
    sev_counts = {}
    for _, fs in ci_cards:
        for f in fs:
            sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1
    if sev_counts:
        sev_palette = {"high": "#dc2626", "warning": "#d97706", "info": "#2563eb"}
        sev_slices = [
            (s.title(), sev_counts[s], sev_palette.get(s, "#6b7280"))
            for s in ("high", "warning", "info") if s in sev_counts
        ]
        content += svg_donut(sev_slices, title="findings")

    # ── Per-CI cards ──────────────────────────────────────────────────────────
    for ci_name, fs in ci_cards:
        card = f'<div class="ci-card"><h4>{esc(ci_name)}</h4>'
        if not fs:
            card += '<p class="clean">No findings</p>'
        else:
            rows = []
            for i, f in enumerate(fs, 1):
                label, variant = _SEVERITY_BADGE.get(f["severity"], ("Info", "blue"))
                summary_html = esc(f["summary"])
                if f["details"]:
                    detail_html = "<br>".join(
                        f"<code>{esc(d)}</code>" for d in f["details"]
                    )
                    summary_html += f'<div class="detail">{detail_html}</div>'
                rows.append([str(i), badge(label, variant), summary_html])
            card += html_table(["#", "Severity", "Finding"], rows)
        card += "</div>"
        content += card

    return ("CI Audit", content)


# ── CI Convert tab ──────────────────────────────────────────────────────────


_CONVERT_HEADER_LINE = re.compile(
    r"_Converted (\d+) file\(s\) — (\d+) auto-fix\(es\), (\d+) manual flag\(s\), "
    r"(\d+) remaining violation\(s\) after conversion\._"
)
_CONVERT_RULE_LINE = re.compile(
    r"^- `([^`]+)` × (\d+)\s+—\s+(.*)$"
)


def _parse_ci_convert_file_block(body):
    """Parse one file's section between `## <name>` and the next `## `.

    Returns {"auto": [{rule, count, message}], "flag": [...], "remaining": [str]}.
    """
    auto, flag, remaining = [], [], []
    section = None  # "auto" | "flag" | "remaining"
    for raw in body.splitlines():
        line = raw.rstrip()
        if line.startswith("**Auto-applied**"):
            section = "auto"
            continue
        if line.startswith("**Flagged for review**"):
            section = "flag"
            continue
        if line.startswith("**Remaining audit violations**"):
            section = "remaining"
            continue
        if not line:
            continue
        if section in ("auto", "flag"):
            m = _CONVERT_RULE_LINE.match(line)
            if m:
                rule, count, msg = m.group(1), int(m.group(2)), m.group(3).strip()
                target = auto if section == "auto" else flag
                target.append({"rule": rule, "count": count, "message": msg})
        elif section == "remaining":
            if line.startswith("- "):
                remaining.append(line[2:].strip().strip("`"))
    return {"auto": auto, "flag": flag, "remaining": remaining}


def build_ci_convert_tab(data_dir):
    report = read_report(data_dir, "ci-convert.md")
    if not report:
        return None

    header_match = _CONVERT_HEADER_LINE.search(report)
    if not header_match:
        return None
    total_files = int(header_match.group(1))
    total_auto = int(header_match.group(2))
    total_flag = int(header_match.group(3))
    total_remaining = int(header_match.group(4))

    # Per-file blocks. The "Clean files" section is a flat bullet list under
    # `## Clean files` — handle it separately from the dirty-file blocks.
    clean_match = re.search(r"^## Clean files\s*\n(.*?)(?=^## |\Z)", report,
                            flags=re.MULTILINE | re.DOTALL)
    clean_files = []
    if clean_match:
        for raw in clean_match.group(1).splitlines():
            line = raw.strip()
            if line.startswith("- "):
                clean_files.append(line[2:].strip())

    file_cards = []
    for chunk in re.split(r"^## ", report, flags=re.MULTILINE)[1:]:
        head, _, body = chunk.partition("\n")
        name = head.strip()
        if name in ("Clean files",):
            continue
        if name.startswith("CI SQL Convert"):  # the H1-derived first chunk
            continue
        parsed = _parse_ci_convert_file_block(body)
        # Skip blocks that only contain section headers but no actual entries
        # (defensive — the report shouldn't produce these, but better safe).
        if not (parsed["auto"] or parsed["flag"] or parsed["remaining"]):
            continue
        file_cards.append((name, parsed))

    # ── Stat cards ────────────────────────────────────────────────────────────
    content = (
        f'<div class="stat-grid">'
        f'<div class="stat-card"><div class="stat-number">{total_files}</div>'
        f'<div class="stat-label">CIs Converted</div></div>'
        f'<div class="stat-card"><div class="stat-number">{total_auto}</div>'
        f'<div class="stat-label">Auto-fixes</div></div>'
        f'<div class="stat-card"><div class="stat-number">{total_flag}</div>'
        f'<div class="stat-label">Manual Flags</div></div>'
        f'<div class="stat-card"><div class="stat-number">{total_remaining}</div>'
        f'<div class="stat-label">Remaining Violations</div></div>'
        f"</div>"
    )

    # ── Aggregate counts by rule (across all files) ───────────────────────────
    by_rule_auto = {}
    by_rule_flag = {}
    for _, p in file_cards:
        for entry in p["auto"]:
            by_rule_auto[entry["rule"]] = by_rule_auto.get(entry["rule"], 0) + entry["count"]
        for entry in p["flag"]:
            by_rule_flag[entry["rule"]] = by_rule_flag.get(entry["rule"], 0) + entry["count"]

    if by_rule_auto:
        content += "<h3>Auto-applied rewrites</h3>"
        rows = sorted(((r, c) for r, c in by_rule_auto.items()), key=lambda x: -x[1])
        content += html_table(
            ["Rule", "Occurrences"],
            [[esc(r), str(c)] for r, c in rows],
            ["", "num"],
        )
    if by_rule_flag:
        content += "<h3>Flagged for human review</h3>"
        rows = sorted(((r, c) for r, c in by_rule_flag.items()), key=lambda x: -x[1])
        content += html_table(
            ["Rule", "Occurrences"],
            [[esc(r), str(c)] for r, c in rows],
            ["", "num"],
        )

    # ── Donut: auto vs flag vs remaining ──────────────────────────────────────
    if total_auto or total_flag or total_remaining:
        slices = []
        if total_auto:
            slices.append(("Auto", total_auto, "#16a34a"))
        if total_flag:
            slices.append(("Flag", total_flag, "#d97706"))
        if total_remaining:
            slices.append(("Remaining", total_remaining, "#dc2626"))
        content += svg_donut(slices, title="conversion outcomes")

    # ── Per-CI cards (only those with notes or remaining) ─────────────────────
    for name, parsed in file_cards:
        card = f'<div class="ci-card"><h4>{esc(name)}</h4>'
        rows = []
        idx = 1
        for entry in parsed["auto"]:
            rows.append([
                str(idx), badge("Auto", "green"),
                f'<code>{esc(entry["rule"])}</code> × {entry["count"]}'
                f'<div class="detail">{esc(entry["message"])}</div>',
            ])
            idx += 1
        for entry in parsed["flag"]:
            rows.append([
                str(idx), badge("Flag", "yellow"),
                f'<code>{esc(entry["rule"])}</code> × {entry["count"]}'
                f'<div class="detail">{esc(entry["message"])}</div>',
            ])
            idx += 1
        for v in parsed["remaining"]:
            rows.append([
                str(idx), badge("Remaining", "red"),
                f'<code>{esc(v)}</code>',
            ])
            idx += 1
        if rows:
            card += html_table(["#", "Severity", "Note"], rows)
        card += "</div>"
        content += card

    if clean_files:
        content += (
            f'<div class="ci-card"><h4>Clean files</h4>'
            f'<p>{len(clean_files)} CI(s) needed no conversion and stayed audit-clean.</p>'
            f'</div>'
        )

    return ("CI Convert", content)


def build_clusters_tab(data_dir):
    reports = find_cluster_reports(data_dir)
    if not reports:
        return None

    content = ""
    for rpath in reports:
        fname = os.path.basename(rpath)
        dmo_slug = fname.replace("cis-on-", "").replace(".md", "")
        with open(rpath) as f:
            text = f.read()

        # Count CIs mentioned
        ci_match = re.search(r"(\d+)\s+CIs?\s+found", text)
        ci_count = ci_match.group(1) if ci_match else "?"

        # Parse the main CI table
        ci_rows = []
        in_table = False
        for line in text.splitlines():
            if "| API Name" in line:
                in_table = True
                continue
            if in_table and line.startswith("|--"):
                continue
            if in_table and line.startswith("|"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) >= 4:
                    ci_rows.append([
                        f"<code>{esc(cells[0].strip('`'))}</code>",
                        esc(cells[1]),
                        status_badge(cells[2]) if len(cells) > 2 else "",
                        esc(cells[3]) if len(cells) > 3 else "",
                    ])
            elif in_table and not line.startswith("|"):
                break

        # Parse join patterns table
        join_rows = []
        in_joins = False
        for line in text.splitlines():
            if "Other DMOs joined" in line:
                in_joins = True
                continue
            if in_joins and line.startswith("|--"):
                continue
            if in_joins and line.startswith("|"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) >= 2:
                    join_rows.append([esc(cells[0]), cells[1]])
            elif in_joins and not line.startswith("|"):
                in_joins = False

        section = (
            f'<details class="collapsible" {"open" if len(reports) == 1 else ""}>'
            f"<summary><strong>{esc(dmo_slug)}</strong> &mdash; {ci_count} CIs</summary>"
        )
        if ci_rows:
            section += html_table(
                ["API Name", "Display", "Status", "Dataspace"],
                ci_rows,
            )
        if join_rows:
            section += "<h4>Join Patterns</h4>"
            section += html_table(["DMOs Joined", "# CIs"], join_rows, ["", "num"])
        section += "</details>"
        content += section

    return ("CI Clusters", content)


def build_diagram_tab(data_dir):
    report = read_report(data_dir, "diagram-crosscheck.md")
    if not report:
        return None

    content = ""

    # Parse summary stats
    for line in report.splitlines():
        m = re.search(r"Diagram has \*\*(\d+) pages?\*\*", line)
        if m:
            content += f'<p>Diagram pages: <strong>{m.group(1)}</strong></p>'
        for label in ["CIs", "DMOs", "Data Transforms"]:
            m = re.search(rf"references \*\*(\d+) {label}\*\*", line)
            if m:
                content += f"<p>{label} matched: <strong>{m.group(1)}</strong></p>"

    # Split into major sections (## headings)
    major_sections = re.split(r"^## ", report, flags=re.MULTILINE)

    gap_content = ""
    page_content = ""

    for ms in major_sections[1:]:
        lines = ms.splitlines()
        section_title = lines[0].strip()

        if "NOT" in section_title and "diagram" in section_title.lower():
            # "CIs on disk but NOT referenced in diagram" — parse ### buckets
            subsections = re.split(r"^### ", ms, flags=re.MULTILINE)
            for ss in subsections[1:]:
                ss_lines = ss.splitlines()
                heading = ss_lines[0].strip()
                items = [l.strip("- ").strip() for l in ss_lines[1:] if l.strip().startswith("-")]
                if items:
                    gap_content += (
                        f"<details><summary>{esc(heading)} ({len(items)})</summary><ul>"
                    )
                    for item in items:
                        gap_content += f"<li><code>{esc(item)}</code></li>"
                    gap_content += "</ul></details>"

        elif "per-page" in section_title.lower() or "page" in section_title.lower():
            # Per-page references — parse ### Page N: headings
            subsections = re.split(r"^### ", ms, flags=re.MULTILINE)
            page_rows = []
            for ss in subsections[1:]:
                ss_lines = ss.splitlines()
                heading = ss_lines[0].strip()
                items = [l.strip("- ").strip() for l in ss_lines[1:] if l.strip().startswith("-")]
                if items:
                    page_rows.append([esc(heading), str(len(items))])

            if page_rows:
                page_content += "<h3>Per-Page References</h3>"
                page_content += html_table(["Page", "Artifacts"], page_rows, ["", "num"])

    if gap_content:
        content += "<h3>On Disk but NOT in Diagram</h3>" + gap_content
    if page_content:
        content += page_content

    return ("Diagram Gaps", content) if content else None


LINEAGE_TYPE_COLORS = {
    "Stream": "#0891b2",
    "DLO": "#0d9488",
    "DMO": "#2563eb",
    "CI": "#7c3aed",
    "Segment": "#d97706",
    "Activation": "#dc2626",
}
# Left-to-right column order matches Data Cloud's data flow:
# Stream → DLO → DMO → CI → Segment → Activation.
LINEAGE_TYPE_ORDER = ["Stream", "DLO", "DMO", "CI", "Segment", "Activation"]


def build_node_meta(data_dir):
    """Build a name→metadata dict for all nodes, for the inspector panel."""
    meta = {}
    for ci in load_all_yaml(os.path.join(data_dir, "object-model", "cis")):
        name = ci.get("apiName") or ci.get("name", "")
        if name:
            meta[name] = {
                "type": "CI",
                "displayName": ci.get("displayName", ""),
                "status": ci.get("status", ""),
                "dimensions": len(ci.get("dimensions") or []),
                "measures": len(ci.get("measures") or []),
            }
    for dmo in load_all_yaml(os.path.join(data_dir, "object-model", "dmos")):
        name = dmo.get("name", "")
        if name:
            meta[name] = {
                "type": "DMO",
                "displayName": dmo.get("label", ""),
                "category": dmo.get("category", ""),
                "fieldCount": len(dmo.get("fields") or []),
            }
    for stream in load_all_yaml(os.path.join(data_dir, "object-model", "streams")):
        name = stream.get("name", "")
        if name:
            meta[name] = {
                "type": "Stream",
                "status": stream.get("status", ""),
                "connectorType": stream.get("connectorType", ""),
                "lastRunStatus": stream.get("lastRunStatus", ""),
                "frequency": stream.get("frequency", ""),
                "totalRecords": stream.get("totalRecords"),
            }
    for seg in load_all_yaml(os.path.join(data_dir, "object-model", "segments")):
        name = seg.get("apiName", "")
        if name:
            meta[name] = {
                "type": "Segment",
                "displayName": seg.get("displayName", ""),
                "status": seg.get("status", ""),
                "segmentOn": seg.get("segmentOnApiName", ""),
            }
    return meta


def build_lineage_graph_svg(nodes, edges, type_by_name):
    """Layered SVG graph: one column per node type, edges as bezier curves.

    Self-contained — no external JS. Embeds a small interactive layer
    (pan/zoom, type filter, click-to-highlight neighbors) inline.
    """
    # Bucket nodes by type, preserving any extras after the canonical order
    extra_types = sorted({n["type"] for n in nodes} - set(LINEAGE_TYPE_ORDER))
    columns = LINEAGE_TYPE_ORDER + extra_types
    by_type = {t: [] for t in columns}
    for n in nodes:
        by_type.setdefault(n["type"], []).append(n)
    for t in by_type:
        by_type[t].sort(key=lambda n: n["name"])

    col_width = 220
    row_height = 18
    pad_x, pad_y = 60, 40
    width = pad_x * 2 + col_width * len(columns)
    tallest = max((len(by_type[t]) for t in columns), default=0)
    height = pad_y * 2 + max(tallest, 10) * row_height + 40

    pos = {}  # name → (x, y)
    headers = []
    node_groups = []
    node_labels = []

    for ci, t in enumerate(columns):
        col_x = pad_x + ci * col_width + col_width / 2
        items = by_type[t]
        # Center the column vertically
        col_height = len(items) * row_height
        start_y = pad_y + 20 + (max(tallest, 10) * row_height - col_height) / 2

        color = LINEAGE_TYPE_COLORS.get(t, "#6b7280")
        headers.append(
            f'<text x="{col_x}" y="{pad_y}" text-anchor="middle" '
            f'fill="{color}" font-size="13" font-weight="700">'
            f'{esc(t)} ({len(items)})</text>'
        )

        for i, n in enumerate(items):
            x = col_x
            y = start_y + i * row_height
            pos[n["name"]] = (x, y)
            label = n["name"]
            display = label if len(label) <= 26 else label[:24] + ".."
            # Labels anchor outward — first column right-aligned to its left,
            # all others left-aligned to the right of the dot.
            if ci == 0:
                anchor, lx = "end", x - 8
            else:
                anchor, lx = "start", x + 8
            # Each node is a <g> with a transparent r=9 hit circle (forgiving
            # click target) plus the visible r=4 dot. The hit circle is what
            # gets pointer events; the dot is purely decorative.
            node_groups.append(
                f'<g class="ln-node" data-name="{esc(label)}" data-type="{esc(t)}">'
                f'<circle class="ln-hit" cx="{x}" cy="{y}" r="9" '
                f'fill="transparent" pointer-events="all">'
                f'<title>{esc(label)}</title></circle>'
                f'<circle class="ln-dot" cx="{x}" cy="{y}" r="4" '
                f'fill="{color}" pointer-events="none"/>'
                f'</g>'
            )
            node_labels.append(
                f'<text class="ln-label" data-name="{esc(label)}" '
                f'x="{lx}" y="{y + 3}" text-anchor="{anchor}" '
                f'font-size="10" fill="var(--fg)">{esc(display)}</text>'
            )

    # Edges — only those whose endpoints are placed
    edge_paths = []
    relations_seen = set()
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a not in pos or b not in pos:
            continue
        rel = e.get("relation", "?")
        relations_seen.add(rel)
        x1, y1 = pos[a]
        x2, y2 = pos[b]
        # Cubic bezier with horizontal control points — Sankey look
        cx1 = x1 + (x2 - x1) * 0.5
        cx2 = x1 + (x2 - x1) * 0.5
        edge_paths.append(
            f'<path class="ln-edge" data-from="{esc(a)}" data-to="{esc(b)}" '
            f'data-relation="{esc(rel)}" '
            f'd="M {x1:.1f} {y1:.1f} C {cx1:.1f} {y1:.1f}, '
            f'{cx2:.1f} {y2:.1f}, {x2:.1f} {y2:.1f}" '
            f'fill="none" stroke="var(--muted)" stroke-width="0.6" '
            f'opacity="0.35"/>'
        )

    # Default: hide labels on graphs with > 60 nodes — they overlap and
    # turn the canvas to mush. User can flip them on with the toggle.
    labels_default_on = len(nodes) <= 60

    # ── Filter bar — row 1: types, labels, focus search, buttons ─────────
    filter_html = '<div class="ln-filters">'
    for t in columns:
        if not by_type[t]:
            continue
        color = LINEAGE_TYPE_COLORS.get(t, "#6b7280")
        filter_html += (
            f'<label class="ln-filter"><input type="checkbox" checked '
            f'data-ln-type="{esc(t)}">'
            f'<span class="ln-swatch" style="background:{color}"></span>'
            f'{esc(t)}</label>'
        )
    labels_checked = " checked" if labels_default_on else ""
    filter_html += (
        f'<label class="ln-filter"><input type="checkbox"{labels_checked} '
        f'class="ln-toggle-labels">Labels</label>'
        '<label class="ln-filter"><input type="checkbox" '
        'class="ln-toggle-edges">Edges</label>'
        '<button class="ln-reset" type="button">Reset</button>'
        '<button class="ln-fit" type="button">Fit</button>'
        '</div>'
    )

    # ── Filter bar — row 2: focus search + edge-relation chips ───────────
    datalist_id = "ln-node-list"
    datalist_html = (
        f'<datalist id="{datalist_id}">'
        + "".join(f'<option value="{esc(n["name"])}">' for n in nodes)
        + "</datalist>"
    )
    filter_html += '<div class="ln-filters ln-filters-2">'
    filter_html += (
        f'<input class="ln-search" list="{datalist_id}" '
        f'placeholder="Focus on node…" />{datalist_html}'
        '<select class="ln-direction" title="Direction of walk from focus">'
        '<option value="both" selected>Both</option>'
        '<option value="up">Upstream only</option>'
        '<option value="down">Downstream only</option>'
        '</select>'
        '<select class="ln-hops" title="Hop limit from focus">'
        '<option value="0">Any hops</option>'
        '<option value="1" selected>1 hop</option>'
        '<option value="2">2 hops</option>'
        '<option value="3">3 hops</option>'
    )
    filter_html += '</select>'
    filter_html += '<button class="ln-clear-focus" type="button">Clear focus</button>'
    filter_html += '</div>'

    # ── Filter bar — row 3: edge-relation chips ──────────────────────────
    if relations_seen:
        filter_html += '<div class="ln-filters ln-filters-3"><span class="ln-rel-label">Relations:</span>'
        for rel in sorted(relations_seen):
            filter_html += (
                f'<label class="ln-filter"><input type="checkbox" checked '
                f'data-ln-relation="{esc(rel)}">'
                f'<code>{esc(rel)}</code></label>'
            )
        filter_html += (
            '<span class="ln-hint">Click a node to focus its lineage · '
            'use Clear focus to exit · scroll to zoom · drag to pan</span>'
            '</div>'
        )

    labels_class = "" if labels_default_on else " ln-labels-off"
    svg = (
        f'<svg class="ln-svg{labels_class} ln-edges-off" viewBox="0 0 {width} {height}" '
        f'data-vb-w="{width}" data-vb-h="{height}" '
        f'preserveAspectRatio="xMidYMid meet" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<g class="ln-viewport">'
        + "".join(edge_paths)
        + "".join(node_groups)
        + "".join(node_labels)
        + "".join(headers)
        + "</g></svg>"
    )

    return (
        '<div class="ln-graph">'
        + filter_html
        + '<div class="ln-graph-body">'
        + f'<div class="ln-canvas">{svg}'
        '<div class="ln-tooltip"></div>'
        '</div>'
        + '<div class="ln-inspector"><div class="ln-ins-toolbar"><button class="ln-ins-close" title="Close">×</button></div><div class="ln-ins-content"></div></div>'
        + '</div>'
        + "</div>"
    )


def build_lineage_tab(data_dir):
    """Render the lineage graph as a tab — interactive Sankey-style graph
    on top, then counts, fan-in/out leaderboards, orphans grouped by type,
    and unresolved-edge surface area."""
    lineage_path = os.path.join(data_dir, "object-model", "lineage.yaml")
    if not os.path.isfile(lineage_path):
        return None
    graph = load_yaml(lineage_path)
    if not graph or not graph.get("nodes"):
        return None

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    unresolved = graph.get("unresolved", [])

    # Build adjacency
    out_adj, in_adj = {}, {}
    for e in edges:
        out_adj.setdefault(e["from"], []).append(e)
        in_adj.setdefault(e["to"], []).append(e)
    type_by_name = {n["name"]: n["type"] for n in nodes}

    # ── Interactive graph (top of tab) ───────────────────────────────────────
    node_meta_json = json.dumps(build_node_meta(data_dir), separators=(',', ':'))
    content = (
        f'<script type="application/json" id="ln-node-meta">{node_meta_json}</script>'
        + build_lineage_graph_svg(nodes, edges, type_by_name)
    )

    # ── Stats strip ──────────────────────────────────────────────────────────
    counts_by_type = {}
    for n in nodes:
        counts_by_type[n["type"]] = counts_by_type.get(n["type"], 0) + 1
    counts_by_relation = {}
    for e in edges:
        counts_by_relation[e["relation"]] = counts_by_relation.get(e["relation"], 0) + 1

    content += '<div class="stat-grid">'
    for label, count in [
        ("Nodes", len(nodes)),
        ("Edges", len(edges)),
        ("Unresolved", len(unresolved)),
    ]:
        content += (
            f'<div class="stat-card"><div class="stat-number">{count}</div>'
            f'<div class="stat-label">{label}</div></div>'
        )
    content += "</div>"

    # ── Counts breakdown ─────────────────────────────────────────────────────
    type_rows = [[esc(t), str(c)] for t, c in sorted(counts_by_type.items())]
    rel_rows = [[esc(r), str(c)] for r, c in sorted(counts_by_relation.items())]
    content += '<div class="grid-2">'
    content += "<div><h3>Nodes by type</h3>" + html_table(["Type", "Count"], type_rows, ["", "num"]) + "</div>"
    content += "<div><h3>Edges by relation</h3>" + html_table(["Relation", "Count"], rel_rows, ["", "num"]) + "</div>"
    content += "</div>"

    # ── Top fan-out / fan-in ────────────────────────────────────────────────
    out_ranked = sorted(out_adj.items(), key=lambda x: -len(x[1]))[:10]
    in_ranked = sorted(in_adj.items(), key=lambda x: -len(x[1]))[:10]

    fan_out_rows = [
        [f'<code>{esc(name)}</code>', esc(type_by_name.get(name, "?")), str(len(es))]
        for name, es in out_ranked
    ]
    fan_in_rows = [
        [f'<code>{esc(name)}</code>', esc(type_by_name.get(name, "?")), str(len(es))]
        for name, es in in_ranked
    ]

    content += '<div class="grid-2">'
    content += (
        "<div><h3>Most-consumed (top fan-out)</h3>"
        + html_table(["Node", "Type", "Out-degree"], fan_out_rows, ["", "", "num"])
        + "<p class='hint'>Backbone — the most-read nodes. Changes here ripple downstream.</p></div>"
    )
    content += (
        "<div><h3>Heaviest dependencies (top fan-in)</h3>"
        + html_table(["Node", "Type", "In-degree"], fan_in_rows, ["", "", "num"])
        + "<p class='hint'>Nodes pulling from many sources. CIs at the top are integration points worth reviewing first.</p></div>"
    )
    content += "</div>"

    # ── Orphans by type ──────────────────────────────────────────────────────
    orphan_groups = {}
    for n in nodes:
        ntype = n["type"]
        name = n["name"]
        outgoing = len(out_adj.get(name, []))
        incoming = len(in_adj.get(name, []))
        is_orphan = False
        reason = ""
        if ntype in ("CI", "Segment", "Activation"):
            if incoming == 0 and outgoing == 0:
                is_orphan, reason = True, "isolated"
            elif incoming == 0:
                is_orphan, reason = True, "no upstream sources"
            elif outgoing == 0:
                is_orphan, reason = True, "nothing downstream consumes it"
        else:  # DMO, Stream
            if outgoing == 0:
                is_orphan, reason = True, "no downstream consumers"
        if is_orphan:
            orphan_groups.setdefault(ntype, []).append((name, reason, incoming, outgoing))

    if orphan_groups:
        content += "<h3>Orphans</h3>"
        content += "<p class='hint'>Nodes with missing upstream sources or no downstream consumers — abandoned-development candidates.</p>"
        for ntype in sorted(orphan_groups.keys()):
            items = orphan_groups[ntype]
            rows = [
                [f'<code>{esc(name)}</code>', esc(reason), str(incoming), str(outgoing)]
                for name, reason, incoming, outgoing in sorted(items)
            ]
            content += (
                f"<details><summary>{esc(ntype)} orphans ({len(items)})</summary>"
                + html_table(["Node", "Reason", "In", "Out"], rows, ["", "", "num", "num"])
                + "</details>"
            )

    # ── Unresolved-edge sample ───────────────────────────────────────────────
    if unresolved:
        # Collapse identical reasons
        reason_counts = {}
        for u in unresolved:
            r = u.get("reason", "(no reason)")
            reason_counts[r] = reason_counts.get(r, 0) + 1
        ur_rows = [[esc(r), str(c)] for r, c in sorted(reason_counts.items(), key=lambda x: -x[1])]
        content += "<h3>Unresolved edges</h3>"
        content += "<p class='hint'>Edges the graph couldn't resolve — typically the public API doesn't expose the linkage. Each row is one underlying gap, deduped.</p>"
        content += html_table(["Reason", "Count"], ur_rows, ["", "num"])

    return ("Lineage", content)


def build_transforms_tab(transforms):
    if not transforms:
        return None

    # Status donut
    t_statuses = {}
    pkg_count = 0
    for t in transforms:
        st = (t.get("status") or "UNKNOWN").upper()
        t_statuses[st] = t_statuses.get(st, 0) + 1
        if t.get("isPackageManaged"):
            pkg_count += 1

    content = ""
    if t_statuses:
        slices = []
        for st in sorted(t_statuses.keys()):
            color = STATUS_COLORS.get(st, "#6b7280")
            slices.append((st.title(), t_statuses[st], color))
        donut = svg_donut(slices, title="transforms")

        pkg_info = (
            f'<div style="margin-top:0.5rem;font-size:0.9em;color:var(--muted)">'
            f'Package-managed: {pkg_count} of {len(transforms)}</div>'
        )
        content += f'<div class="chart-row"><div>{donut}{pkg_info}</div></div>'

    rows = []
    for t in sorted(transforms, key=lambda x: x.get("name", "")):
        rows.append([
            f"<code>{esc(t.get('name', ''))}</code>",
            esc(t.get("label", "")),
            status_badge(t.get("status", "")),
            esc(t.get("type", "")),
            str(t.get("nodeCount", "")),
            "Yes" if t.get("isPackageManaged") else "No",
        ])

    content += html_table(
        ["Name", "Label", "Status", "Type", "Nodes", "Pkg Managed"],
        rows,
        ["", "", "", "", "num", ""],
    )
    return ("Transforms", content)


# ---------------------------------------------------------------------------
# Full HTML assembly
# ---------------------------------------------------------------------------

DASHBOARD_CSS = """\
:root {
  --bg: #ffffff;
  --fg: #1a1a1a;
  --muted: #6b7280;
  --border: #e5e7eb;
  --accent: #2563eb;
  --code-bg: #f3f4f6;
  --table-stripe: #f9fafb;
  --card-bg: #ffffff;
  --card-shadow: 0 1px 3px rgba(0,0,0,0.08);
  --tab-bg: #f3f4f6;
  --tab-active: #ffffff;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #111827;
    --fg: #f3f4f6;
    --muted: #9ca3af;
    --border: #374151;
    --accent: #60a5fa;
    --code-bg: #1f2937;
    --table-stripe: #1f2937;
    --card-bg: #1f2937;
    --card-shadow: 0 1px 3px rgba(0,0,0,0.3);
    --tab-bg: #1f2937;
    --tab-active: #374151;
  }
}
[data-theme="dark"] {
  --bg: #111827;
  --fg: #f3f4f6;
  --muted: #9ca3af;
  --border: #374151;
  --accent: #60a5fa;
  --code-bg: #1f2937;
  --table-stripe: #1f2937;
  --card-bg: #1f2937;
  --card-shadow: 0 1px 3px rgba(0,0,0,0.3);
  --tab-bg: #1f2937;
  --tab-active: #374151;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 15px;
  line-height: 1.6;
  color: var(--fg);
  background: var(--bg);
  max-width: 1400px;
  margin: 0 auto;
  padding: 1.5rem;
}
h1 {
  font-size: 1.6rem;
  margin-bottom: 0.25rem;
}
h1 + .meta {
  color: var(--muted);
  font-size: 0.9em;
  margin-bottom: 1rem;
}
.hint {
  color: var(--muted);
  font-size: 0.85em;
  margin: 0.25rem 0 0.75rem;
}
h3 { font-size: 1.1rem; margin: 1.2rem 0 0.5rem; }
h4 { font-size: 1rem; margin: 1rem 0 0.3rem; color: var(--muted); }
p { margin: 0.4rem 0; }
a { color: var(--accent); text-decoration: none; }
code {
  font-family: "SF Mono", "Fira Code", Menlo, Consolas, monospace;
  font-size: 0.88em;
  background: var(--code-bg);
  padding: 0.1em 0.3em;
  border-radius: 3px;
}
ul { margin: 0.3rem 0 0.3rem 1.5rem; }
li { margin: 0.15rem 0; }

/* Stat cards */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 0.75rem;
  margin: 1rem 0;
}
.stat-card {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem;
  text-align: center;
  box-shadow: var(--card-shadow);
}
.stat-number { font-size: 1.8rem; font-weight: 700; color: var(--accent); }
.stat-label { font-size: 0.85em; color: var(--muted); margin-top: 0.2rem; }

/* Tables */
table {
  width: 100%;
  border-collapse: collapse;
  margin: 0.75rem 0;
  font-size: 0.88em;
}
th, td {
  padding: 0.45rem 0.65rem;
  border: 1px solid var(--border);
  text-align: left;
}
th { background: var(--code-bg); font-weight: 600; }
tr:nth-child(even) { background: var(--table-stripe); }
td.num, th.num { text-align: right; }

/* CI audit cards */
.ci-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.75rem 1rem;
  margin: 0.75rem 0;
  background: var(--card-bg);
}
.ci-card h4 { margin: 0 0 0.5rem; color: var(--fg); }
.ci-card .clean { color: var(--muted); font-style: italic; }

/* Collapsibles */
details { margin: 0.5rem 0; }
summary {
  cursor: pointer;
  padding: 0.4rem 0;
  font-size: 0.95em;
}
summary:hover { color: var(--accent); }

/* Badges */
.badge { display: inline-block; white-space: nowrap; }

/* Charts */
.chart-container {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  margin: 1rem 0;
  flex-wrap: wrap;
}
.chart-row {
  display: flex;
  gap: 2rem;
  flex-wrap: wrap;
  margin: 1rem 0;
}
.chart-row > div { flex: 1; min-width: 280px; }
.legend {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
  font-size: 0.88em;
}
.legend-item { display: flex; align-items: center; gap: 0.4rem; }
.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}
.chart-scroll { overflow-x: auto; }

/* Lineage graph */
.ln-graph {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--card-bg);
  margin: 0.75rem 0 1rem;
  overflow: hidden;
}
.ln-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem 1rem;
  padding: 0.6rem 0.9rem;
  border-bottom: 1px solid var(--border);
  font-size: 0.85em;
  align-items: center;
}
.ln-filter {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  cursor: pointer;
  user-select: none;
}
.ln-swatch {
  width: 10px; height: 10px; border-radius: 50%;
  display: inline-block;
}
.ln-reset, .ln-fit {
  background: var(--code-bg);
  border: 1px solid var(--border);
  color: var(--fg);
  font-size: 0.85em;
  padding: 0.2rem 0.6rem;
  border-radius: 4px;
  cursor: pointer;
}
.ln-hint { color: var(--muted); margin-left: auto; }
.ln-filters-2, .ln-filters-3 {
  border-top: none;
  padding-top: 0.4rem;
  padding-bottom: 0.6rem;
}
.ln-search, .ln-direction, .ln-hops {
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--fg);
  font-size: 0.85em;
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
}
.ln-search { min-width: 240px; }
.ln-rel-label { color: var(--muted); font-size: 0.85em; }
.ln-clear-focus {
  background: var(--code-bg);
  border: 1px solid var(--border);
  color: var(--fg);
  font-size: 0.85em;
  padding: 0.2rem 0.6rem;
  border-radius: 4px;
  cursor: pointer;
}
/* Focus mode — hide everything outside the focus subgraph entirely */
.ln-svg.ln-focused .ln-node:not(.in-focus) { display: none; }
.ln-svg.ln-focused .ln-edge:not(.in-focus) { display: none; }
.ln-svg.ln-focused .ln-label:not(.in-focus) { display: none; }
.ln-svg.ln-focused .ln-node.in-focus.is-pinned .ln-dot {
  stroke: var(--accent); stroke-width: 2.5; r: 6;
}
.ln-svg.ln-focused .ln-label.in-focus { opacity: 1 !important; }

/* Hover tooltip — name + type, follows the cursor */
.ln-tooltip {
  position: absolute;
  pointer-events: none;
  background: var(--card-bg);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.3rem 0.5rem;
  font-size: 0.85em;
  box-shadow: var(--card-shadow);
  white-space: nowrap;
  z-index: 10;
  display: none;
}
.ln-tooltip.visible { display: block; }
.ln-tooltip .ln-tt-type {
  display: inline-block;
  margin-left: 0.4rem;
  padding: 0 0.4rem;
  border-radius: 8px;
  font-size: 0.85em;
  color: #fff;
}
.ln-graph-body { display: flex; align-items: stretch; }
.ln-inspector {
  width: 280px;
  flex-shrink: 0;
  display: none;
  flex-direction: column;
  border-left: 1px solid var(--border);
  background: var(--card-bg);
  font-size: 0.85rem;
  max-height: 600px;
  overflow: hidden;
}
.ln-inspector.visible { display: flex; }
.ln-ins-close {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1.1rem;
  color: var(--muted);
  line-height: 1;
  padding: 0.1rem 0.35rem;
  border-radius: 4px;
  flex-shrink: 0;
  align-self: flex-start;
}
.ln-ins-close:hover { background: var(--code-bg); color: var(--fg); }
.ln-ins-toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 0.4rem 0.5rem 0;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.ln-ins-content {
  overflow-y: auto;
  flex: 1;
  padding: 0.75rem 1rem 1rem;
}
.ln-ins-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
}
.ln-ins-name {
  font-weight: 600;
  font-size: 0.8rem;
  word-break: break-all;
  color: var(--fg);
}
.ln-ins-disp { color: var(--muted); font-size: 0.8rem; margin-top: 0.1rem; }
.ln-ins-badge {
  font-size: 0.7rem;
  padding: 0.15rem 0.45rem;
  border-radius: 999px;
  color: #fff;
  white-space: nowrap;
  flex-shrink: 0;
}
.ln-ins-status {
  display: inline-block;
  font-size: 0.75rem;
  padding: 0.1rem 0.4rem;
  border-radius: 4px;
  margin-bottom: 0.75rem;
}
.ln-ins-status-ok { background: #d1fae5; color: #065f46; }
.ln-ins-status-warn { background: #fef3c7; color: #92400e; }
[data-theme="dark"] .ln-ins-status-ok,
[data-theme="dark"] .ln-ins-status-warn { background: transparent; }
[data-theme="dark"] .ln-ins-status-ok { color: #6ee7b7; border: 1px solid #6ee7b7; }
[data-theme="dark"] .ln-ins-status-warn { color: #fcd34d; border: 1px solid #fcd34d; }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) .ln-ins-status-ok { background: transparent; color: #6ee7b7; border: 1px solid #6ee7b7; }
  :root:not([data-theme="light"]) .ln-ins-status-warn { background: transparent; color: #fcd34d; border: 1px solid #fcd34d; }
}
.ln-ins-facts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.4rem;
  margin-bottom: 0.75rem;
}
.ln-ins-fact {
  background: var(--code-bg);
  border-radius: 6px;
  padding: 0.35rem 0.5rem;
  display: flex;
  flex-direction: column;
}
.ln-ins-fact span:first-child { font-weight: 700; font-size: 1rem; color: var(--accent); }
.ln-ins-fact span:last-child { font-size: 0.7rem; color: var(--muted); margin-top: 0.1rem; }
.ln-ins-fact-full { font-size: 0.8rem; color: var(--muted); margin-bottom: 0.5rem; }
.ln-ins-conn {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
}
.ln-ins-conn-item {
  flex: 1;
  background: var(--code-bg);
  border-radius: 6px;
  padding: 0.35rem 0.5rem;
  font-size: 0.75rem;
  color: var(--muted);
  text-align: center;
}
.ln-ins-conn-count { font-weight: 700; font-size: 1rem; color: var(--fg); display: block; }
.ln-ins-section {
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  margin: 0.75rem 0 0.35rem;
}
.ln-ins-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.ln-ins-list li {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.78rem;
  color: var(--fg);
  word-break: break-all;
}
.ln-ins-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.ln-ins-more { color: var(--muted); font-size: 0.75rem; }
.ln-canvas {
  flex: 1;
  min-width: 0;
  height: 600px;
  overflow: hidden;
  cursor: grab;
  background:
    linear-gradient(var(--border) 1px, transparent 1px) 0 0 / 40px 40px,
    linear-gradient(90deg, var(--border) 1px, transparent 1px) 0 0 / 40px 40px;
  background-color: var(--bg);
  position: relative;
}
.ln-canvas.dragging { cursor: grabbing; }
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-right: 2rem;
}
.theme-toggle {
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: 50%;
  width: 2.2rem;
  height: 2.2rem;
  cursor: pointer;
  font-size: 1.1rem;
  color: var(--fg);
  flex-shrink: 0;
  transition: background 0.15s;
}
.theme-toggle:hover { background: var(--code-bg); }
.ln-hairball-hint {
  position: absolute;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  background: var(--card-bg);
  color: var(--muted);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.75rem 1.5rem;
  font-size: 0.9rem;
  pointer-events: none;
  z-index: 10;
  text-align: center;
  max-width: 360px;
  box-shadow: var(--card-shadow);
}
.ln-svg {
  width: 100%; height: 100%; display: block;
  transform-origin: 0 0;
  user-select: none;
}
.ln-edges-off .ln-edge { display: none; }
.ln-svg:not(.ln-focused) .ln-node,
.ln-svg:not(.ln-focused) .ln-label { display: none; }
.ln-node { cursor: pointer; }
.ln-node .ln-dot { transition: r 0.1s; }
.ln-node:hover .ln-dot { r: 6; }
.ln-node.dim, .ln-edge.dim, .ln-label.dim { opacity: 0.08; }
.ln-edge.hot { stroke: var(--accent); stroke-width: 1.4; opacity: 0.95; }
.ln-node.hot .ln-dot { r: 6; stroke: var(--accent); stroke-width: 2; }
.ln-label.hot { font-weight: 700; fill: var(--accent); opacity: 1 !important; }
.ln-label { cursor: pointer; transition: opacity 0.1s; }
/* Labels off by default on big graphs — re-shown on hover or highlight */
.ln-svg.ln-labels-off .ln-label { opacity: 0; }
.ln-svg.ln-labels-off .ln-label.hot,
.ln-svg.ln-labels-off .ln-label.hover { opacity: 1; }
.ln-type-hidden { display: none !important; }

/* ---- CSS-only tabs via radio buttons ---- */
.tabs { margin-top: 1rem; }
.tabs input[type="radio"] { display: none; }
.tab-labels {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  border-bottom: 2px solid var(--border);
  margin-bottom: 1rem;
}
.tab-labels label {
  padding: 0.6rem 1.2rem;
  cursor: pointer;
  font-weight: 500;
  font-size: 0.95em;
  color: var(--muted);
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  transition: color 0.15s, border-color 0.15s;
}
.tab-labels label:hover { color: var(--fg); }
.tab-panel { display: none; }

/* Activate selected tab — per-tab selectors are appended at render time */

/* Print: expand all tabs */
@media print {
  body { max-width: none; padding: 0; font-size: 12px; }
  .tab-labels { display: none; }
  .tab-panel { display: block !important; page-break-inside: avoid; margin-bottom: 2rem; }
  .tab-panel::before {
    content: attr(data-tab-title);
    display: block;
    font-size: 1.3rem;
    font-weight: 700;
    border-bottom: 2px solid var(--border);
    padding-bottom: 0.3rem;
    margin-bottom: 0.75rem;
  }
  .stat-card { box-shadow: none; border: 1px solid #ccc; }
}
"""


LINEAGE_JS = r"""
(function () {
  var TYPE_COLORS = {
    Stream: '#0891b2', DLO: '#0d9488', DMO: '#2563eb', CI: '#7c3aed',
    Segment: '#d97706', Activation: '#dc2626'
  };
  var NODE_META = (function () {
    var el = document.getElementById('ln-node-meta');
    try { return el ? JSON.parse(el.textContent) : {}; } catch (e) { return {}; }
  })();

  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  document.querySelectorAll('.ln-graph').forEach(function (root) {
    var canvas = root.querySelector('.ln-canvas');
    var svg = root.querySelector('.ln-svg');
    var tooltip = root.querySelector('.ln-tooltip');
    if (!svg || !canvas) return;

    // ── Pan + zoom via CSS transform on the SVG itself ────────────────────
    // tx/ty are pixel offsets in canvas coordinate space; scale is uniform.
    // Doing the math in pixels (rather than mixing canvas pixels with SVG
    // viewBox units) avoids the drift bug that broke pan-after-zoom.
    var tx = 0, ty = 0, scale = 1;
    function apply() {
      svg.style.transform =
        'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
    }

    function fit() { tx = 0; ty = 0; scale = 1; apply(); }

    // Prompt overlay — always visible until a node is focused.
    var _hairballHint = document.createElement('div');
    _hairballHint.className = 'ln-hairball-hint';
    _hairballHint.textContent = 'Type a node name in “Focus on node” above to explore lineage';
    canvas.appendChild(_hairballHint);

    // ── Drag-to-pan with a 4px threshold so a click isn't read as a drag ─
    var pressed = false, dragging = false;
    var pressX = 0, pressY = 0, startTx = 0, startTy = 0;
    var DRAG_THRESHOLD = 4;

    canvas.addEventListener('mousedown', function (e) {
      if (e.button !== 0) return;
      pressed = true;
      dragging = false;
      pressX = e.clientX; pressY = e.clientY;
      startTx = tx; startTy = ty;
    });
    window.addEventListener('mousemove', function (e) {
      if (!pressed) return;
      var dx = e.clientX - pressX;
      var dy = e.clientY - pressY;
      if (!dragging && Math.abs(dx) + Math.abs(dy) < DRAG_THRESHOLD) return;
      if (!dragging) {
        dragging = true;
        canvas.classList.add('dragging');
      }
      tx = startTx + dx;
      ty = startTy + dy;
      apply();
    });
    window.addEventListener('mouseup', function () {
      pressed = false;
      // Defer clearing `dragging` so the click handler sees it
      setTimeout(function () {
        dragging = false;
        canvas.classList.remove('dragging');
      }, 0);
    });

    // Zoom range — capped at 50× so tall graphs (e.g. 1000+ nodes where
    // the SVG fit-scale starts near 0.05) can reach legible label sizes.
    var MIN_SCALE = 0.1, MAX_SCALE = 50;
    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      var rect = canvas.getBoundingClientRect();
      var mx = e.clientX - rect.left;
      var my = e.clientY - rect.top;
      var factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      var newScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * factor));
      var ratio = newScale / scale;
      // Zoom around the cursor: keep (mx,my) fixed in canvas pixels.
      tx = mx - (mx - tx) * ratio;
      ty = my - (my - ty) * ratio;
      scale = newScale;
      apply();
    }, { passive: false });

    // ── Index nodes/labels/edges by name for fast lookup ─────────────────
    // Also capture each node's original position + DOM refs so focus mode
    // can relayout the surviving subgraph and restore on clear.
    var nodeIndex = {}, labelIndex = {};
    var nodePos = {};   // name → {origX, origY, x, y, hit, dot}
    var edgeRefs = [];  // [{el, from, to}]
    var outAdj = {}, inAdj = {};
    svg.querySelectorAll('.ln-node').forEach(function (n) {
      var name = n.getAttribute('data-name');
      nodeIndex[name] = n;
      var hit = n.querySelector('.ln-hit');
      var dot = n.querySelector('.ln-dot');
      if (hit && dot) {
        var x = parseFloat(hit.getAttribute('cx'));
        var y = parseFloat(hit.getAttribute('cy'));
        nodePos[name] = {
          origX: x, origY: y, x: x, y: y, hit: hit, dot: dot
        };
      }
    });
    svg.querySelectorAll('.ln-label').forEach(function (l) {
      var name = l.getAttribute('data-name');
      labelIndex[name] = l;
      // Capture the label's original (x, y) and its anchor side so we
      // can restore it precisely (first-column labels live left of the dot).
      var ox = parseFloat(l.getAttribute('x'));
      var oy = parseFloat(l.getAttribute('y'));
      l.setAttribute('data-orig-x', ox);
      l.setAttribute('data-orig-y', oy);
    });
    svg.querySelectorAll('.ln-edge').forEach(function (e) {
      var f = e.getAttribute('data-from');
      var t = e.getAttribute('data-to');
      var r = e.getAttribute('data-relation');
      e.setAttribute('data-orig-d', e.getAttribute('d'));
      edgeRefs.push({ el: e, from: f, to: t });
      (outAdj[f] = outAdj[f] || []).push({ to: t, relation: r, el: e });
      (inAdj[t] = inAdj[t] || []).push({ from: f, relation: r, el: e });
    });
    function nodeByName(n) { return nodeIndex[n] || null; }
    function labelByName(n) { return labelIndex[n] || null; }

    // ── Layout helpers — move nodes/labels/edges in SVG units ────────────
    function setNodePosition(name, x, y) {
      var p = nodePos[name];
      if (!p) return;
      p.x = x; p.y = y;
      p.hit.setAttribute('cx', x);
      p.hit.setAttribute('cy', y);
      p.dot.setAttribute('cx', x);
      p.dot.setAttribute('cy', y);
      var l = labelIndex[name];
      if (l) {
        // In focus relayout we always anchor labels to the right of the dot
        l.setAttribute('x', x + 8);
        l.setAttribute('y', y + 3);
        l.setAttribute('text-anchor', 'start');
      }
    }
    function setEdgePath(edge) {
      var pf = nodePos[edge.from], pt = nodePos[edge.to];
      if (!pf || !pt) return;
      var x1 = pf.x, y1 = pf.y, x2 = pt.x, y2 = pt.y;
      var cx = x1 + (x2 - x1) * 0.5;
      edge.el.setAttribute(
        'd',
        'M ' + x1.toFixed(1) + ' ' + y1.toFixed(1) +
        ' C ' + cx.toFixed(1) + ' ' + y1.toFixed(1) + ', ' +
        cx.toFixed(1) + ' ' + y2.toFixed(1) + ', ' +
        x2.toFixed(1) + ' ' + y2.toFixed(1)
      );
    }
    function relayoutFocus(inFocus) {
      // Group focused nodes by their column (origX), sort by name, then
      // pack each column tightly from the top with a fixed row height.
      var byCol = {};
      Object.keys(inFocus).forEach(function (name) {
        var p = nodePos[name];
        if (!p) return;
        var col = p.origX;
        (byCol[col] = byCol[col] || []).push(name);
      });
      Object.keys(byCol).forEach(function (c) { byCol[c].sort(); });

      var rowHeight = 22;
      var topPad = 60;
      var maxCount = 0;
      Object.keys(byCol).forEach(function (c) {
        if (byCol[c].length > maxCount) maxCount = byCol[c].length;
      });

      Object.keys(byCol).forEach(function (c) {
        var names = byCol[c];
        var colHeight = names.length * rowHeight;
        var startY = topPad + (maxCount * rowHeight - colHeight) / 2;
        names.forEach(function (nm, i) {
          setNodePosition(nm, parseFloat(c), startY + i * rowHeight);
        });
      });

      edgeRefs.forEach(function (er) {
        if (er.from in inFocus && er.to in inFocus) setEdgePath(er);
      });
    }
    function restoreLayout() {
      Object.keys(nodePos).forEach(function (name) {
        var p = nodePos[name];
        setNodePosition(name, p.origX, p.origY);
        var l = labelIndex[name];
        if (l) {
          l.setAttribute('x', l.getAttribute('data-orig-x'));
          l.setAttribute('y', l.getAttribute('data-orig-y'));
          // Heuristic: the original anchor was 'end' for first-column
          // (label left of dot, so x < dot x), 'start' otherwise.
          var lx = parseFloat(l.getAttribute('x'));
          l.setAttribute('text-anchor', lx < p.origX ? 'end' : 'start');
        }
      });
      edgeRefs.forEach(function (er) {
        er.el.setAttribute('d', er.el.getAttribute('data-orig-d'));
      });
    }

    // ── Type + relation filters (visibility) ─────────────────────────────
    function applyVisibilityFilters() {
      var hiddenTypes = {};
      root.querySelectorAll('input[data-ln-type]').forEach(function (cb) {
        if (!cb.checked) hiddenTypes[cb.getAttribute('data-ln-type')] = true;
      });
      var hiddenRels = {};
      root.querySelectorAll('input[data-ln-relation]').forEach(function (cb) {
        if (!cb.checked) hiddenRels[cb.getAttribute('data-ln-relation')] = true;
      });
      svg.querySelectorAll('.ln-node').forEach(function (n) {
        n.classList.toggle('ln-type-hidden',
          !!hiddenTypes[n.getAttribute('data-type')]);
      });
      svg.querySelectorAll('.ln-label').forEach(function (l) {
        var node = nodeByName(l.getAttribute('data-name'));
        l.classList.toggle('ln-type-hidden',
          !!(node && node.classList.contains('ln-type-hidden')));
      });
      svg.querySelectorAll('.ln-edge').forEach(function (e) {
        var f = nodeByName(e.getAttribute('data-from'));
        var t = nodeByName(e.getAttribute('data-to'));
        var typeHide = (f && f.classList.contains('ln-type-hidden')) ||
                       (t && t.classList.contains('ln-type-hidden'));
        var relHide = !!hiddenRels[e.getAttribute('data-relation')];
        e.classList.toggle('ln-type-hidden', typeHide || relHide);
      });
      // Refresh focus subgraph if active — relation filter changes its shape
      if (focusName) applyFocus();
    }
    root.querySelectorAll('input[data-ln-type]').forEach(function (cb) {
      cb.addEventListener('change', applyVisibilityFilters);
    });
    root.querySelectorAll('input[data-ln-relation]').forEach(function (cb) {
      cb.addEventListener('change', applyVisibilityFilters);
    });

    // ── Labels-on/off toggle ─────────────────────────────────────────────
    var labelToggle = root.querySelector('.ln-toggle-labels');
    if (labelToggle) {
      labelToggle.addEventListener('change', function () {
        svg.classList.toggle('ln-labels-off', !labelToggle.checked);
      });
    }
    var edgeToggle = root.querySelector('.ln-toggle-edges');
    if (edgeToggle) {
      edgeToggle.addEventListener('change', function () {
        svg.classList.toggle('ln-edges-off', !edgeToggle.checked);
      });
    }

    // ── Focus mode — walk the graph from one node ────────────────────────
    var focusName = null;
    var searchInput = root.querySelector('.ln-search');
    var dirSel = root.querySelector('.ln-direction');
    var hopsSel = root.querySelector('.ln-hops');
    var clearFocusBtn = root.querySelector('.ln-clear-focus');

    function isRelEnabled(rel) {
      var cb = root.querySelector(
        'input[data-ln-relation][data-ln-relation="' + cssAttr(rel) + '"]'
      );
      return !cb || cb.checked;
    }
    function isTypeEnabled(type) {
      var cb = root.querySelector(
        'input[data-ln-type][data-ln-type="' + cssAttr(type) + '"]'
      );
      return !cb || cb.checked;
    }

    function walkFocus(start, direction, maxHops) {
      // BFS that respects the current type and relation filters. Edges
      // crossing a hidden type or whose relation is unchecked don't count.
      var inFocus = {};
      var edgesInFocus = [];
      inFocus[start] = 0;
      var queue = [start];
      while (queue.length) {
        var cur = queue.shift();
        var depth = inFocus[cur];
        if (maxHops && depth >= maxHops) continue;

        var step = function (neighbor, edgeEl, rel) {
          var nbNode = nodeByName(neighbor);
          if (!nbNode) return;
          if (!isTypeEnabled(nbNode.getAttribute('data-type'))) return;
          if (!isRelEnabled(rel)) return;
          edgesInFocus.push(edgeEl);
          if (!(neighbor in inFocus)) {
            inFocus[neighbor] = depth + 1;
            queue.push(neighbor);
          }
        };
        if (direction !== 'up') {
          (outAdj[cur] || []).forEach(function (e) { step(e.to, e.el, e.relation); });
        }
        if (direction !== 'down') {
          (inAdj[cur] || []).forEach(function (e) { step(e.from, e.el, e.relation); });
        }
      }
      return { nodes: inFocus, edges: edgesInFocus };
    }

    function applyFocus() {
      if (!focusName || !nodeByName(focusName)) {
        clearFocus();
        return;
      }
      var direction = dirSel ? dirSel.value : 'both';
      var maxHops = hopsSel ? parseInt(hopsSel.value, 10) : 0;
      var sub = walkFocus(focusName, direction, maxHops);

      svg.classList.add('ln-focused');
      svg.querySelectorAll('.in-focus').forEach(function (el) {
        el.classList.remove('in-focus');
        el.classList.remove('is-pinned');
      });
      Object.keys(sub.nodes).forEach(function (name) {
        var n = nodeByName(name);
        if (n) n.classList.add('in-focus');
        var l = labelByName(name);
        if (l) l.classList.add('in-focus');
      });
      var pinned = nodeByName(focusName);
      if (pinned) pinned.classList.add('is-pinned');
      sub.edges.forEach(function (e) { e.classList.add('in-focus'); });

      relayoutFocus(sub.nodes);
      fitToFocus();
    }

    function fitToFocus() {
      // Read positions of the in-focus nodes from their hit-circle cx/cy
      // and solve for tx/ty/scale that maps that bbox to the canvas.
      var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      var count = 0;
      svg.querySelectorAll('.ln-node.in-focus').forEach(function (n) {
        var hit = n.querySelector('.ln-hit');
        if (!hit) return;
        var x = parseFloat(hit.getAttribute('cx'));
        var y = parseFloat(hit.getAttribute('cy'));
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
        count++;
      });
      if (count === 0) return;

      // SVG-unit padding — leaves room for labels and the dot itself
      minX -= 100; maxX += 100;
      minY -= 30;  maxY += 30;
      // Single-node bbox would be zero-area; give it a min width/height
      if (maxX - minX < 1) { minX -= 60; maxX += 60; }
      if (maxY - minY < 1) { minY -= 30; maxY += 30; }

      var vbW = parseFloat(svg.getAttribute('data-vb-w'));
      var vbH = parseFloat(svg.getAttribute('data-vb-h'));
      var rect = canvas.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;

      // Without any CSS transform, the SVG already fits the canvas via
      // preserveAspectRatio="xMidYMid meet". Compute that natural scale
      // and offsets so we can map SVG units → canvas pixels.
      var natural = Math.min(rect.width / vbW, rect.height / vbH);
      var natOffX = (rect.width - vbW * natural) / 2;
      var natOffY = (rect.height - vbH * natural) / 2;

      var pad = 30; // canvas-pixel padding inside the visible area
      var bboxPxW = (maxX - minX) * natural;
      var bboxPxH = (maxY - minY) * natural;
      var newScale = Math.min(
        (rect.width  - 2 * pad) / bboxPxW,
        (rect.height - 2 * pad) / bboxPxH
      );
      newScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, newScale));

      // Bbox center in canvas pixels (pre-transform), then translate so it
      // lands at the canvas center after scaling.
      var centerPxX = natOffX + ((minX + maxX) / 2) * natural;
      var centerPxY = natOffY + ((minY + maxY) / 2) * natural;

      scale = newScale;
      tx = rect.width  / 2 - scale * centerPxX;
      ty = rect.height / 2 - scale * centerPxY;
      apply();
    }

    function clearFocus() {
      focusName = null;
      svg.classList.remove('ln-focused');
      svg.querySelectorAll('.in-focus, .is-pinned').forEach(function (el) {
        el.classList.remove('in-focus');
        el.classList.remove('is-pinned');
      });
      restoreLayout();
      if (searchInput) { searchInput.value = ''; searchInput.style.outline = ''; }
      if (_hairballHint) _hairballHint.style.display = '';
      var ins = root.querySelector('.ln-inspector');
      if (ins) ins.classList.remove('visible');
    }

    function renderInspector(name) {
      var ins = root.querySelector('.ln-inspector');
      if (!ins) return;
      var meta = NODE_META[name] || {};
      var nodeEl = nodeByName(name);
      var type = nodeEl ? nodeEl.getAttribute('data-type') : (meta.type || '?');
      var color = TYPE_COLORS[type] || '#6b7280';
      var up = (inAdj[name] || []).map(function (e) { return e.from; });
      var down = (outAdj[name] || []).map(function (e) { return e.to; });

      var h = '<div class="ln-ins-header"><div>';
      h += '<div class="ln-ins-name">' + escHtml(name) + '</div>';
      if (meta.displayName && meta.displayName !== name) {
        h += '<div class="ln-ins-disp">' + escHtml(meta.displayName) + '</div>';
      }
      h += '</div><span class="ln-ins-badge" style="background:' + color + '">' + escHtml(type) + '</span></div>';

      if (meta.status) {
        var sc = meta.status === 'ACTIVE' ? 'ln-ins-status-ok' : 'ln-ins-status-warn';
        h += '<span class="ln-ins-status ' + sc + '">' + escHtml(meta.status) + '</span>';
      }

      h += '<div class="ln-ins-facts">';
      if (type === 'CI') {
        if (meta.dimensions != null) h += '<div class="ln-ins-fact"><span>' + meta.dimensions + '</span><span>dimensions</span></div>';
        if (meta.measures != null) h += '<div class="ln-ins-fact"><span>' + meta.measures + '</span><span>measures</span></div>';
      }
      if (type === 'DMO') {
        if (meta.fieldCount != null) h += '<div class="ln-ins-fact"><span>' + meta.fieldCount + '</span><span>fields</span></div>';
        if (meta.category) h += '<div class="ln-ins-fact"><span>' + escHtml(meta.category) + '</span><span>category</span></div>';
      }
      if (type === 'Stream') {
        if (meta.connectorType) h += '<div class="ln-ins-fact"><span>' + escHtml(meta.connectorType) + '</span><span>connector</span></div>';
        if (meta.totalRecords != null) h += '<div class="ln-ins-fact"><span>' + Number(meta.totalRecords).toLocaleString() + '</span><span>records</span></div>';
        if (meta.frequency) h += '<div class="ln-ins-fact"><span>' + escHtml(meta.frequency) + '</span><span>frequency</span></div>';
        if (meta.lastRunStatus) {
          var rc = meta.lastRunStatus === 'SUCCESS' ? 'ln-ins-status-ok' : 'ln-ins-status-warn';
          h += '<span class="ln-ins-status ' + rc + '">' + escHtml(meta.lastRunStatus) + '</span>';
        }
      }
      if (type === 'Segment' && meta.segmentOn) {
        h += '<div class="ln-ins-fact-full"><span>On: </span><code>' + escHtml(meta.segmentOn) + '</code></div>';
      }
      h += '</div>';

      h += '<div class="ln-ins-conn">';
      h += '<div class="ln-ins-conn-item"><span class="ln-ins-conn-count">' + up.length + '</span>upstream</div>';
      h += '<div class="ln-ins-conn-item"><span class="ln-ins-conn-count">' + down.length + '</span>downstream</div>';
      h += '</div>';

      function nodeList(names, label) {
        if (!names.length) return '';
        var s = '<div class="ln-ins-section">' + label + '</div><ul class="ln-ins-list">';
        var show = names.slice(0, 12);
        show.forEach(function (n) {
          var el2 = nodeByName(n);
          var t2 = el2 ? el2.getAttribute('data-type') : '';
          var c2 = TYPE_COLORS[t2] || '#6b7280';
          s += '<li><span class="ln-ins-dot" style="background:' + c2 + '"></span>' + escHtml(n) + '</li>';
        });
        if (names.length > 12) s += '<li class="ln-ins-more">+' + (names.length - 12) + ' more</li>';
        return s + '</ul>';
      }
      h += nodeList(up, 'Reads from');
      h += nodeList(down, 'Read by');

      var content = ins.querySelector('.ln-ins-content');
      if (content) content.innerHTML = h;
      ins.classList.add('visible');
    }

    function setFocus(name) {
      if (!nodeByName(name)) return;
      if (_hairballHint) { _hairballHint.style.display = 'none'; }
      focusName = name;
      if (searchInput) searchInput.value = name;
      applyFocus();
      renderInspector(name);
    }

    if (searchInput) {
      var commitSearch = function () {
        var v = searchInput.value.trim();
        if (!v) { clearFocus(); searchInput.style.outline = ''; return; }
        if (nodeByName(v)) {
          searchInput.style.outline = '';
          setFocus(v);
        } else {
          searchInput.style.outline = '2px solid #ef4444';
        }
      };
      searchInput.addEventListener('change', commitSearch);
      searchInput.addEventListener('input', commitSearch);
      searchInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { commitSearch(); searchInput.style.outline = ''; }
      });
    }
    if (dirSel) dirSel.addEventListener('change', function () { if (focusName) applyFocus(); });
    if (hopsSel) hopsSel.addEventListener('change', function () { if (focusName) applyFocus(); });
    if (clearFocusBtn) clearFocusBtn.addEventListener('click', clearFocus);
    var insCloseBtn = root.querySelector('.ln-ins-close');
    if (insCloseBtn) insCloseBtn.addEventListener('click', function () {
      var ins = root.querySelector('.ln-inspector');
      if (ins) ins.classList.remove('visible');
      if (focusName) requestAnimationFrame(fitToFocus);
    });

    // Click a node OR its label → set it as focus.
    // Click empty canvas / an edge → no-op (use "Clear focus" to exit).
    // This avoids losing focus when the user clicks just outside a tiny
    // dot on a packed graph.
    svg.addEventListener('click', function (e) {
      if (dragging) return;
      var nodeEl = e.target.closest && e.target.closest('.ln-node');
      var labelEl = e.target.closest && e.target.closest('.ln-label');
      var name = nodeEl ? nodeEl.getAttribute('data-name')
               : labelEl ? labelEl.getAttribute('data-name')
               : null;
      if (name) setFocus(name);
    });

    // Hover — show name + type tooltip, plus the label inline
    function showTooltip(name, type, clientX, clientY) {
      if (!tooltip) return;
      var color = TYPE_COLORS[type] || '#6b7280';
      tooltip.innerHTML =
        '<strong></strong><span class="ln-tt-type"></span>';
      tooltip.querySelector('strong').textContent = name;
      var chip = tooltip.querySelector('.ln-tt-type');
      chip.textContent = type;
      chip.style.background = color;
      moveTooltip(clientX, clientY);
      tooltip.classList.add('visible');
    }
    function moveTooltip(clientX, clientY) {
      if (!tooltip) return;
      var rect = canvas.getBoundingClientRect();
      var x = clientX - rect.left + 12;
      var y = clientY - rect.top + 12;
      // Keep inside canvas — flip left/up if too close to the right/bottom
      var ttW = tooltip.offsetWidth || 200;
      var ttH = tooltip.offsetHeight || 28;
      if (x + ttW > rect.width) x = clientX - rect.left - ttW - 12;
      if (y + ttH > rect.height) y = clientY - rect.top - ttH - 12;
      tooltip.style.left = x + 'px';
      tooltip.style.top = y + 'px';
    }
    function hideTooltip() {
      if (tooltip) tooltip.classList.remove('visible');
    }

    function targetName(e) {
      var nodeEl = e.target.closest && e.target.closest('.ln-node');
      if (nodeEl) return {
        name: nodeEl.getAttribute('data-name'),
        type: nodeEl.getAttribute('data-type'),
        node: nodeEl
      };
      var labelEl = e.target.closest && e.target.closest('.ln-label');
      if (labelEl) {
        var nm = labelEl.getAttribute('data-name');
        var n = nodeByName(nm);
        return {
          name: nm,
          type: n ? n.getAttribute('data-type') : '',
          node: n
        };
      }
      return null;
    }

    svg.addEventListener('mouseover', function (e) {
      var t = targetName(e);
      if (!t) return;
      var l = labelByName(t.name);
      if (l) l.classList.add('hover');
      showTooltip(t.name, t.type, e.clientX, e.clientY);
    });
    svg.addEventListener('mousemove', function (e) {
      if (!tooltip || !tooltip.classList.contains('visible')) return;
      var t = targetName(e);
      if (!t) { hideTooltip(); return; }
      moveTooltip(e.clientX, e.clientY);
    });
    svg.addEventListener('mouseout', function (e) {
      var t = targetName(e);
      if (!t) return;
      var l = labelByName(t.name);
      // Only drop the hover class if we're not just moving onto the
      // companion element (node ↔ label) for the same name
      var to = e.relatedTarget && e.relatedTarget.closest;
      var toNode = to && (e.relatedTarget.closest('.ln-node') ||
                          e.relatedTarget.closest('.ln-label'));
      var toName = toNode &&
                   (toNode.getAttribute('data-name') ||
                    toNode.getAttribute('data-name'));
      if (toName !== t.name) {
        if (l) l.classList.remove('hover');
        hideTooltip();
      }
    });

    // ── Buttons ──────────────────────────────────────────────────────────
    var reset = root.querySelector('.ln-reset');
    if (reset) {
      reset.addEventListener('click', function () {
        fit();
        clearFocus();
        root.querySelectorAll('input[data-ln-type]').forEach(function (cb) {
          cb.checked = true;
        });
        root.querySelectorAll('input[data-ln-relation]').forEach(function (cb) {
          cb.checked = true;
        });
        if (dirSel) dirSel.value = 'both';
        if (hopsSel) hopsSel.value = '1';
        applyVisibilityFilters();
      });
    }
    var fitBtn = root.querySelector('.ln-fit');
    if (fitBtn) {
      fitBtn.addEventListener('click', function () {
        if (focusName) fitToFocus(); else fit();
      });
    }

    // ── tiny helper for attribute selectors ──────────────────────────────
    function cssAttr(s) {
      return String(s).replace(/(["\\])/g, '\\$1');
    }
  });
})();
"""


def build_html(data_dir, client_name=None):
    """Build the full dashboard HTML string."""

    # Load data
    index_path = os.path.join(data_dir, "object-model", "index.yaml")
    if not os.path.isfile(index_path):
        print(f"Error: {index_path} not found. Run intake.py first.", file=sys.stderr)
        sys.exit(1)

    index_data = load_yaml(index_path)
    dmos = load_all_yaml(os.path.join(data_dir, "object-model", "dmos"))
    cis = load_all_yaml(os.path.join(data_dir, "object-model", "cis"))
    transforms = load_all_yaml(os.path.join(data_dir, "object-model", "transforms"))
    segments = load_all_yaml(os.path.join(data_dir, "object-model", "segments"))

    org_name = client_name or index_data.get("client", index_data.get("orgAlias", "Data Cloud"))

    # Build tabs
    tab_builders = [
        lambda: build_overview_tab(data_dir, index_data, dmos, cis, transforms, segments),
        lambda: build_architecture_tab(data_dir, dmos, cis, segments),
        lambda: build_ci_audit_tab(data_dir),
        lambda: build_ci_convert_tab(data_dir),
        lambda: build_clusters_tab(data_dir),
        lambda: build_lineage_tab(data_dir),
        lambda: build_diagram_tab(data_dir),
        lambda: build_transforms_tab(transforms),
    ]

    tabs = []
    for builder in tab_builders:
        result = builder()
        if result:
            tabs.append(result)

    if not tabs:
        print("Error: no data found to build dashboard.", file=sys.stderr)
        sys.exit(1)

    # Assemble HTML
    title = f"{org_name} — Data360 Analyst Dashboard"

    radios = ""
    labels = '<div class="tab-labels">'
    panels = '<div class="tab-panels">'

    label_selectors = []
    panel_selectors = []
    for i, (label, content) in enumerate(tabs):
        tid = f"tab{i+1}"
        checked = " checked" if i == 0 else ""
        radios += f'<input type="radio" name="tabs" id="{tid}"{checked}>\n'
        labels += f'<label for="{tid}">{esc(label)}</label>'
        panels += f'<div class="tab-panel" data-tab-title="{esc(label)}">{content}</div>\n'
        label_selectors.append(f'#{tid}:checked ~ .tab-labels label[for="{tid}"]')
        panel_selectors.append(f'#{tid}:checked ~ .tab-panels > .tab-panel:nth-child({i+1})')

    labels += "</div>"
    panels += "</div>"

    tab_css = (
        ",\n".join(label_selectors)
        + " {\n  color: var(--accent);\n  border-bottom-color: var(--accent);\n}\n"
        + ",\n".join(panel_selectors)
        + " {\n  display: block;\n}\n"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
{DASHBOARD_CSS}
{tab_css}
</style>
<script>(function(){{var t=localStorage.getItem('dashboard-theme');if(t)document.documentElement.setAttribute('data-theme',t);}})();</script>
</head>
<body>
<div class="page-header">
<h1>{esc(title)}</h1>
<button class="theme-toggle" id="themeToggle" title="Toggle light / dark mode">☀</button>
</div>
<div class="tabs">
{radios}
{labels}
{panels}
</div>
<script>{LINEAGE_JS}</script>
<script>
(function() {{
  var btn = document.getElementById('themeToggle');
  if (!btn) return;
  function isDark() {{
    var t = document.documentElement.getAttribute('data-theme');
    if (t === 'dark') return true;
    if (t === 'light') return false;
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  }}
  function sync() {{ btn.textContent = isDark() ? '☀' : '☾'; }}
  sync();
  btn.addEventListener('click', function() {{
    var next = isDark() ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('dashboard-theme', next);
    sync();
  }});
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', sync);
}})();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate a Data Cloud dashboard from data360-analyst output."
    )
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Path to the client's Data360/ folder (containing object-model/ and reports/)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML path (default: <data-dir>/reports/dashboard.html)",
    )
    parser.add_argument(
        "--client",
        default=None,
        help="Client name for the dashboard title (default: from index.yaml)",
    )
    args = parser.parse_args()

    data_dir = os.path.expanduser(args.data_dir)
    if not os.path.isdir(data_dir):
        print(f"Error: {data_dir} is not a directory.", file=sys.stderr)
        sys.exit(1)

    output = args.output
    if not output:
        reports_dir = os.path.join(data_dir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        output = os.path.join(reports_dir, "dashboard.html")

    output = os.path.expanduser(output)
    html_content = build_html(data_dir, args.client)

    with open(output, "w") as f:
        f.write(html_content)

    print(f"Dashboard written to {output}")


if __name__ == "__main__":
    main()
