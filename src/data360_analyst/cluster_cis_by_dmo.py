#!/usr/bin/env python3
"""Analyze the CIs that read from a specific DMO.

Useful when you've identified a backbone DMO (via dmo_graph.py) and want to
understand the cluster of CIs built on top of it: naming patterns, join
co-references, common output measures, and CI-on-CI dependencies.

Usage:
    python3 cluster_cis_by_dmo.py \\
        --org <alias> \\
        --output-dir <path-to-data360-folder> \\
        --dmo <DMO_API_NAME>

Example:
    python3 cluster_cis_by_dmo.py --org acme --output-dir ~/Projects/clients/Acme/Data360 \\
                                  --dmo ssot__Account__dlm

Output:
    <output-dir>/reports/cis-on-<dmo-slug>.md
"""

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

from data360_analyst.sf_auth import get_token_and_url_or_exit


DMO_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__dlm)\b")
CIO_PATTERN = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*__cio)\b")


def token_and_url(org_alias):
    return get_token_and_url_or_exit(org_alias)


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


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", required=True, help="Salesforce CLI org alias")
    parser.add_argument("--output-dir", required=True, help="Per-client Data360 folder")
    parser.add_argument("--dmo", required=True, help="DMO API name (e.g. ssot__Account__dlm)")
    parser.add_argument("--api-version", default="v64.0")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser()
    target_dmo = args.dmo

    print(f"Authenticating as '{args.org}'...")
    token, url = token_and_url(args.org)
    base = f"{url}/services/data/{args.api_version}"

    print(f"Fetching CIs...")
    cis = fetch_all_cis(token, base)
    print(f"  {len(cis)} CIs")

    target_cis = []
    for ci in cis:
        sql = ci.get("expression") or ""
        dmos = set(DMO_PATTERN.findall(sql))
        cios = set(CIO_PATTERN.findall(sql))
        if target_dmo in dmos:
            target_cis.append({
                "apiName": ci.get("apiName"),
                "displayName": ci.get("displayName"),
                "dataSpace": ci.get("dataSpace"),
                "status": ci.get("calculatedInsightStatus"),
                "other_dmos": dmos - {target_dmo},
                "derived_from_cis": cios - {ci.get("apiName")},
                "dims": [d.get("apiName") for d in (ci.get("dimensions") or [])],
                "measures": [m.get("apiName") for m in (ci.get("measures") or [])],
                "sql_len": len(sql),
            })

    print(f"Found {len(target_cis)} CIs reading from {target_dmo}")
    if not target_cis:
        print("Nothing to write.")
        return

    # Group by 2-token prefix
    by_prefix = defaultdict(list)
    for c in target_cis:
        name = c["apiName"].replace("__cio", "")
        parts = name.split("_")
        prefix = "_".join(parts[:2])
        by_prefix[prefix].append(c)

    # Join patterns
    dmo_combos = Counter()
    for c in target_cis:
        dmo_combos[tuple(sorted(c["other_dmos"]))] += 1

    measure_counter = Counter()
    for c in target_cis:
        for m in c["measures"]:
            measure_counter[m] += 1

    derived = [c for c in target_cis if c["derived_from_cis"]]

    out = []
    out.append(f"# CIs reading from `{target_dmo}`\n")
    out.append(f"_{len(target_cis)} CIs found._\n")

    out.append("## All CIs\n")
    out.append("| API Name | Display | Status | Dataspace | # other DMOs | SQL size |")
    out.append("|----------|---------|--------|-----------|-------------:|---------:|")
    for c in sorted(target_cis, key=lambda x: x["apiName"]):
        out.append(f"| `{c['apiName']}` | {c['displayName']} | {c['status']} | {c['dataSpace']} | {len(c['other_dmos'])} | {c['sql_len']:,} |")
    out.append("")

    out.append("## Naming clusters\n")
    for prefix in sorted(by_prefix, key=lambda k: -len(by_prefix[k])):
        group = by_prefix[prefix]
        if len(group) <= 1:
            continue
        out.append(f"### `{prefix}_*` ({len(group)} CIs)")
        for c in sorted(group, key=lambda x: x["apiName"]):
            out.append(f"- `{c['apiName']}` — {c['displayName']}")
        out.append("")
    singles = [c for g in by_prefix.values() for c in g if len(g) == 1]
    if singles:
        out.append(f"### Singletons ({len(singles)})")
        for c in sorted(singles, key=lambda x: x["apiName"]):
            out.append(f"- `{c['apiName']}` — {c['displayName']}")
        out.append("")

    out.append(f"## Join patterns (DMOs co-referenced with `{target_dmo}`)\n")
    out.append("| Other DMOs joined | # CIs |")
    out.append("|-------------------|------:|")
    for combo, n in sorted(dmo_combos.items(), key=lambda x: -x[1]):
        combo_str = ", ".join(f"`{d}`" for d in combo) if combo else "_(none)_"
        out.append(f"| {combo_str} | {n} |")
    out.append("")

    out.append("## Most-produced measures across these CIs\n")
    out.append("| Measure | Used by # CIs |")
    out.append("|---------|--------------:|")
    for m, n in measure_counter.most_common(20):
        out.append(f"| `{m}` | {n} |")
    out.append("")

    if derived:
        out.append(f"## CIs that build on other CIs ({len(derived)})\n")
        for c in sorted(derived, key=lambda x: x["apiName"]):
            chain = ", ".join(f"`{x}`" for x in sorted(c["derived_from_cis"]))
            out.append(f"- `{c['apiName']}` ← reads {chain}")
        out.append("")

    multi = [(p, g) for p, g in by_prefix.items() if len(g) > 1]
    if multi:
        out.append("## Suggested reading order\n")
        out.append("Pick the simplest CI in each cluster first (smallest SQL, fewest joins). Once you understand one cluster's pattern, the others follow cheaply.\n")
        reps = [(p, len(g), min(g, key=lambda x: x["sql_len"])) for p, g in multi]
        for prefix, size, rep in sorted(reps, key=lambda x: -x[1]):
            out.append(f"- `{rep['apiName']}` — simplest in `{prefix}_*` cluster ({size} CIs, {rep['sql_len']:,} char SQL)")
        out.append("")

    slug = re.sub(r"[^a-z0-9]+", "-", target_dmo.lower()).strip("-")
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"cis-on-{slug}.md"
    report_path.write_text("\n".join(out))
    print(f"\nWrote {report_path}")
    print(f"\nCluster sizes:")
    for prefix, group in sorted(by_prefix.items(), key=lambda x: -len(x[1])):
        if len(group) > 1:
            print(f"  {prefix}_*: {len(group)} CIs")


if __name__ == "__main__":
    main()
