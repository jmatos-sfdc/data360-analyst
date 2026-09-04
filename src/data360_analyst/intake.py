#!/usr/bin/env python3
"""
Data 360 Intake Script
Generates object-model.md + per-artifact YAML sidecars for a Salesforce Data 360 org.

Usage:
    python3 intake.py --org <alias> --output-dir <path-to-client-data360-folder>
    python3 intake.py --org <alias> --output-dir <path> --dry-run
    python3 intake.py --org <alias> --output-dir <path> --incremental

Example:
    python3 intake.py --org <alias> --output-dir ~/Projects/clients/<Client>/Data360

Output:
    <output-dir>/object-model.md                       Narrative, human-read
    <output-dir>/object-model/dmos/<name>.yaml         Per-DMO schema
    <output-dir>/object-model/dlos/<name>.yaml         Per-DLO schema
    <output-dir>/object-model/mappings/<name>.yaml     Per DLO->DMO mapping
    <output-dir>/object-model/cis/<name>.yaml          Per-CI metadata (sqlPath points at queries/<name>.sql)
    <output-dir>/object-model/transforms/<name>.yaml   Per-transform metadata (definitionPath points at transforms/<name>.json)
    <output-dir>/object-model/streams/<name>.yaml      Per-stream metadata
    <output-dir>/object-model/segments/<name>.yaml     Per-segment metadata
    <output-dir>/object-model/index.yaml               Org-level rollup
    <output-dir>/object-model/_manifest.yaml           Per-artifact content hashes (incremental mode)
    <output-dir>/queries/<name>.sql                    Per-CI SQL body (HTML-decoded `expression` field)
    <output-dir>/transforms/<name>.json                Per-transform full definitions DAG (HTML-decoded)
"""

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from data360_analyst.sf_auth import get_token_and_url_or_exit

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install -r requirements.txt (or use the bundled .venv)")
    sys.exit(1)


def get_token(org_alias):
    return get_token_and_url_or_exit(org_alias)


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


def hash_payload(obj):
    """Stable SHA-256 of a JSON-serializable artifact. Sort keys so dict order
    doesn't churn the hash across runs."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


def load_manifest(output_dir):
    path = Path(output_dir).expanduser() / "object-model" / "_manifest.yaml"
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}


def save_manifest(output_dir, manifest):
    path = Path(output_dir).expanduser() / "object-model" / "_manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(manifest, sort_keys=True, allow_unicode=True))


# Raw source keys intake reads off each endpoint's payload. If one of these was
# present in a prior run but has vanished from every artifact this run, the
# endpoint likely dropped or renamed it (e.g. `measures` on
# /ssot/calculated-insights) — and the sidecar we'd write for it is silently
# malformed. We fingerprint field presence per run and warn on regressions
# instead of writing a broken snapshot without comment.
_EXPECTED_SOURCE_FIELDS = {
    "dmos": ["name", "label", "category", "fields"],
    "dlos": ["name", "label", "category"],
    "mappings": ["developerName", "sourceEntityDeveloperName", "targetEntityDeveloperName"],
    "cis": ["apiName", "expression", "dimensions", "measures", "calculatedInsightStatus"],
    "transforms": ["name", "label", "status"],
    "streams": ["name", "dataStreamType", "status"],
    "segments": ["apiName", "segmentOnApiName"],
    "activations": ["activationType"],
}

_SCHEMA_KEY = "#schema"


def _observed_fields(category, items):
    """Which expected source fields actually appear (as keys) in this run's raw
    artifacts for a category. A key present with a None/empty value still counts
    as present — we're detecting a field disappearing, not going empty."""
    expected = _EXPECTED_SOURCE_FIELDS.get(category, [])
    return sorted(
        f for f in expected
        if any(isinstance(it, dict) and f in it for it in items)
    )


