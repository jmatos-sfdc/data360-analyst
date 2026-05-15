#!/usr/bin/env python3
"""
Data 360 Intake Script
Generates object-model.md + per-artifact YAML sidecars for a Salesforce Data 360 org.

Usage:
    python3 intake.py --org <alias> --output-dir <path-to-client-data360-folder>

Example:
    python3 intake.py --org <alias> --output-dir ~/Projects/clients/<Client>/Data360

Output:
    <output-dir>/object-model.md                       Narrative, human-read
    <output-dir>/object-model/dmos/<name>.yaml         Per-DMO schema
    <output-dir>/object-model/cis/<name>.yaml          Per-CI metadata (sqlPath points at queries/<name>.sql)
    <output-dir>/object-model/transforms/<name>.yaml   Per-transform metadata (definitionPath points at transforms/<name>.json)
    <output-dir>/object-model/streams/<name>.yaml      Per-stream metadata
    <output-dir>/object-model/segments/<name>.yaml     Per-segment metadata
    <output-dir>/object-model/index.yaml               Org-level rollup
    <output-dir>/queries/<name>.sql                    Per-CI SQL body (HTML-decoded `expression` field)
    <output-dir>/transforms/<name>.json                Per-transform full definitions DAG (HTML-decoded)
"""

import argparse
import html
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install -r requirements.txt (or use the bundled .venv)")
    sys.exit(1)


