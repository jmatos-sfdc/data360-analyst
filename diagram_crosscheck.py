#!/usr/bin/env python3
"""Cross-check a Lucidchart architecture diagram against an org's on-disk inventory.

Useful when a colleague maintains a "canonical" diagram of CIs / DMOs / transforms.
This finds: (a) artifacts in the diagram that don't exist on disk (broken refs, renames),
and (b) artifacts on disk that aren't referenced anywhere in the diagram (likely
dev-trail / abandoned / out-of-scope cleanup candidates).

Usage:
    python3 diagram_crosscheck.py \\
        --diagram <path-to-lucid-document.json> \\
        --output-dir <path-to-data360-folder>

The Lucid JSON is the "Document JSON" export (File → Export → Document JSON).

Inputs:
    Lucid JSON file (any version)
    <output-dir>/object-model/{cis,dmos,transforms}/*.yaml  (from intake.py)

Output:
    <output-dir>/reports/diagram-crosscheck.md

Reliability:
- Name-matching is heuristic. The matcher handles `__cio`/`__dlm` suffixes,
  `CI:`/`DMO:`/`Object:`/`Transform:` prefixes, and substring/equality fuzzy match.
- It does NOT handle abbreviation-style aliases (e.g. `CustLTV↔Customer_Lifetime_Value`).
  Add per-engagement substitutions below if your diagram uses abbreviations.
- MermaidDiagramBlock shapes (rendered as base64-SVG) ARE parsed for text labels.
"""

import argparse
import base64
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


CIO = re.compile(r"\b([A-Za-z_][A-Za-z0-9_ ]*?)__cio\b")
DLM = re.compile(r"\b([A-Za-z_][A-Za-z0-9_ ]*?)__dlm\b")
DLO = re.compile(r"\b([A-Za-z_][A-Za-z0-9_ ]*?)__dll\b")
SVG_TEXT = re.compile(r"<text[^>]*>(.*?)</text>", re.DOTALL)
TAG_STRIP = re.compile(r"<[^>]+>")
PREFIX_STRIP = re.compile(r"^(CI[:\s]+|DMO[:\s]+|Object[:\s]+|Transform[:\s]+)", re.IGNORECASE)


def loose(name):
    base = re.sub(r"__(cio|dlm|dll)$", "", name)
    return re.sub(r"[^a-z0-9]", "", base.lower())


def text_chunks(shape_or_line):
    out = []
    ta = shape_or_line.get("textAreas")
    if isinstance(ta, list):
        for entry in ta:
            if isinstance(entry, dict):
                t = entry.get("text")
                if t: out.append(t)
    elif isinstance(ta, dict):
        for v in ta.values():
            if isinstance(v, dict):
                t = v.get("text")
                if t: out.append(t)
            elif isinstance(v, str):
                out.append(v)
    img = shape_or_line.get("image")
    if isinstance(img, dict):
        url = img.get("url", "")
        if url.startswith("data:image/svg+xml;base64,"):
            try:
                svg = base64.b64decode(url.split(",", 1)[1]).decode("utf-8", errors="ignore")
                for m in SVG_TEXT.finditer(svg):
                    inner = TAG_STRIP.sub("", m.group(1)).strip()
                    if inner:
                        out.append(inner)
            except Exception:
                pass
    return out


