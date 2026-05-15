#!/usr/bin/env python3
"""
Data 360 MCP Server (local, stdio)

Wraps the Salesforce Data Cloud `/services/data/vXX.X/ssot/*` REST endpoints
(plus a slice of the Tooling API for Data Processing Engine flows)
as Model Context Protocol tools so Claude Code can drill into an org iteratively
instead of re-running intake.py.

Launch from Claude Code by registering this as an MCP server:

    claude mcp add data360 \
        /Users/<you>/Projects/salesforce/data360-analyst/.venv/bin/python \
        /Users/<you>/Projects/salesforce/data360-analyst/mcp_server.py \
        --org <sf-alias>

The server shells out to `sf org display --target-org <alias> --json` to get
a fresh access token on each call — simple and reuses the existing sf CLI auth.
Access tokens are Data-Cloud-short-lived (~2h); shelling out per call dodges
refresh logic at the cost of a few hundred ms per tool call.
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from typing import Any


_TOKEN_PATTERN = re.compile(r"(?i)(bearer\s+)?00D[A-Za-z0-9!._]{40,}")


def _scrub(body: str) -> str:
    """Redact anything that looks like a Salesforce session token from error bodies."""
    return _TOKEN_PATTERN.sub("[REDACTED]", body)[:2000]

try:
    from fastmcp import FastMCP
except ImportError:
    print("ERROR: fastmcp not installed. Use the bundled .venv.", file=sys.stderr)
    sys.exit(1)


ORG_ALIAS = os.environ.get("DATA360_ORG", "")


def _sf_token(org_alias: str) -> tuple[str, str]:
    result = subprocess.run(
        ["sf", "org", "display", "--target-org", org_alias, "--json"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"sf org display failed for '{org_alias}': {result.stderr}")
    data = json.loads(result.stdout)
    return data["result"]["accessToken"], data["result"]["instanceUrl"]


def _api_version(token: str, instance_url: str) -> str:
    req = urllib.request.Request(
        f"{instance_url}/services/data/",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        versions = json.loads(r.read())
    candidates = [float(v["version"]) for v in versions if float(v["version"]) >= 64.0]
    if not candidates:
        raise RuntimeError("Org does not support API v64.0+ (required for Data 360 Connect).")
    return str(max(candidates))


_AUTH_CACHE: dict = {"token": None, "instance_url": None, "version": None, "fetched_at": 0.0}
_AUTH_TTL_SEC = 1800  # refresh every 30 min; tokens last ~2h


def _auth() -> tuple[str, str, str]:
    import time
    now = time.time()
    if _AUTH_CACHE["token"] and (now - _AUTH_CACHE["fetched_at"] < _AUTH_TTL_SEC):
        return _AUTH_CACHE["token"], _AUTH_CACHE["instance_url"], _AUTH_CACHE["version"]
    token, instance_url = _sf_token(ORG_ALIAS)
    version = _api_version(token, instance_url)
    _AUTH_CACHE.update(token=token, instance_url=instance_url, version=version, fetched_at=now)
    return token, instance_url, version


def _get(path: str, params: dict | None = None) -> Any:
    if not ORG_ALIAS:
        raise RuntimeError("DATA360_ORG not set — pass --org on launch or export DATA360_ORG.")
    token, instance_url, version = _auth()
    url = f"{instance_url}/services/data/v{version}{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": _scrub(e.read().decode())}


def _tooling_query(soql: str) -> Any:
    return _get("/tooling/query/", {"q": soql})


def _tooling_get(path: str) -> Any:
    if not ORG_ALIAS:
        raise RuntimeError("DATA360_ORG not set — pass --org on launch or export DATA360_ORG.")
    token, instance_url, version = _auth()
    url = f"{instance_url}/services/data/v{version}/tooling{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": _scrub(e.read().decode())}


def _post(path: str, body: dict) -> Any:
    if not ORG_ALIAS:
        raise RuntimeError("DATA360_ORG not set — pass --org on launch or export DATA360_ORG.")
    token, instance_url, version = _auth()
    url = f"{instance_url}/services/data/v{version}{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": _scrub(e.read().decode())}


mcp = FastMCP(
    name="data360",
    instructions=(
        "Salesforce Data Cloud (Data 360) read-only wrapper. "
        "Use to list and fetch DMOs, Calculated Insights, Data Transforms, "
        "Data Streams, Segments, Activations, and Identity Resolution rulesets. "
        "CI SQL is returned in full on /ssot/calculated-insights → `expression` (HTML-entity-encoded). "
        "Data Transform definitions including formula expressions are returned in full on "
        "/ssot/data-transforms → `definitions[].nodes[].parameters` (also HTML-entity-encoded). "
        "Older guidance saying these require manual Setup export is wrong (verified against v66.0)."
    ),
)


_DMO_SLIM_FIELDS = ("name", "label", "category", "dataSpaceName", "isEnabled", "isSegmentable")


@mcp.tool
def list_dmos(limit: int = 2000, slim: bool = True, exclude_unassigned: bool = True) -> dict:
    """List Data Model Objects across all pages. Auto-paginates.
    `slim=True` (default) returns a compact shape (name, displayName, category).
    `exclude_unassigned=True` drops UNASSIGNED stubs that inflate API counts vs the UI."""
    items = []
    offset = 0
    page_size = 100
    while len(items) < limit:
        page = _get("/ssot/data-model-objects", {"limit": page_size, "offset": offset})
        if "_error" in page:
            return page
        batch = page.get("dataModelObject") or page.get("dataModelObjects") or page.get("items") or []
        if not batch:
            break
        items.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    if exclude_unassigned:
        items = [d for d in items if (d.get("category") or "").upper() != "UNASSIGNED"]
    truncated = len(items) > limit
    items = items[:limit]
    if slim:
        items = [_slim(i, _DMO_SLIM_FIELDS) for i in items]
    return {"items": items, "count": len(items), "truncated": truncated}


@mcp.tool
def get_dmo(name: str) -> dict:
    """Get a single DMO's field schema and metadata. Name example: `ssot__Individual__dlm`."""
    return _get(f"/ssot/data-model-objects/{urllib.parse.quote(name)}")