def get_token(org_alias):
    result = subprocess.run(
        ["sf", "org", "display", "--target-org", org_alias, "--json"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"ERROR: Could not get token for org '{org_alias}'")
        print(result.stderr)
        sys.exit(1)
    data = json.loads(result.stdout)
    token = data["result"]["accessToken"]
    instance_url = data["result"]["instanceUrl"]
    return token, instance_url


_TOKEN_PATTERN = re.compile(r"(?i)(bearer\s+)?00D[A-Za-z0-9!._]{40,}")


def _scrub(body):
    """Redact anything that looks like a Salesforce session token from error bodies."""
    return _TOKEN_PATTERN.sub("[REDACTED]", body)[:2000]


def api_get(token, url, timeout=60):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": _scrub(e.read().decode())}


def find_api_version(token, instance_url):
    data = api_get(token, f"{instance_url}/services/data/")
    if isinstance(data, list):
        versions = [float(v["version"]) for v in data]
        best = max(v for v in versions if v >= 64.0) if any(v >= 64.0 for v in versions) else None
        return str(best) if best else None
    return None


def fetch_all_pages(token, base_url, collection_key, page_param="offset", batch_size=50):
    results = []
    offset = 0
    while True:
        url = f"{base_url}&{page_param}={offset}" if "?" in base_url else f"{base_url}?offset={offset}"
        data = api_get(token, url)
        if "_error" in data:
            break
        batch = data.get(collection_key, [])
        if isinstance(batch, dict):
            batch = batch.get("items", [])
        results.extend(batch)
        if len(batch) < batch_size:
            break
        offset += len(batch)
    return results


def fetch_segments_dmos(token, base_url):
    """Extract unique DMO names referenced in segment criteria."""
    dmos = set()
    segments = fetch_all_pages(token, f"{base_url}/ssot/segments?count=20", "segments", batch_size=20)
    for s in segments:
        for field in ["includeCriteria", "excludeCriteria"]:
            raw = s.get(field, "")
            if raw:
                decoded = html.unescape(raw)
                for m in re.finditer(r'"objectApiName":"([^"]+)"', decoded):
                    dmos.add(m.group(1))
    return dmos, segments


def safe_filename(name):
    """Filesystem-safe version of a Data Cloud API name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def emit_yaml_sidecars(output_dir, org_meta, dmos, cis, transforms, streams, segments, ir_items):
    """Write per-artifact YAML files for tooling/diffing; complements object-model.md.

    Also writes:
    - queries/<name>.sql for each CI (HTML-decoded `expression` field)
    - transforms/<name>.json for each user transform (full `definitions` dump, decoded)
    """
    out_root = Path(output_dir).expanduser()
    root = out_root / "object-model"
    queries_dir = out_root / "queries"
    transforms_dir = out_root / "transforms"
    queries_dir.mkdir(parents=True, exist_ok=True)
    transforms_dir.mkdir(parents=True, exist_ok=True)

    # DMOs
    for dmo in dmos:
        name = dmo.get("name")
        if not name:
            continue
        doc = {
            "name": name,
            "label": dmo.get("label") or dmo.get("displayName"),
            "category": dmo.get("category"),
            "fields": [
                {
                    "name": f.get("name"),
                    "type": f.get("type"),
                    "label": f.get("label"),
                    "usageTag": f.get("usageTag") or None,
                }
                for f in sorted(dmo.get("fields", []) or [], key=lambda x: x.get("name", ""))
            ],
        }
        write_yaml(root / "dmos" / f"{safe_filename(name)}.yaml", doc)

    # CIs — sidecar metadata + decoded SQL to queries/
    ci_sql_written = 0
    for ci in cis:
        name = ci.get("apiName")
        if not name:
            continue
        expression = ci.get("expression") or ""
        sql_path = None
        if expression:
            decoded = html.unescape(expression)
            sql_path = queries_dir / f"{safe_filename(name)}.sql"
            sql_path.write_text(decoded)
            ci_sql_written += 1
        doc = {
            "apiName": name,
            "displayName": ci.get("displayName"),
            "status": ci.get("calculatedInsightStatus"),
            "definitionType": ci.get("definitionType"),
            "dataSpace": ci.get("dataSpace"),
            "dimensions": [{"apiName": d.get("apiName"), "type": d.get("type")}
                           for d in ci.get("dimensions", []) or []],
            "metrics": [{"apiName": m.get("apiName"), "type": m.get("type")}
                        for m in ci.get("metrics", []) or []],
            "sqlPath": f"queries/{safe_filename(name)}.sql" if sql_path else None,
            "hasExpression": bool(expression),
        }
        write_yaml(root / "cis" / f"{safe_filename(name)}.yaml", doc)

    # Transforms — sidecar metadata + full definitions JSON to transforms/
    # The list endpoint returns `definitions` (plural, list) only for some transforms;
    # the detail endpoint returns `definition` (singular, dict) for the rest.
    # Normalize to a list and fall back to the detail endpoint as needed.
    token = org_meta.get("_token")
    base = org_meta.get("_base")
    transform_defs_written = 0
    for t in transforms:
        name = t.get("name")
        if not name:
            continue
        is_dpe = name.startswith("DPE_")
        defs = t.get("definitions") or []
        if not defs and not is_dpe and token and base:
            detail = api_get(token, f"{base}/ssot/data-transforms/{urllib.parse.quote(name)}")
            if isinstance(detail, dict) and "_error" not in detail:
                if detail.get("definitions"):
                    defs = detail["definitions"]
                elif detail.get("definition"):
                    defs = [detail["definition"]]
        definition_path = None
        if defs and not is_dpe:
            # Decode HTML entities only inside string leaves (e.g. formulaExpression).
            # Roundtripping the whole structure through html.unescape corrupts JSON
            # quoting, so walk the tree.
            def _decode_strings(obj):
                if isinstance(obj, str):
                    return html.unescape(obj)
                if isinstance(obj, list):
                    return [_decode_strings(x) for x in obj]
                if isinstance(obj, dict):
                    return {k: _decode_strings(v) for k, v in obj.items()}
                return obj
            decoded = _decode_strings(defs)
            definition_path = transforms_dir / f"{safe_filename(name)}.json"
            definition_path.write_text(json.dumps(decoded, indent=2))
            transform_defs_written += 1
        doc = {
            "name": name,
            "label": t.get("label"),
            "status": t.get("status"),
            "type": t.get("type"),
            "createdBy": (t.get("createdBy") or {}).get("name") if isinstance(t.get("createdBy"), dict) else None,
            "createdDate": (t.get("createdDate") or "")[:10] or None,
            "isPackageManaged": is_dpe,
            "definitionPath": f"transforms/{safe_filename(name)}.json" if definition_path else None,
            "nodeCount": sum(len(d.get("nodes") or {}) for d in defs) if isinstance(defs, list) else None,
        }
        write_yaml(root / "transforms" / f"{safe_filename(name)}.yaml", doc)
    print(f"  CI SQL files: {ci_sql_written}/{len(cis)}  •  transform definitions: {transform_defs_written}/{len(transforms)}")

    # Streams
    for s in streams:
        name = s.get("name")
        if not name:
            continue
        rc = s.get("refreshConfig", {}) or {}
        doc = {
            "name": name,
            "dataStreamType": s.get("dataStreamType"),
            "connectorType": (s.get("connectorInfo") or {}).get("connectorType"),
            "status": s.get("status"),
            "lastRunStatus": s.get("lastRunStatus"),
            "frequency": (rc.get("frequency") or {}).get("frequencyType"),
            "refreshMode": rc.get("refreshMode"),
            "totalRecords": s.get("totalRecords"),
        }
        write_yaml(root / "streams" / f"{safe_filename(name)}.yaml", doc)

    # Segments
    for seg in segments:
        name = seg.get("apiName")
        if not name:
            continue
        doc = {
            "apiName": name,
            "displayName": seg.get("displayName"),
            "status": seg.get("segmentStatus"),
            "segmentOnApiName": seg.get("segmentOnApiName"),
        }
        write_yaml(root / "segments" / f"{safe_filename(name)}.yaml", doc)

    # Org-level index (drop private auth fields prefixed with _)
    index = {
        **{k: v for k, v in org_meta.items() if not k.startswith("_")},
        "counts": {
            "dmos": len(dmos),
            "cis": len(cis),
            "transforms": len(transforms),
            "streams": len(streams),
            "segments": len(segments),
            "identityResolutionRulesets": len(ir_items),
        },
        "identityResolutionRulesets": [
            ir.get("developerName") or ir.get("name") or ir.get("Name")
            for ir in ir_items
        ],
    }
    write_yaml(root / "index.yaml", index)


def generate(org_alias, output_dir, skip_streams=False):
    client_name = Path(output_dir).parent.name
    print(f"Authenticating as '{org_alias}'...")
    token, instance_url = get_token(org_alias)
    print(f"  Instance: {instance_url}")

    print("Detecting API version...")
    version = find_api_version(token, instance_url)
    if not version:
        print("ERROR: Org does not support v64.0+. Data 360 Connect API requires v64.0 minimum.")
        sys.exit(1)
    print(f"  Using v{version}")
    base = f"{instance_url}/services/data/v{version}"

    out = []
    out.append(f"# Data 360 Object Model — {client_name.upper()}")
    out.append(f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} from {instance_url} (v{version})_\n")

    # ── Data Spaces ──────────────────────────────────────────────────────────
    print("Fetching data spaces...")
    spaces_data = api_get(token, f"{base}/ssot/data-spaces?limit=50")
    spaces = spaces_data.get("records", spaces_data.get("dataSpaces", []))
    out.append("---\n## Data Spaces\n")
    if spaces:
        for s in spaces:
            out.append(f"- `{s.get('Name', s.get('name', '?'))}` — {s.get('Description', s.get('description', ''))}")
    else:
        out.append("- `default`")
    out.append("")

    # ── Streams ───────────────────────────────────────────────────────────────
    if not skip_streams:
        print("Fetching data streams...")
        streams_data = api_get(token, f"{base}/ssot/data-streams?limit=100&includeMappings=true", timeout=180)
        streams = streams_data.get("dataStreams", [])
        if streams:
            stream_types = {}
            for s in streams:
                st = s.get("dataStreamType", "UNKNOWN")
                stream_types.setdefault(st, []).append(s)

            out.append("---\n## Data Streams\n")
            out.append(f"Total: {len(streams)}\n")
            for stype, items in sorted(stream_types.items()):
                out.append(f"### {stype} ({len(items)})\n")
                out.append("| Name | Connector | Status | Last Run | Frequency | Mode | Total Records |")
                out.append("|------|-----------|--------|----------|-----------|------|---------------|")
                for s in sorted(items, key=lambda x: x.get("name", "")):
                    name = s.get("name", "?")
                    connector = s.get("connectorInfo", {}).get("connectorType", "?")
                    status = s.get("status", "?")
                    last_run = s.get("lastRunStatus", "?")
                    rc = s.get("refreshConfig", {})
                    freq_obj = rc.get("frequency", {})
                    freq = freq_obj.get("frequencyType", "?")
                    mode = rc.get("refreshMode", "?")
                    total = s.get("totalRecords", "?")
                    out.append(f"| `{name}` | {connector} | {status} | {last_run} | {freq} | {mode} | {total:,} |" if isinstance(total, int) else f"| `{name}` | {connector} | {status} | {last_run} | {freq} | {mode} | {total} |")
                out.append("")
    else:
        print("Skipping data streams (--skip-streams)")
        streams = []

    # ── Calculated Insights ───────────────────────────────────────────────────
    print("Fetching calculated insights...")
    cis = []
    ci_offset = 0
    ci_batch = 25
    while True:
        ci_data = api_get(token, f"{base}/ssot/calculated-insights?batchSize={ci_batch}&offset={ci_offset}")
        if "_error" in ci_data:
            break
        batch = ci_data.get("collection", {}).get("items", [])
        if not batch:
            break
        cis.extend(batch)
        print(f"  ...fetched {len(cis)} CIs")
        if not ci_data.get("collection", {}).get("nextPageToken"):
            break
        ci_offset += ci_batch
    if cis:
        out.append("---\n## Calculated Insights\n")
        out.append(f"Total: {len(cis)}\n")
        out.append("| API Name | Display Name | Status | Type | Dimensions | Metrics |")
        out.append("|----------|-------------|--------|------|------------|---------|")
        for ci in sorted(cis, key=lambda x: x.get("apiName", "")):
            dims = ", ".join(d["apiName"] for d in ci.get("dimensions", []))
            metrics = ", ".join(m["apiName"] for m in ci.get("metrics", []))
            out.append(f"| `{ci.get('apiName','?')}` | {ci.get('displayName','?')} | {ci.get('calculatedInsightStatus','?')} | {ci.get('definitionType','?')} | {dims} | {metrics} |")
        out.append("")
        out.append("> **SQL on disk:** intake writes each CI's `expression` field (HTML-decoded) to `queries/<ci-name>.sql` automatically.")
        out.append("")

    # ── Data Transforms ───────────────────────────────────────────────────────
    print("Fetching data transforms...")
    transforms = fetch_all_pages(token, f"{base}/ssot/data-transforms", "dataTransforms", batch_size=20)
    # DPE_* transforms are Auto Cloud / package-managed — not user-configured assets
    user_transforms = [t for t in transforms if not t.get("name", "").startswith("DPE_")]
    dpe_count = len(transforms) - len(user_transforms)
    out.append("---\n## Data Transforms\n")
    if user_transforms:
        out.append(f"Total: {len(user_transforms)}\n")
        out.append("| Name | Label | Status | Created By | Created Date |")
        out.append("|------|-------|--------|------------|--------------|")
        for t in sorted(user_transforms, key=lambda x: x.get("label", x.get("name", ""))):
            name = t.get("name", "?")
            label = t.get("label", name)
            status = t.get("status", "?")
            created_by = t.get("createdBy", {}).get("name", "?") if isinstance(t.get("createdBy"), dict) else "?"
            created = t.get("createdDate", "?")[:10] if t.get("createdDate") else "?"
            out.append(f"| `{name}` | {label} | {status} | {created_by} | {created} |")
        out.append("")
        out.append("> **Definitions on disk:** intake writes each transform's full `definitions` (DAG of load/join/formula/filter/aggregate/output nodes, including `formulaExpression` SQL — HTML-decoded) to `transforms/<name>.json` automatically.")
    else:
        out.append("- No user-configured transforms found")
    if dpe_count:
        out.append(f"\n> **Note:** {dpe_count} Auto Cloud system transform(s) (prefix `DPE_`) are excluded — these are package-managed and not user-configured.")
    out.append("")

    # ── Identity Resolution ───────────────────────────────────────────────────
    print("Fetching identity resolution rulesets...")
    ir_data = api_get(token, f"{base}/ssot/identity-resolutions")
    ir_items = ir_data if isinstance(ir_data, list) else ir_data.get("identityResolutions", ir_data.get("records", []))
    out.append("---\n## Identity Resolution\n")
    if ir_items:
        for ir in ir_items:
            name = ir.get("developerName", ir.get("name", ir.get("Name", "?")))
            out.append(f"- `{name}`")
    else:
        out.append("- No identity resolution rulesets found (or not accessible)")
    out.append("")

    # ── DMO Schemas ───────────────────────────────────────────────────────────
    print("Fetching data model objects...")
    all_dmos = []
    dmo_offset = 0
    dmo_batch = 50
    while True:
        dmo_data = api_get(token, f"{base}/ssot/data-model-objects?limit={dmo_batch}&offset={dmo_offset}", timeout=120)
        if "_error" in dmo_data:
            break
        batch = dmo_data.get("dataModelObject", [])
        all_dmos.extend(batch)
        print(f"  ...fetched {len(all_dmos)} DMOs")
        if len(batch) < dmo_batch:
            break
        dmo_offset += len(batch)

    # Categorize
    cat_counts = {}
    for d in all_dmos:
        cat = d.get("category", "UNKNOWN")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    out.append("---\n## Data Model Objects\n")
    out.append(f"Total: {len(all_dmos)}\n")
    out.append("### By Category\n")
    for cat, count in sorted(cat_counts.items()):
        out.append(f"- **{cat}**: {count}")
    out.append("")

    # Exclude noise categories: UNASSIGNED = standard schema stubs with no mappings,
    # ACTIVATION_AUDIENCE / SEGMENT_MEMBERSHIP = system-managed, not user-facing.
    # DPE_DS_* are Auto Cloud / package-managed transform output DMOs, not user-configured.
    exclude_cats = {"UNASSIGNED", "ACTIVATION_AUDIENCE", "SEGMENT_MEMBERSHIP"}
    core_dmos = [d for d in all_dmos
                 if d.get("category", "UNASSIGNED").upper() not in exclude_cats
                 and not d.get("name", "").startswith("DPE_")]

    out.append(f"### Field Schemas ({len(core_dmos)} core objects)\n")
    for dmo in sorted(core_dmos, key=lambda x: (x.get("category", ""), x.get("name", ""))):
        name = dmo.get("name", "?")
        label = dmo.get("label", dmo.get("displayName", "?"))
        cat = dmo.get("category", "?")
        fields = dmo.get("fields", [])
        out.append(f"#### `{name}` — {label} _{cat}_")
        if fields:
            out.append("| Field | Type | Label | Usage |")
            out.append("|-------|------|-------|-------|")
            for f in sorted(fields, key=lambda x: x.get("name", "")):
                usage = f.get("usageTag", "")
                out.append(f"| `{f['name']}` | {f['type']} | {f['label']} | {usage} |")
        out.append("")

    # ── Segments summary ──────────────────────────────────────────────────────
    print("Fetching segments summary...")
    seg_dmos, segments = fetch_segments_dmos(token, base)
    out.append("---\n## Segments\n")
    out.append(f"Total: {len(segments)}\n")
    out.append("| API Name | Display Name | Status | Segment On |")
    out.append("|----------|-------------|--------|------------|")
    for s in sorted(segments, key=lambda x: x.get("apiName", "")):
        out.append(f"| `{s.get('apiName','?')}` | {s.get('displayName','?')} | {s.get('segmentStatus','?')} | `{s.get('segmentOnApiName','?')}` |")
    out.append("")

    if seg_dmos:
        out.append("### DMOs Referenced in Segment Criteria\n")
        for d in sorted(seg_dmos):
            out.append(f"- `{d}`")
        out.append("")

    # Write markdown
    output_path = Path(output_dir).expanduser() / "object-model.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out))
    print(f"\nSaved: {output_path}")

    # Write YAML sidecars
    print("Writing YAML sidecars...")
    emit_yaml_sidecars(
        output_dir,
        org_meta={
            "client": client_name,
            "orgAlias": org_alias,
            "instanceUrl": instance_url,
            "apiVersion": version,
            "generatedAt": datetime.now().isoformat(timespec="seconds"),
            "_token": token,
            "_base": base,
        },
        dmos=core_dmos,
        cis=cis,
        transforms=user_transforms,
        streams=streams,
        segments=segments,
        ir_items=ir_items,
    )
    sidecar_root = Path(output_dir).expanduser() / "object-model"
    print(f"  YAML sidecars under: {sidecar_root}")

    print(f"  {len(cis)} CIs | {len(all_dmos)} DMOs | {len(streams)} streams | {len(transforms)} transforms | {len(segments)} segments")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Data 360 object model for a Salesforce org")
    parser.add_argument("--org", required=True, help="sf org alias")
    parser.add_argument("--output-dir", required=True,
                        help="Path to the client's Data360 folder, e.g. ~/Projects/clients/<Client>/Data360")
    parser.add_argument("--skip-streams", action="store_true",
                        help="Skip data streams fetch (useful for large orgs that timeout)")
    args = parser.parse_args()
    generate(args.org, args.output_dir, skip_streams=args.skip_streams)
