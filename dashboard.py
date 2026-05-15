#!/usr/bin/env python3
"""Generate a single-page HTML dashboard from data360-analyst output.

Reads the YAML sidecars, markdown reports, and SQL files produced by
intake.py / dmo_graph.py / ci_audit.py / cluster_cis_by_dmo.py /
diagram_crosscheck.py and assembles a tabbed, self-contained HTML page
with no external dependencies.

Usage:
    python dashboard.py --data-dir ~/Projects/clients/Acme/data360 \
                        --output   ~/Projects/clients/Acme/data360/reports/dashboard.html
"""

import argparse
import glob
import html
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


def build_ci_audit_tab(data_dir):
    report = read_report(data_dir, "ci-audit.md")
    if not report:
        return None

    content = ""

    # Parse per-CI sections
    sections = re.split(r"^## ", report, flags=re.MULTILINE)
    ci_cards = []
    for section in sections[1:]:  # skip preamble
        lines = section.splitlines()
        ci_name = lines[0].strip()

        # Find findings table
        findings = []
        in_findings = False
        for line in lines:
            if "| #" in line and "Severity" in line:
                in_findings = True
                continue
            if in_findings and line.startswith("|--"):
                continue
            if in_findings and line.startswith("|"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) >= 3:
                    findings.append(cells)
            elif in_findings and not line.startswith("|"):
                in_findings = False

        # Find field formulas table
        formulas = []
        in_formulas = False
        for line in lines:
            if "| Field" in line and "Formula" in line:
                in_formulas = True
                continue
            if in_formulas and line.startswith("|--"):
                continue
            if in_formulas and line.startswith("|"):
                cells = [c.strip() for c in line.split("|")[1:-1]]
                if len(cells) >= 2:
                    formulas.append(cells)
            elif in_formulas and not line.startswith("|"):
                in_formulas = False

        card = f'<div class="ci-card"><h4>{esc(ci_name)}</h4>'

        if findings:
            severity_map = {"low": "green", "medium": "yellow", "high": "red", "info": "blue"}
            rows = []
            for f in findings:
                sev_text = re.sub(r"[^\w\s-]", "", f[1]).strip().lower()
                variant = "default"
                for k, v in severity_map.items():
                    if k in sev_text:
                        variant = v
                        break
                rows.append([f[0], badge(f[1].strip(), variant), f[2]])
            card += html_table(["#", "Severity", "Finding"], rows)
        else:
            card += '<p class="clean">No findings</p>'

        if formulas:
            card += "<details><summary>Field formulas</summary>"
            f_rows = [[f"<code>{esc(f[0].strip('`'))}</code>", f"<code>{esc(f[1].strip('`'))}</code>"] for f in formulas]
            card += html_table(["Field", "Formula"], f_rows)
            card += "</details>"

        card += "</div>"
        ci_cards.append((ci_name, card, findings))

    if ci_cards:
        # Summary counts
        total_findings = sum(len(f) for _, _, f in ci_cards)
        cis_with = sum(1 for _, _, f in ci_cards if f)
        cis_clean = sum(1 for _, _, f in ci_cards if not f)
        content += (
            f'<div class="stat-grid">'
            f'<div class="stat-card"><div class="stat-number">{len(ci_cards)}</div>'
            f'<div class="stat-label">CIs Audited</div></div>'
            f'<div class="stat-card"><div class="stat-number">{total_findings}</div>'
            f'<div class="stat-label">Total Findings</div></div>'
            f'<div class="stat-card"><div class="stat-number">{cis_with}</div>'
            f'<div class="stat-label">CIs with Findings</div></div>'
            f'<div class="stat-card"><div class="stat-number">{cis_clean}</div>'
            f'<div class="stat-label">Clean CIs</div></div>'
            f"</div>"
        )

        # Severity donut across all findings
        sev_counts = {}
        for _, _, findings in ci_cards:
            for f in findings:
                sev_raw = re.sub(r"[^\w\s-]", "", f[1]).strip().lower()
                for key in ("high", "medium", "low", "info"):
                    if key in sev_raw:
                        sev_counts[key] = sev_counts.get(key, 0) + 1
                        break
                else:
                    sev_counts["other"] = sev_counts.get("other", 0) + 1

        if sev_counts:
            sev_order = ["high", "medium", "low", "info", "other"]
            sev_slices = []
            for s in sev_order:
                if s in sev_counts:
                    color = SEVERITY_COLORS.get(s, "#6b7280")
                    sev_slices.append((s.title(), sev_counts[s], color))
            content += svg_donut(sev_slices, title="findings")

        for _, card, _ in ci_cards:
            content += card

    return ("CI Audit", content) if content else None


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
  :root {
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
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 15px;
  line-height: 1.6;
  color: var(--fg);
  background: var(--bg);
  max-width: 1100px;
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

/* Activate selected tab */
#tab1:checked ~ .tab-labels label[for="tab1"],
#tab2:checked ~ .tab-labels label[for="tab2"],
#tab3:checked ~ .tab-labels label[for="tab3"],
#tab4:checked ~ .tab-labels label[for="tab4"],
#tab5:checked ~ .tab-labels label[for="tab5"],
#tab6:checked ~ .tab-labels label[for="tab6"] {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
#tab1:checked ~ .tab-panels > .tab-panel:nth-child(1),
#tab2:checked ~ .tab-panels > .tab-panel:nth-child(2),
#tab3:checked ~ .tab-panels > .tab-panel:nth-child(3),
#tab4:checked ~ .tab-panels > .tab-panel:nth-child(4),
#tab5:checked ~ .tab-panels > .tab-panel:nth-child(5),
#tab6:checked ~ .tab-panels > .tab-panel:nth-child(6) {
  display: block;
}

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
        lambda: build_clusters_tab(data_dir),
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

    for i, (label, content) in enumerate(tabs):
        tid = f"tab{i+1}"
        checked = " checked" if i == 0 else ""
        radios += f'<input type="radio" name="tabs" id="{tid}"{checked}>\n'
        labels += f'<label for="{tid}">{esc(label)}</label>'
        panels += f'<div class="tab-panel" data-tab-title="{esc(label)}">{content}</div>\n'

    labels += "</div>"
    panels += "</div>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
{DASHBOARD_CSS}
</style>
</head>
<body>
<h1>{esc(title)}</h1>
<div class="tabs">
{radios}
{labels}
{panels}
</div>
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
        help="Path to the client's data360/ folder (containing object-model/ and reports/)",
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