def detect_schema_drift(prior_schema, items_by_category, api_version):
    """Compare this run's field presence against the prior manifest fingerprint.

    Returns (new_schema, warnings). `new_schema` goes back into the manifest
    under `_SCHEMA_KEY`; `warnings` is a list of human-readable drift lines.
    A category with zero artifacts this run is not evaluated (can't tell an
    empty org from a dropped endpoint) — its prior fingerprint is carried
    forward untouched."""
    prior = prior_schema or {}
    prior_fields = dict(prior.get("fields") or {})
    warnings = []

    new_fields = dict(prior_fields)
    for category in _EXPECTED_SOURCE_FIELDS:
        items = items_by_category.get(category) or []
        if not items:
            continue  # nothing to observe; keep any prior fingerprint as-is
        observed = _observed_fields(category, items)
        prior_here = prior_fields.get(category)
        if prior_here:
            dropped = [f for f in prior_here if f not in observed]
            if dropped:
                warnings.append(
                    f"{category}: field(s) {', '.join(dropped)} present last run but "
                    f"missing from all {len(items)} artifact(s) this run — endpoint may "
                    f"have dropped/renamed them; sidecars for this category may be malformed"
                )
        new_fields[category] = observed

    prior_version = prior.get("apiVersion")
    if prior_version and api_version and prior_version != api_version:
        warnings.append(
            f"API version changed {prior_version} -> {api_version} since last run; "
            f"field changes below (if any) may be version-driven"
        )

    return {"apiVersion": api_version, "fields": new_fields}, warnings