def make_classifier(ci_loose, dmo_loose, xform_loose):
    def classify(text):
        if not text:
            return (None, None)
        raw = PREFIX_STRIP.sub("", text.strip()).strip()

        for m in CIO.finditer(raw):
            candidate = m.group(0)
            cl = loose(candidate)
            if cl in ci_loose:
                return ("ci", ci_loose[cl])
            return ("ci-missing", candidate)
        for m in DLM.finditer(raw):
            candidate = m.group(0)
            cl = loose(candidate)
            if cl in dmo_loose:
                return ("dmo", dmo_loose[cl])
            return ("dmo-missing", candidate)
        for m in DLO.finditer(raw):
            return ("dlo", m.group(0))

        cl = loose(raw)
        if cl in ci_loose:
            return ("ci", ci_loose[cl])
        if cl in dmo_loose:
            return ("dmo", dmo_loose[cl])
        if cl in xform_loose:
            return ("xform", xform_loose[cl])

        if 4 <= len(raw) <= 120 and re.match(r"^[A-Za-z][A-Za-z0-9_/&,() \-]*$", raw):
            for lk, name in ci_loose.items():
                if lk == cl or (cl and lk.endswith(cl)) or (lk and cl.endswith(lk)):
                    return ("ci-fuzzy", name)
            for lk, name in dmo_loose.items():
                if lk == cl or (cl and lk.endswith(cl)) or (lk and cl.endswith(lk)):
                    return ("dmo-fuzzy", name)
            return ("unknown-label", raw)
        return (None, None)
    return classify


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--diagram", required=True, help="Lucid Document JSON file")
    parser.add_argument("--output-dir", required=True, help="Per-client Data360 folder")
    args = parser.parse_args()

    diagram_path = Path(args.diagram).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    om = output_dir / "object-model"
    if not om.exists():
        print(f"ERROR: {om} not found. Run intake.py first.")
        sys.exit(1)
    if not diagram_path.exists():
        print(f"ERROR: {diagram_path} not found.")
        sys.exit(1)

    disk_cis = {p.stem.lower(): p.stem for p in (om / "cis").glob("*.yaml")}
    disk_dmos = {p.stem.lower(): p.stem for p in (om / "dmos").glob("*.yaml")}
    disk_xforms = {p.stem.lower(): p.stem for p in (om / "transforms").glob("*.yaml")}
    ci_loose = {loose(n): n for n in disk_cis.values()}
    dmo_loose = {loose(n): n for n in disk_dmos.values()}
    xform_loose = {loose(n): n for n in disk_xforms.values()}
    classify = make_classifier(ci_loose, dmo_loose, xform_loose)

    with diagram_path.open() as f:
        diagram = json.load(f)

    pages_summary = []
    all_diagram_cis = set()
    all_diagram_dmos = set()
    all_diagram_xforms = set()

    for i, page in enumerate(diagram.get("pages", [])):
        title = page.get("title", f"Page {i}")
        items = page.get("items", {})
        shapes = items.get("shapes", [])
        lines = items.get("lines", [])
        page_cis, page_dmos, page_xforms = set(), set(), set()
        page_missing_cis, page_missing_dmos = set(), set()
        for sh in shapes + lines:
            for txt in text_chunks(sh):
                kind, name = classify(txt)
                if kind in ("ci", "ci-fuzzy"):
                    page_cis.add(name); all_diagram_cis.add(name)
                elif kind == "ci-missing":
                    page_missing_cis.add(name)
                elif kind in ("dmo", "dmo-fuzzy"):
                    page_dmos.add(name); all_diagram_dmos.add(name)
                elif kind == "dmo-missing":
                    page_missing_dmos.add(name)
                elif kind == "xform":
                    page_xforms.add(name); all_diagram_xforms.add(name)
        pages_summary.append({
            "title": title, "shapes": len(shapes), "lines": len(lines),
            "cis": sorted(page_cis), "dmos": sorted(page_dmos), "xforms": sorted(page_xforms),
            "missing_cis": sorted(page_missing_cis), "missing_dmos": sorted(page_missing_dmos),
        })

    # ── Render report ─────────────────────────────────────────────────────────
    client = output_dir.parent.name
    out = []
    out.append(f"# {client} — Diagram vs Org Cross-Check\n")
    out.append(f"_Source: `{diagram_path.name}` (Lucidchart export). Cross-checked against on-disk intake snapshot ({len(disk_cis)} CIs, {len(disk_dmos)} DMOs, {len(disk_xforms)} transforms)._\n")
    out.append("> **Reliability caveat:** name-matching is heuristic. Diagrams typically use human-friendly abbreviations (e.g. `Cust LTV`) while disk uses API names (`Customer_Lifetime_Value__cio`). The matcher handles `__cio`/`__dlm` suffixes, `CI:`/`DMO:` prefixes, and substring/equality fuzzy match — but **abbreviation-style aliases are NOT handled** and will produce false 'not in diagram' positives. Treat the 'not in diagram' list as a *starting point* for cleanup discussion, not as authoritative.\n")

    out.append("## Summary\n")
    out.append(f"- Diagram has **{len(diagram.get('pages', []))} pages**")
    out.append(f"- Diagram references **{len(all_diagram_cis)} CIs** that exist on disk")
    out.append(f"- Diagram references **{len(all_diagram_dmos)} DMOs** that exist on disk")
    out.append(f"- Diagram references **{len(all_diagram_xforms)} Data Transforms** that exist on disk\n")

    on_disk_not_in_diagram = sorted(set(disk_cis.values()) - all_diagram_cis)
    out.append(f"## CIs on disk but NOT referenced anywhere in the diagram ({len(on_disk_not_in_diagram)})\n")
    out.append("Strong candidates for **'not on the canonical path'** — likely dev/test/abandoned, or out of diagram scope.\n")
    buckets = defaultdict(list)
    for n in on_disk_not_in_diagram:
        if "test" in n.lower() and not n.lower().startswith("ssot"): buckets["Test/POC"].append(n)
        elif "_CLONE" in n: buckets["*_CLONE"].append(n)
        elif n.startswith("POC_"): buckets["Test/POC"].append(n)
        elif n.lower().startswith("b2c_") or n.startswith("Consumer_"): buckets["b2c_/Consumer_*"].append(n)
        else: buckets["Other"].append(n)
    for label in ["Test/POC", "*_CLONE", "b2c_/Consumer_*", "Other"]:
        items = buckets.get(label, [])
        if items:
            out.append(f"### {label} ({len(items)})\n")
            for n in items:
                out.append(f"- `{n}`")
            out.append("")

    out.append("## Per-page references\n")
    out.append("| # | Page | Shapes | CIs | DMOs | Transforms |")
    out.append("|--:|------|------:|----:|-----:|-----------:|")
    for i, p in enumerate(pages_summary):
        out.append(f"| {i} | {p['title']} | {p['shapes']} | {len(p['cis'])} | {len(p['dmos'])} | {len(p['xforms'])} |")
    out.append("")

    for i, p in enumerate(pages_summary):
        if not (p['cis'] or p['dmos'] or p['xforms'] or p['missing_cis'] or p['missing_dmos']):
            continue
        out.append(f"### Page {i}: {p['title']}\n")
        if p['cis']:
            out.append(f"**CIs ({len(p['cis'])}):**")
            for n in p['cis']: out.append(f"- `{n}`")
            out.append("")
        if p['dmos']:
            out.append(f"**DMOs ({len(p['dmos'])}):**")
            for n in p['dmos']: out.append(f"- `{n}`")
            out.append("")
        if p['xforms']:
            out.append(f"**Data Transforms ({len(p['xforms'])}):**")
            for n in p['xforms']: out.append(f"- `{n}`")
            out.append("")
        if p['missing_cis']:
            out.append(f"**⚠ CIs in diagram but NOT on disk ({len(p['missing_cis'])}):**")
            for n in p['missing_cis']: out.append(f"- `{n}`")
            out.append("")
        if p['missing_dmos']:
            out.append(f"**⚠ DMOs in diagram but NOT on disk ({len(p['missing_dmos'])}):**")
            for n in p['missing_dmos']: out.append(f"- `{n}`")
            out.append("")

    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / "diagram-crosscheck.md"
    out_path.write_text("\n".join(out))
    print(f"Wrote {out_path}\n")
    print(f"Diagram→disk: {len(all_diagram_cis)} CIs, {len(all_diagram_dmos)} DMOs, {len(all_diagram_xforms)} transforms")
    print(f"CIs on disk but NOT in diagram: {len(on_disk_not_in_diagram)} of {len(disk_cis)}")


if __name__ == "__main__":
    main()
