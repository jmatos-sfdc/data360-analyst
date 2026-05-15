#!/usr/bin/env python3
"""DMO centrality analysis for a Salesforce Data 360 org.

Identifies the backbone DMOs of an org by counting fan-in from CIs (parsed from
`expression` SQL) and segments. Streams are listed but lineage is not graphed
(DLO→DMO mapping is not exposed by the public API).

Usage:
    python3 dmo_graph.py --org <alias> --output-dir <path-to-data360-folder>

Inputs (must already exist under --output-dir):
    object-model/dmos/*.yaml
    object-model/segments/*.yaml

Output:
    <output-dir>/reports/dmo-graph.md
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install -r requirements.txt")
    sys.exit(1)


DMO_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__dlm)\b")
CIO_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__cio)\b")


def token_and_url(org_alias):
    result = subprocess.run(
        ["sf", "org", "display", "--target-org", org_alias, "--json"],
        capture_output=True, text=True, check=True,
    )
    d = json.loads(result.stdout)["result"]
    return d["accessToken"], d["instanceUrl"]


def api_get(token, url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def fetch_all_cis(token, base):
    items, offset = [], 0
    while True:
        d = api_get(token, f"{base}/ssot/calculated-insights?batchSize=25&offset={offset}")
        batch = d.get("collection", {}).get("items", [])
        if not batch:
            break
        items.extend(batch)
        if not d.get("collection", {}).get("nextPageToken"):
            break
        offset += 25
    return items


def fetch_streams(token, base):
    streams, offset = [], 0
    while True:
        d = api_get(token, f"{base}/ssot/data-streams?limit=100&offset={offset}")
        batch = d.get("dataStreams", [])
        if not batch:
            break
        streams.extend(batch)
        if not d.get("nextPageUrl"):
            break
        offset += 100
    return streams


def dmos_referenced(sql):
    if not sql:
        return set(), set()
    return set(DMO_PATTERN.findall(sql)), set(CIO_PATTERN.findall(sql))


def load_segments(om_dir):
    seg_on = Counter()
    seg_detail = defaultdict(list)
    for p in (om_dir / "segments").glob("*.yaml"):
        s = yaml.safe_load(p.read_text())
        dmo = s.get("segmentOnApiName")
        if dmo:
            seg_on[dmo] += 1
            seg_detail[dmo].append(s.get("apiName"))
    return seg_on, seg_detail


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--org", required=True, help="Salesforce CLI org alias")
    parser.add_argument("--output-dir", required=True, help="Per-client Data360 folder (must contain object-model/)")
    parser.add_argument("--api-version", default="v64.0")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser()
    om_dir = output_dir / "object-model"
    if not om_dir.exists():
        print(f"ERROR: {om_dir} not found. Run intake.py first.")
        sys.exit(1)

    print(f"Authenticating as '{args.org}'...")
    token, url = token_and_url(args.org)
    base = f"{url}/services/data/{args.api_version}"

    print("Loading DMO index...")
    known_dmos = {p.stem for p in (om_dir / "dmos").glob("*.yaml")}
    print(f"  {len(known_dmos)} DMO sidecars")

    print("Fetching CIs (with expression bodies)...")
    cis = fetch_all_cis(token, base)
    print(f"  {len(cis)} CIs")

    print("Loading segments...")
    seg_on, seg_detail = load_segments(om_dir)
    print(f"  {sum(seg_on.values())} segment→DMO edges across {len(seg_on)} DMOs")

    print("Fetching streams...")
    streams = fetch_streams(token, base)
    print(f"  {len(streams)} streams")
    # Stream → DMO lineage is NOT exposed via the public API — streams feed DLOs (*__dll),
    # and the DLO→DMO mapping is only configured/visible in Setup UI. We skip the column
    # rather than publish a misleading name-match heuristic.

    # ── Count CI fan-out (CI references DMO) ──────────────────────────────────
    ci_reads = Counter()
    ci_reads_detail = defaultdict(list)
    ci_with_no_sql = 0
    for ci in cis:
        name = ci.get("apiName")
        sql = ci.get("expression") or ""
        if not sql:
            ci_with_no_sql += 1
        dmos, _ = dmos_referenced(sql)
        for d in dmos:
            ci_reads[d] += 1
            ci_reads_detail[d].append(name)

    # ── CI→CI dependency (finds "derived" CIs that read other CIs) ────────────
    ci_to_ci = defaultdict(set)
    for ci in cis:
        name = ci.get("apiName")
        sql = ci.get("expression") or ""
        _, cios = dmos_referenced(sql)
        cios.discard(name)
        for c in cios:
            ci_to_ci[c].add(name)

    # ── Rank backbone DMOs ────────────────────────────────────────────────────
    all_dmos = set(ci_reads) | set(seg_on)
    ranked = []
    for d in all_dmos:
        total = ci_reads[d] + seg_on[d]
        ranked.append((d, ci_reads[d], seg_on[d], total))
    ranked.sort(key=lambda x: (-x[3], x[0]))

    # ── Write report ──────────────────────────────────────────────────────────
    client = output_dir.parent.name
    out = []
    out.append(f"# {client} — DMO Graph\n")
    out.append(f"_Generated from intake snapshot. {len(known_dmos)} DMO sidecars • {len(cis)} CIs ({ci_with_no_sql} with no `expression`) • {sum(seg_on.values())} segment→DMO edges • {len(streams)} streams (lineage not exposed by API — see note below)._\n")
    out.append("> **Stream→DMO lineage is NOT included.** Streams feed DLOs (`*__dll`); the DLO→DMO mapping is configured/visible only in the Setup UI. Name-match heuristics typically yield <10% coverage so the signal would be actively misleading. Rankings are based on CI reads + segment usage only.\n")

    out.append("## Ranking\n")
    out.append("`Total = CIs-read-from + segments-built-on`.\n")
    out.append("| DMO | CIs | Segments | Total |")
    out.append("|-----|----:|---------:|------:|")
    for d, ci, seg, tot in ranked[:40]:
        out.append(f"| `{d}` | {ci} | {seg} | **{tot}** |")
    out.append("")

    out.append(f"<details><summary>Full list ({len(ranked)} DMOs with any edge)</summary>\n")
    out.append("| DMO | CIs | Segments | Total |")
    out.append("|-----|----:|---------:|------:|")
    for d, ci, seg, tot in ranked:
        out.append(f"| `{d}` | {ci} | {seg} | {tot} |")
    out.append("\n</details>\n")

    out.append("## Backbone detail (top 10)\n")
    for d, ci, seg, tot in ranked[:10]:
        out.append(f"### `{d}` — total {tot}")
        if ci:
            sample = sorted(ci_reads_detail[d])[:8]
            out.append(f"- **{ci} CIs** read from it: {', '.join(f'`{c}`' for c in sample)}{' …' if ci > 8 else ''}")
        if seg:
            sample = sorted(seg_detail[d])[:8]
            out.append(f"- **{seg} segments** built on it: {', '.join(f'`{c}`' for c in sample)}{' …' if seg > 8 else ''}")
        out.append("")

    out.append("## CI → CI dependencies\n")
    out.append("CIs that other CIs read from (treating CIs as derived data sources):\n")
    out.append("| CI | Consumed by |")
    out.append("|----|-------------|")
    for c in sorted(ci_to_ci, key=lambda k: -len(ci_to_ci[k])):
        consumers = sorted(ci_to_ci[c])
        out.append(f"| `{c}` | {len(consumers)} — {', '.join(f'`{x}`' for x in consumers[:5])}{' …' if len(consumers) > 5 else ''} |")
    out.append("")

    if ci_with_no_sql:
        zombies = [ci.get("apiName") for ci in cis if not (ci.get("expression") or "")]
        out.append(f"## CIs with no `expression` ({len(zombies)})\n")
        out.append("These may be Auto Cloud / package-managed, ML-based, or have definitions stored elsewhere:\n")
        for z in sorted(zombies):
            out.append(f"- `{z}`")
        out.append("")

    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "dmo-graph.md"
    report_path.write_text("\n".join(out))
    print(f"\nWrote {report_path}")
    print(f"Top 5 backbone DMOs:")
    for d, ci, seg, tot in ranked[:5]:
        print(f"  {d}: CIs={ci}  segs={seg}  total={tot}")


if __name__ == "__main__":
    main()