def emit_yaml_sidecars(output_dir, org_meta, dmos, dlos, mappings, cis, transforms,
                       streams, segments, activations, ir_items, incremental=False):
    """Write per-artifact YAML files for tooling/diffing; complements object-model.md.

    Also writes:
    - queries/<name>.sql for each CI (HTML-decoded `expression` field)
    - transforms/<name>.json for each user transform (full `definitions` dump, decoded)

    When `incremental=True`, skips writes for artifacts whose normalized payload
    hash matches the prior `_manifest.yaml` entry. Returns a stats dict.
    """
    out_root = Path(output_dir).expanduser()
    root = out_root / "object-model"
    queries_dir = out_root / "queries"
    transforms_dir = out_root / "transforms"
    queries_dir.mkdir(parents=True, exist_ok=True)
    transforms_dir.mkdir(parents=True, exist_ok=True)

    prior = load_manifest(output_dir) if incremental else {}
    manifest = dict(prior)
    stats = {"written": 0, "skipped": 0}

    def _persist(category, name, doc, side_files=None):
        """Write a sidecar (and any side files) only if the payload changed.
        `side_files` is an iterable of (path, bytes_or_str) — these write/skip in
        lockstep with the sidecar so a CI's .sql or transform's .json stays in
        sync with its yaml."""
        key = f"{category}/{safe_filename(name)}"
        digest = hash_payload({"doc": doc, "side": [
            (str(p), s if isinstance(s, str) else s.decode("utf-8", "replace"))
            for p, s in (side_files or [])
        ]})
        if incremental and prior.get(key) == digest:
            stats["skipped"] += 1
            manifest[key] = digest
            return False
        write_yaml(root / category / f"{safe_filename(name)}.yaml", doc)
        for path, payload in (side_files or []):
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, bytes):
                path.write_bytes(payload)
            else:
                path.write_text(payload)
        manifest[key] = digest
        stats["written"] += 1
        return True

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
        _persist("dmos", name, doc)

    # DLOs
    for dlo in dlos or []:
        name = dlo.get("name")
        if not name:
            continue
        # DLO fields can come back under `dataLakeFieldInfoRepresentation` or
        # `fields` depending on whether the response is from list vs detail.
        raw_fields = (
            dlo.get("fields")
            or dlo.get("dataLakeFieldInfoRepresentation")
            or []
        )
        ds_info = dlo.get("dataSpaceInfo") or []
        ds_name = (
            ds_info[0].get("name") if isinstance(ds_info, list) and ds_info else None
        ) or dlo.get("dataSpaceName")
        doc = {
            "name": name,
            "label": dlo.get("label") or dlo.get("displayName"),
            "category": dlo.get("category"),
            "dataSpaceName": ds_name,
            "fields": [
                {
                    "name": f.get("name"),
                    "type": f.get("dataType") or f.get("type"),
                    "label": f.get("label"),
                    "isPrimaryKey": f.get("isPrimaryKey") or None,
                }
                for f in sorted(raw_fields, key=lambda x: x.get("name", ""))
            ],
        }
        _persist("dlos", name, doc)

    # DLO -> DMO mappings — `sourceEntityDeveloperName` is the DLO,
    # `targetEntityDeveloperName` is the DMO.
    for m in mappings or []:
        name = m.get("developerName")
        if not name:
            continue
        doc = {
            "developerName": name,
            "dloDeveloperName": m.get("sourceEntityDeveloperName"),
            "dmoDeveloperName": m.get("targetEntityDeveloperName"),
            "status": m.get("status"),
            "fieldMappingsCount": len(m.get("fieldMappings") or []) or None,
        }
        _persist("mappings", name, doc)

    # CIs — sidecar metadata + decoded SQL to queries/
    ci_sql_written = 0
    for ci in cis:
        name = ci.get("apiName")
        if not name:
            continue
        expression = ci.get("expression") or ""
        decoded_sql = html.unescape(expression) if expression else None
        sql_path = queries_dir / f"{safe_filename(name)}.sql" if decoded_sql else None
        doc = {
            "apiName": name,
            "displayName": ci.get("displayName"),
            "status": ci.get("calculatedInsightStatus"),
            "definitionType": ci.get("definitionType"),
            "dataSpace": ci.get("dataSpace"),
            "dimensions": [{"apiName": d.get("apiName"), "type": d.get("type")}
                           for d in ci.get("dimensions", []) or []],
            "measures": [{"apiName": m.get("apiName"), "type": m.get("type")}
                         for m in ci.get("measures", []) or []],
            "sqlPath": f"queries/{safe_filename(name)}.sql" if sql_path else None,
            "hasExpression": bool(expression),
        }
        side = [(sql_path, decoded_sql)] if sql_path else None
        _persist("cis", name, doc, side_files=side)
        if sql_path:
            ci_sql_written += 1  # count CIs whose SQL ends up on disk (writes or kept-from-prior-run)

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
        # Incremental fast-path: if list-level signature (status + lastModified)
        # hasn't changed since last run, skip the detail fetch entirely.
        list_sig = hash_payload({
            "status": t.get("status"),
            "lastModifiedDate": t.get("lastModifiedDate"),
            "createdDate": t.get("createdDate"),
        })
        sig_key = f"transforms/{safe_filename(name)}#sig"
        defs = t.get("definitions") or []
        if not defs and not is_dpe and token and base:
            if not (incremental and prior.get(sig_key) == list_sig):
                detail = api_get(token, f"{base}/ssot/data-transforms/{urllib.parse.quote(name)}")
                if isinstance(detail, dict) and "_error" not in detail:
                    if detail.get("definitions"):
                        defs = detail["definitions"]
                    elif detail.get("definition"):
                        defs = [detail["definition"]]
        definition_path = None
        side_files = None
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
            side_files = [(definition_path, json.dumps(decoded, indent=2))]
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
        _persist("transforms", name, doc, side_files=side_files)
        manifest[sig_key] = list_sig
    print(f"  CI SQL files: {ci_sql_written}/{len(cis)}  •  transform definitions: {transform_defs_written}/{len(transforms)}")

    # Streams
    for s in streams:
        name = s.get("name")
        if not name:
            continue
        rc = s.get("refreshConfig", {}) or {}
        # Stream→DLO link: the `includeMappings=true` response surfaces a
        # `dataLakeObjectInfo` block with the DLO name. Field varies across
        # API versions, so try a few keys before falling back to None.
        dlo_info = (
            s.get("dataLakeObjectInfo")
            or s.get("dataLakeObject")
            or {}
        )
        dlo_name = (
            dlo_info.get("name")
            if isinstance(dlo_info, dict) else None
        ) or s.get("dataLakeObjectName")
        doc = {
            "name": name,
            "dataStreamType": s.get("dataStreamType"),
            "connectorType": (s.get("connectorInfo") or {}).get("connectorType"),
            "status": s.get("status"),
            "lastRunStatus": s.get("lastRunStatus"),
            "frequency": (rc.get("frequency") or {}).get("frequencyType"),
            "refreshMode": rc.get("refreshMode"),
            "totalRecords": s.get("totalRecords"),
            "dataLakeObjectName": dlo_name,
        }
        _persist("streams", name, doc)

    # Segments — surface every DMO/CI referenced in include/exclude criteria so
    # the lineage graph can resolve segment → upstream object edges without
    # re-parsing the HTML-encoded criteria trees.
    for seg in segments:
        name = seg.get("apiName")
        if not name:
            continue
        criteria_objects = set()
        for field in ("includeCriteria", "excludeCriteria"):
            raw = seg.get(field) or ""
            if raw:
                decoded = html.unescape(raw)
                for m in re.finditer(r'"objectApiName":"([^"]+)"', decoded):
                    criteria_objects.add(m.group(1))
        doc = {
            "apiName": name,
            "displayName": seg.get("displayName"),
            "status": seg.get("segmentStatus"),
            "segmentOnApiName": seg.get("segmentOnApiName"),
            "criteriaObjects": sorted(criteria_objects),
        }
        _persist("segments", name, doc)

    # Activations
    for act in activations:
        name = act.get("name") or act.get("developerName")
        if not name:
            continue
        doc = {
            "name": name,
            "developerName": act.get("developerName"),
            "label": act.get("label") or act.get("displayName"),
            "activationType": act.get("activationType"),
            "activationTargetName": act.get("activationTargetName"),
            "segmentApiName": act.get("segmentApiName"),
            "dataSpace": act.get("dataSpaceName"),
            "status": act.get("status"),
            "lastPublishStatus": act.get("lastPublishStatus"),
            "lastPublishDate": (act.get("lastPublishDate") or "")[:10] or None,
        }
        _persist("activations", name, doc)

    # Schema-drift check — fingerprint field presence and warn if an endpoint
    # dropped/renamed a field intake depends on (vs the prior run's manifest).
    items_by_category = {
        "dmos": dmos, "dlos": dlos or [], "mappings": mappings or [],
        "cis": cis, "transforms": transforms, "streams": streams,
        "segments": segments, "activations": activations,
    }
    # Read the prior fingerprint even on a full (non-incremental) run — `prior`
    # is empty there, but the last run's _manifest.yaml on disk still holds it.
    prior_schema = prior.get(_SCHEMA_KEY) or load_manifest(output_dir).get(_SCHEMA_KEY)
    new_schema, drift_warnings = detect_schema_drift(
        prior_schema, items_by_category, org_meta.get("apiVersion")
    )
    manifest[_SCHEMA_KEY] = new_schema
    for w in drift_warnings:
        print(f"  WARN: schema drift — {w}")

    # Org-level index (drop private auth fields prefixed with _)
    index = {
        **{k: v for k, v in org_meta.items() if not k.startswith("_")},
        "schemaDrift": drift_warnings or None,
        "counts": {
            "dmos": len(dmos),
            "dlos": len(dlos or []),
            "mappings": len(mappings or []),
            "cis": len(cis),
            "transforms": len(transforms),
            "streams": len(streams),
            "segments": len(segments),
            "activations": len(activations),
            "identityResolutionRulesets": len(ir_items),
        },
        "identityResolutionRulesets": [
            ir.get("developerName") or ir.get("name") or ir.get("Name")
            for ir in ir_items
        ],
    }
    write_yaml(root / "index.yaml", index)
    save_manifest(output_dir, manifest)
    stats["drift"] = drift_warnings
    return stats