@mcp.tool
def get_dmo_relationships(name: str) -> dict:
    """Get the relationship graph (FKs) for a DMO — use to trace join paths."""
    return _get(f"/ssot/data-model-objects/{urllib.parse.quote(name)}/relationships")


_CI_SLIM_FIELDS = ("apiName", "displayName", "dataSpace", "calculatedInsightStatus", "definitionType")


def _slim(item: dict, fields: tuple) -> dict:
    return {f: item.get(f) for f in fields}


@mcp.tool
def list_cis(limit: int = 500, slim: bool = True) -> dict:
    """List Calculated Insights across all pages. The underlying API caps at 25/page —
    this tool auto-paginates. `slim=True` (default) returns only apiName/displayName/
    dataSpace/status; pass `slim=False` for full records (dimensions, measures, expression).
    `expression` IS returned by the API for most CIs, despite older docs suggesting otherwise —
    but outer Setup-applied filters still aren't captured."""
    items = []
    offset = 0
    page_size = 25
    while len(items) < limit:
        page = _get("/ssot/calculated-insights", {"batchSize": page_size, "offset": offset})
        if "_error" in page:
            return page
        batch = page.get("collection", {}).get("items", [])
        if not batch:
            break
        items.extend(batch)
        if not page.get("collection", {}).get("nextPageToken"):
            break
        offset += page_size
    truncated = len(items) > limit
    items = items[:limit]
    if slim:
        items = [_slim(i, _CI_SLIM_FIELDS) for i in items]
    return {"items": items, "count": len(items), "truncated": truncated}


@mcp.tool
def get_ci_metadata(ci_name: str) -> dict:
    """Get a Calculated Insight's metadata (dims, metrics, output object). No SQL body."""
    return _get(f"/ssot/insight/metadata/{urllib.parse.quote(ci_name)}")