def count_only(org_alias):
    """Hit each list endpoint with a tiny page and report totals — no writes."""
    print(f"Authenticating as '{org_alias}'...")
    token, instance_url = get_token(org_alias)
    print(f"  Instance: {instance_url}")
    version = find_api_version(token, instance_url)
    if not version:
        print("ERROR: Org does not support v64.0+. Data 360 Connect API requires v64.0 minimum.")
        sys.exit(1)
    base = f"{instance_url}/services/data/v{version}"

    def _len(url, key, sub=None, timeout=60):
        try:
            data = api_get(token, url, timeout=timeout)
        except Exception as exc:
            return f"err({type(exc).__name__})"
        if "_error" in data:
            return f"err({data['_error']})"
        coll = data.get(key, [])
        if sub and isinstance(coll, dict):
            coll = coll.get(sub, [])
        return len(coll) if isinstance(coll, list) else "?"

    print("\nFetching counts (single-page only, no writes)...")
    # DMOs / DLOs / streams / transforms / IR: full single-page fetches.
    # CIs / segments / activations: paginated; only first page counted here as a
    # rough scope check — the full-run total is what gets persisted.
    rows = [
        ("DMOs (page 1)",        _len(f"{base}/ssot/data-model-objects?limit=200", "dataModelObject")),
        ("DLOs (page 1)",        _len(f"{base}/ssot/data-lake-objects?limit=100", "dataLakeObjects")),
        # Mappings endpoint requires dmoDeveloperName; sample one DMO for the count.
        ("Mappings (sample)",    _len(f"{base}/ssot/data-model-object-mappings?dmoDeveloperName=ssot__Account__dlm", "objectSourceTargetMaps")),
        ("Streams",              _len(f"{base}/ssot/data-streams?limit=100", "dataStreams", timeout=180)),
        ("CIs (page 1)",         _len(f"{base}/ssot/calculated-insights?batchSize=25", "collection", "items")),
        ("Transforms (page 1)",  _len(f"{base}/ssot/data-transforms?limit=20", "dataTransforms")),
        ("Segments (page 1)",    _len(f"{base}/ssot/segments?count=20", "segments")),
        ("Activations (page 1)", _len(f"{base}/ssot/activations?count=20", "activations")),
        ("IR rulesets",          _len(f"{base}/ssot/identity-resolutions", "identityResolutions")),
    ]
    width = max(len(r[0]) for r in rows)
    for label, n in rows:
        print(f"  {label:<{width}}  {n}")
    print("\nDry run complete — no files written.")