@mcp.tool
def run_ci(ci_name: str, limit: int = 100) -> dict:
    """Fetch rows from a Calculated Insight's output object."""
    return _get(f"/ssot/insight/calculated-insights/{urllib.parse.quote(ci_name)}", {"limit": limit})


_TRANSFORM_SLIM_FIELDS = ("createdDate", "creationType", "status")


@mcp.tool
def list_transforms(limit: int = 500, slim: bool = True, include_dpe: bool = False) -> dict:
    """List Data Transforms across all pages. Underlying API caps at 20/page — this tool
    auto-paginates. `slim=True` returns a compact shape (name, label, status, created).
    `include_dpe=False` (default) filters out `DPE_*` Auto Cloud / package-managed transforms."""
    items = []
    offset = 0
    page_size = 20
    while len(items) < limit:
        page = _get("/ssot/data-transforms", {"limit": page_size, "offset": offset})
        if "_error" in page:
            return page
        batch = page.get("dataTransforms", [])
        if not batch:
            break
        items.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    if not include_dpe:
        items = [
            t for t in items
            if not (t.get("name") or _transform_name(t) or "").startswith("DPE_")
        ]
    truncated = len(items) > limit
    items = items[:limit]
    if slim:
        items = [
            {
                "name": t.get("name") or _transform_name(t),
                "label": _transform_label(t),
                "status": t.get("status"),
                "createdBy": (t.get("createdBy") or {}).get("name"),
                "createdDate": t.get("createdDate"),
            }
            for t in items
        ]
    return {"items": items, "count": len(items), "truncated": truncated}


def _transform_name(t: dict) -> str | None:
    defs = t.get("definitions") or []
    return defs[0].get("name") if defs else None


def _transform_label(t: dict) -> str | None:
    defs = t.get("definitions") or []
    return defs[0].get("label") if defs else None


@mcp.tool
def get_transform(name: str) -> dict:
    """Fetch a transform's node graph. Formula expressions come back null — manual export needed."""
    return _get(f"/ssot/data-transforms/{urllib.parse.quote(name)}")


@mcp.tool
def list_dpes(slim: bool = True, process_type: str | None = None, platform: str | None = None) -> dict:
    """List Data Processing Engine flows (`BatchCalcJobDefinition` via Tooling API).
    Covers all `ProcessType`s — `DataProcessingEngine` (CDP-platform "DPE flows" in the Setup UI),
    `AdvancedAccountForecast`, `Rebates`, etc. Pass `process_type` to filter (e.g.
    `process_type='DataProcessingEngine'` for the CDP DPE flows commonly called "DPEs").
    `platform` filters on `ExecutionPlatformType` ('CDP' vs 'CRMA'). `slim=True` returns
    a compact shape; pass `slim=False` for the full record (no flow body — use `get_dpe`)."""
    fields = (
        "Id, DeveloperName, MasterLabel, ProcessType, ExecutionPlatformType, "
        "DataSpaceApiName, DefinitionRunMode, IsTemplate, NamespacePrefix, "
        "CreatedDate, LastModifiedDate"
    )
    where = []
    if process_type:
        where.append(f"ProcessType = '{process_type}'")
    if platform:
        where.append(f"ExecutionPlatformType = '{platform}'")
    soql = f"SELECT {fields} FROM BatchCalcJobDefinition"
    if where:
        soql += " WHERE " + " AND ".join(where)
    soql += " ORDER BY LastModifiedDate DESC"
    page = _tooling_query(soql)
    if "_error" in page:
        return page
    items = page.get("records", [])
    if slim:
        items = [
            {
                "name": r.get("DeveloperName"),
                "label": r.get("MasterLabel"),
                "processType": r.get("ProcessType"),
                "platform": r.get("ExecutionPlatformType"),
                "dataSpace": r.get("DataSpaceApiName"),
                "runMode": r.get("DefinitionRunMode"),
                "lastModified": r.get("LastModifiedDate"),
            }
            for r in items
        ]
    return {"items": items, "count": len(items)}