def generate(org_alias, output_dir, skip_streams=False, incremental=False):
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
    if incremental:
        prior_count = len(load_manifest(output_dir))
        print(f"  Incremental mode: prior manifest has {prior_count} entries")

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
        out.append("| API Name | Display Name | Status | Type | Dimensions | Measures |")
        out.append("|----------|-------------|--------|------|------------|----------|")
        for ci in sorted(cis, key=lambda x: x.get("apiName", "")):
            dims = ", ".join(d["apiName"] for d in ci.get("dimensions", []))
            measures = ", ".join(m["apiName"] for m in ci.get("measures", []))
            out.append(f"| `{ci.get('apiName','?')}` | {ci.get('displayName','?')} | {ci.get('calculatedInsightStatus','?')} | {ci.get('definitionType','?')} | {dims} | {measures} |")
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

    # ── Data Lake Objects ─────────────────────────────────────────────────────
    # Bridge between streams and DMOs. Endpoint paginates via `nextPageUrl`
    # (server caps actual page size around 20 regardless of `limit`); response
    # wraps records in `dataLakeObjects`.
    print("Fetching data lake objects...")
    dlos = []
    next_path = "/ssot/data-lake-objects?limit=100&offset=0"
    while next_path:
        # `next_path` may be a full path starting with /services/ — strip the
        # base prefix so we can pass it through api_get's expected absolute URL.
        if next_path.startswith("/services/"):
            page_url = f"{instance_url}{next_path}"
        else:
            page_url = f"{base}{next_path}"
        page = api_get(token, page_url, timeout=120)
        if "_error" in page:
            print(f"  WARN: DLO fetch returned {page['_error']} — continuing without DLOs")
            break
        dlos.extend(page.get("dataLakeObjects") or [])
        print(f"  ...fetched {len(dlos)} DLOs")
        next_path = page.get("nextPageUrl")
    print(f"  DLOs: {len(dlos)}")

    # ── DLO → DMO mappings ────────────────────────────────────────────────────
    # Closes the Stream→DLO→DMO lineage gap. The list endpoint requires a
    # filter; iterate over DMOs and union the results. Response wraps records
    # in `objectSourceTargetMaps` with `sourceEntityDeveloperName` (DLO) and
    # `targetEntityDeveloperName` (DMO).
    print("Fetching DLO→DMO mappings...")
    mappings = []
    seen_mappings = set()
    for dmo in core_dmos:
        dmo_name = dmo.get("name")
        if not dmo_name:
            continue
        page = api_get(
            token,
            f"{base}/ssot/data-model-object-mappings?dmoDeveloperName={urllib.parse.quote(dmo_name)}",
        )
        if "_error" in page:
            continue
        for m in page.get("objectSourceTargetMaps") or []:
            key = m.get("developerName")
            if key and key not in seen_mappings:
                seen_mappings.add(key)
                mappings.append(m)
    print(f"  Mappings: {len(mappings)}")

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

    # ── Activations ───────────────────────────────────────────────────────────
    print("Fetching activations...")
    activations = fetch_all_pages(token, f"{base}/ssot/activations?count=20", "activations", batch_size=20)
    out.append("---\n## Activations\n")
    out.append(f"Total: {len(activations)}\n")
    if activations:
        out.append("| Name | Type | Target | Segment | Status | Last Publish |")
        out.append("|------|------|--------|---------|--------|--------------|")
        for a in sorted(activations, key=lambda x: x.get("name", x.get("developerName", ""))):
            name = a.get("name", a.get("developerName", "?"))
            atype = a.get("activationType", "?")
            target = a.get("activationTargetName", "?")
            seg = a.get("segmentApiName", "?")
            status = a.get("status", "?")
            last = (a.get("lastPublishDate") or "")[:10] or "?"
            out.append(f"| `{name}` | {atype} | `{target}` | `{seg}` | {status} | {last} |")
        out.append("")

    # Write markdown
    output_path = Path(output_dir).expanduser() / "object-model.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(out))
    print(f"\nSaved: {output_path}")

    # Write YAML sidecars
    print("Writing YAML sidecars...")
    stats = emit_yaml_sidecars(
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
        dlos=dlos,
        mappings=mappings,
        cis=cis,
        transforms=user_transforms,
        streams=streams,
        segments=segments,
        activations=activations,
        ir_items=ir_items,
        incremental=incremental,
    )
    sidecar_root = Path(output_dir).expanduser() / "object-model"
    print(f"  YAML sidecars under: {sidecar_root}")
    if incremental:
        print(f"  Incremental: {stats['written']} written, {stats['skipped']} unchanged")
    if stats.get("drift"):
        print(f"\n  Schema drift detected ({len(stats['drift'])}) — see 'schemaDrift' in index.yaml:")
        for w in stats["drift"]:
            print(f"    - {w}")

    print(f"  {len(cis)} CIs | {len(all_dmos)} DMOs | {len(dlos)} DLOs | {len(mappings)} mappings | {len(streams)} streams | {len(transforms)} transforms | {len(segments)} segments | {len(activations)} activations")


def run_downstream_reports(org_alias, output_dir):
    """Run ci_audit.py and dmo_graph.py against the freshly-written snapshot.

    These two reports are what the dashboard reads — keeping them in step with
    intake means a refreshed snapshot is dashboard-ready in one command.
    Diagram cross-check and CI clustering need explicit user inputs (a Lucid
    file / a target DMO) so they stay user-invoked.
    """
    here = Path(__file__).resolve().parent
    output_dir = str(Path(output_dir).expanduser())
    for label, cmd in [
        ("ci_audit", [sys.executable, str(here / "ci_audit.py"),
                      "--output-dir", output_dir]),
        ("dmo_graph", [sys.executable, str(here / "dmo_graph.py"),
                       "--org", org_alias, "--output-dir", output_dir]),
    ]:
        print(f"\n→ Running {label}.py...")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"  WARN: {label}.py exited {result.returncode} — dashboard tab may be stale")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Data 360 object model for a Salesforce org")
    parser.add_argument("--org", required=True, help="sf org alias")
    parser.add_argument("--output-dir",
                        help="Path to the client's Data360 folder, e.g. ~/Projects/clients/<Client>/Data360. "
                             "Required unless --dry-run is passed.")
    parser.add_argument("--skip-streams", action="store_true",
                        help="Skip data streams fetch (useful for large orgs that timeout)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Hit list endpoints, print counts, write nothing. "
                             "Use to estimate scope before a full intake.")
    parser.add_argument("--incremental", action="store_true",
                        help="Skip sidecar writes for artifacts whose payload hash matches "
                             "the prior run's _manifest.yaml. Cuts re-run time on large orgs.")
    parser.add_argument("--skip-reports", action="store_true",
                        help="Skip the ci_audit.py and dmo_graph.py runs that normally "
                             "follow intake. By default both run so dashboard.py picks up "
                             "fresh inputs without a separate command.")
    args = parser.parse_args()
    if args.dry_run:
        count_only(args.org)
    else:
        if not args.output_dir:
            parser.error("--output-dir is required unless --dry-run is passed")
        generate(args.org, args.output_dir,
                 skip_streams=args.skip_streams,
                 incremental=args.incremental)
        if not args.skip_reports:
            run_downstream_reports(args.org, args.output_dir)