@mcp.tool
def get_dpe(name: str) -> dict:
    """Get a Data Processing Engine flow's full body — datasources, joins, aggregates, formulas,
    appends, custom nodes, atomic writebacks. `name` is the `DeveloperName`. The flow definition
    is on the `Metadata` field of `BatchCalcJobDefinition` (Tooling API)."""
    safe = name.replace("'", "\\'")
    lookup = _tooling_query(
        f"SELECT Id FROM BatchCalcJobDefinition WHERE DeveloperName = '{safe}' LIMIT 1"
    )
    if "_error" in lookup:
        return lookup
    records = lookup.get("records", [])
    if not records:
        return {"_error": 404, "_body": f"No BatchCalcJobDefinition with DeveloperName='{name}'"}
    return _tooling_get(f"/sobjects/BatchCalcJobDefinition/{records[0]['Id']}")


@mcp.tool
def list_streams(include_mappings: bool = True) -> dict:
    """List Data Streams. Pass include_mappings=True (default) to get connector type, status, row counts."""
    return _get("/ssot/data-streams", {"limit": 100, "includeMappings": str(include_mappings).lower()})


_SEGMENT_SLIM_FIELDS = (
    "apiName", "displayName", "dataSpace", "segmentStatus",
    "segmentOnApiName", "lastSegmentMemberCount", "lastPublishedEndDateTime",
)

_ACTIVATION_SLIM_FIELDS = (
    "name", "developerName", "activationType", "activationTargetName",
    "dataSpaceName", "status", "segmentApiName", "lastPublishStatus", "lastPublishDate",
)


@mcp.tool
def list_segments(limit: int = 500, slim: bool = True) -> dict:
    """List segments across all pages. Auto-paginates (server pages 20 at a time).
    `slim=True` (default) strips the HTML-encoded criteria trees — pass `slim=False`
    to retrieve `includeCriteria` / `excludeCriteria` JSON."""
    items = []
    offset = 0
    page_size = 20
    while len(items) < limit:
        page = _get("/ssot/segments", {"count": page_size, "offset": offset})
        if "_error" in page:
            return page
        batch = page.get("segments", [])
        if not batch:
            break
        items.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    truncated = len(items) > limit
    items = items[:limit]
    if slim:
        items = [_slim(i, _SEGMENT_SLIM_FIELDS) for i in items]
    return {"items": items, "count": len(items), "truncated": truncated}


@mcp.tool
def list_activations(limit: int = 500, slim: bool = True) -> dict:
    """List activations across all pages. Auto-paginates.
    `slim=True` (default) returns a compact shape; pass `slim=False` for full records
    including `relatedDmoFiltersConfig`."""
    items = []
    offset = 0
    page_size = 20
    while len(items) < limit:
        page = _get("/ssot/activations", {"count": page_size, "offset": offset})
        if "_error" in page:
            return page
        batch = page.get("activations", [])
        if not batch:
            break
        items.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    truncated = len(items) > limit
    items = items[:limit]
    if slim:
        items = [_slim(i, _ACTIVATION_SLIM_FIELDS) for i in items]
    return {"items": items, "count": len(items), "truncated": truncated}


@mcp.tool
def list_ir_rulesets() -> dict:
    """List Identity Resolution rulesets."""
    return _get("/ssot/identity-resolutions")


@mcp.tool
def run_sql(query: str) -> dict:
    """Submit a Data Cloud SQL query (async). Returns the job handle; fetch rows with `get_sql_rows`."""
    return _post("/ssot/query-sql", {"sql": query})


@mcp.tool
def get_sql_rows(job_id: str) -> dict:
    """Fetch rows for a previously submitted SQL query job."""
    return _get(f"/ssot/query-sql/{urllib.parse.quote(job_id)}/rows")


def main():
    global ORG_ALIAS
    parser = argparse.ArgumentParser(description="Data 360 MCP server (stdio)")
    parser.add_argument("--org", help="sf org alias (or set DATA360_ORG env var)")
    args, _ = parser.parse_known_args()
    if args.org:
        ORG_ALIAS = args.org

    # stdio is the default transport for Claude Code / Claude Desktop
    mcp.run()


if __name__ == "__main__":
    main()
