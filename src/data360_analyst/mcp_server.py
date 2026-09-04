#!/usr/bin/env python3
"""
Data 360 MCP Server (local, stdio)

Wraps the Salesforce Data Cloud `/services/data/vXX.X/ssot/*` REST endpoints
(plus a slice of the Tooling API for Data Processing Engine flows)
as Model Context Protocol tools so Claude Code can drill into an org iteratively
instead of re-running intake.py.

Launch from Claude Code by registering this as an MCP server:

    claude mcp add data360 \
        /path/to/data360-analyst/.venv/bin/python \
        -m data360_analyst.mcp_server \
        --org <sf-alias>

The server shells out to the sf CLI (`sf org auth show-access-token` on newer
versions, `sf org display` on older ones) to get a fresh access token —
simple and reuses the existing sf CLI auth. Access tokens are
Data-Cloud-short-lived (~2h); shelling out per call dodges refresh logic at
the cost of a few hundred ms per tool call.
"""

import argparse
import asyncio
import html
import json
import os
import re
import sys
import threading
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data360_analyst.sf_auth import get_token_and_url, SfAuthError, SfCliMissing

try:
    import yaml
except ImportError:
    yaml = None  # the lineage tools surface a clear error when this is None


_TOKEN_PATTERN = re.compile(r"(?i)(bearer\s+)?00D[A-Za-z0-9!._]{40,}")


def _scrub(body: str) -> str:
    """Redact anything that looks like a Salesforce session token from error bodies."""
    return _TOKEN_PATTERN.sub("[REDACTED]", body)[:2000]

try:
    from fastmcp import FastMCP
except ImportError:
    print("ERROR: fastmcp not installed. Use the bundled .venv.", file=sys.stderr)
    sys.exit(1)


# Org alias — primary env var is D360_ANALYST_ORG_ALIAS; DATA360_ORG kept as a
# transitional alias so existing `claude mcp add` registrations keep working.
ORG_ALIAS = os.environ.get("D360_ANALYST_ORG_ALIAS") or os.environ.get("DATA360_ORG", "")


def _sf_token(org_alias: str) -> tuple[str, str]:
    return get_token_and_url(org_alias)


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


# Lazy-refresh-on-401 auth. Cache token in memory; only re-shell to sf when a
# request returns 401. The mutex coalesces concurrent 401s — five in-flight
# requests that all expire on the same boundary trigger one sf invocation.
_AUTH_CACHE: dict = {"token": None, "instance_url": None, "version": None}
_AUTH_LOCK = threading.Lock()


def _populate_cache_locked() -> None:
    """Caller must hold _AUTH_LOCK."""
    token, instance_url = _sf_token(ORG_ALIAS)
    version = _api_version(token, instance_url)
    _AUTH_CACHE.update(token=token, instance_url=instance_url, version=version)


def _auth() -> tuple[str, str, str]:
    if _AUTH_CACHE["token"]:
        return _AUTH_CACHE["token"], _AUTH_CACHE["instance_url"], _AUTH_CACHE["version"]
    with _AUTH_LOCK:
        if not _AUTH_CACHE["token"]:
            _populate_cache_locked()
    return _AUTH_CACHE["token"], _AUTH_CACHE["instance_url"], _AUTH_CACHE["version"]


def _refresh_after_401(stale_token: str) -> str:
    """Refresh the cached token after a 401. Coalesces concurrent callers:
    if another thread already refreshed past `stale_token`, return that one
    without re-shelling. Returns the new token."""
    with _AUTH_LOCK:
        if _AUTH_CACHE["token"] and _AUTH_CACHE["token"] != stale_token:
            return _AUTH_CACHE["token"]
        _populate_cache_locked()
        return _AUTH_CACHE["token"]


def _send(req_factory, retried: bool = False) -> Any:
    """Send a request built by `req_factory(token, instance_url, version)`.

    On HTTP 401, refresh the token once and retry. On the second 401, surface
    the error to the caller — repeated 401 indicates permissions/scope, not
    expiry, and looping wastes shell-outs.
    """
    if not ORG_ALIAS:
        raise RuntimeError(
            "Org alias not set — pass --org on launch or export D360_ANALYST_ORG_ALIAS."
        )
    token, instance_url, version = _auth()
    req = req_factory(token, instance_url, version)
    try:
        with urllib.request.urlopen(req, timeout=req.timeout if hasattr(req, "timeout") else 60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = _scrub(e.read().decode())
        if e.code == 401 and not retried:
            _refresh_after_401(token)
            return _send(req_factory, retried=True)
        return {"_error": e.code, "_body": body}


def _get(path: str, params: dict | None = None) -> Any:
    def factory(token, instance_url, version):
        url = f"{instance_url}/services/data/v{version}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        req.timeout = 60
        return req
    return _send(factory)


def _tooling_query(soql: str) -> Any:
    return _get("/tooling/query/", {"q": soql})


def _tooling_get(path: str) -> Any:
    def factory(token, instance_url, version):
        url = f"{instance_url}/services/data/v{version}/tooling{path}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        req.timeout = 60
        return req
    return _send(factory)


def _post(path: str, body: dict) -> Any:
    encoded = json.dumps(body).encode()
    def factory(token, instance_url, version):
        url = f"{instance_url}/services/data/v{version}{path}"
        req = urllib.request.Request(
            url,
            data=encoded,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        req.timeout = 120
        return req
    return _send(factory)


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


_DLO_SLIM_FIELDS = ("name", "label", "category", "dataSpaceName")


@mcp.tool
def list_dlos(limit: int = 1000, category: str | None = None,
              dataspace: str | None = None, slim: bool = True) -> dict:
    """List Data Lake Objects across all pages. Walks `nextPageUrl` (the
    server caps actual page size around 20 even when `limit` is higher).
    `category` filters to one of: Profile, Engagement, Other, Directory_Table,
    Insights. `dataspace` scopes to a named data space."""
    items = []
    params = {"limit": 100, "offset": 0}
    if category:
        params["category"] = category
    if dataspace:
        params["dataspace"] = dataspace
    page = _get("/ssot/data-lake-objects", params)
    while True:
        if "_error" in page:
            return page
        items.extend(page.get("dataLakeObjects") or [])
        if len(items) >= limit:
            break
        next_path = page.get("nextPageUrl")
        if not next_path:
            break
        # nextPageUrl is /services/data/vXX/ssot/... — strip the prefix and
        # any leading version path so _get's path arg is just /ssot/...
        if "/ssot/" in next_path:
            page = _get(next_path[next_path.index("/ssot/"):], None)
        else:
            break
    truncated = len(items) > limit
    items = items[:limit]
    if slim:
        items = [_slim(i, _DLO_SLIM_FIELDS) for i in items]
    return {"items": items, "count": len(items), "truncated": truncated}


@mcp.tool
def get_dlo(name: str, dataspace: str | None = None) -> dict:
    """Get a single DLO's full schema (fields, dataspace info, modified/event
    field names). Name example: `Account_00D000000000000__dll`."""
    params = {"dataspace": dataspace} if dataspace else None
    return _get(f"/ssot/data-lake-objects/{urllib.parse.quote(name)}", params)


@mcp.tool
def list_dlo_mappings(dmo_developer_name: str | None = None,
                      dlo_developer_name: str | None = None,
                      source_object_name: str | None = None,
                      dataspace: str | None = None,
                      slim: bool = True) -> dict:
    """List DLO→DMO mappings. The endpoint requires at least one of
    `dmo_developer_name` or `source_object_name`. Use this to close the
    Stream→DLO→DMO lineage chain — pass a DMO name to see every DLO that
    feeds it. Response is wrapped in `objectSourceTargetMaps`; each record has
    `sourceEntityDeveloperName` (DLO) and `targetEntityDeveloperName` (DMO)."""
    if not (dmo_developer_name or source_object_name):
        return {"_error": 400,
                "_body": "list_dlo_mappings requires dmo_developer_name or source_object_name"}
    params = {}
    if dmo_developer_name:
        params["dmoDeveloperName"] = dmo_developer_name
    if dlo_developer_name:
        params["dloDeveloperName"] = dlo_developer_name
    if source_object_name:
        params["sourceObjectName"] = source_object_name
    if dataspace:
        params["dataspace"] = dataspace
    page = _get("/ssot/data-model-object-mappings", params)
    if "_error" in page:
        return page
    items = page.get("objectSourceTargetMaps") or []
    if slim:
        items = [
            {
                "developerName": m.get("developerName"),
                "dloDeveloperName": m.get("sourceEntityDeveloperName"),
                "dmoDeveloperName": m.get("targetEntityDeveloperName"),
                "status": m.get("status"),
                "fieldMappingsCount": len(m.get("fieldMappings") or []) or None,
            }
            for m in items
        ]
    return {"items": items, "count": len(items)}


@mcp.tool
def get_dlo_mapping(developer_name: str) -> dict:
    """Get a single DLO→DMO mapping including field-level mappings."""
    return _get(f"/ssot/data-model-object-mappings/{urllib.parse.quote(developer_name)}")


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
def get_ci_sql(ci_name: str) -> dict:
    """Fetch a Calculated Insight's SQL body (the `expression` field, HTML-decoded).

    Returns `{ci_name, sql, api_version, fetched_at}`. If the CI exists but has
    no populated `expression`, `sql` is an empty string and a `warning` field is
    included. On 404 (CI not found) or other HTTP errors, returns the underlying
    `{_error, _body}` shape so callers can distinguish."""
    resp = _get(f"/ssot/calculated-insights/{urllib.parse.quote(ci_name)}")
    if "_error" in resp:
        return resp
    expression = resp.get("expression") or ""
    result = {
        "ci_name": ci_name,
        "sql": html.unescape(expression),
        "api_version": f"v{_AUTH_CACHE['version']}",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if not expression:
        result["warning"] = "CI has no populated `expression` field (empty or malformed)."
    return result


@mcp.tool
def get_ci_rows(ci_name: str, limit: int = 100) -> dict:
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
    """Fetch a transform's node graph. Formula expressions are returned in full under
    `definitions[].nodes[].parameters` (HTML-entity-encoded — decode before use)."""
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


# ── Lineage tools ────────────────────────────────────────────────────────────
# These read the on-disk lineage.yaml produced by lineage_graph.py. Live API
# calls aren't needed — the snapshot is the source of truth for graph queries.

DATA_DIR = os.environ.get("D360_ANALYST_DATA_DIR", "")

_LINEAGE_CACHE: dict = {"path": None, "mtime": None, "graph": None,
                       "out_adj": None, "in_adj": None, "node_index": None}
_LINEAGE_LOCK = threading.Lock()


def _lineage_path() -> Path:
    if not DATA_DIR:
        raise RuntimeError(
            "Lineage tools need D360_ANALYST_DATA_DIR set to the client Data360 folder "
            "(the parent of object-model/)."
        )
    return Path(DATA_DIR).expanduser() / "object-model" / "lineage.yaml"


def _load_lineage() -> dict:
    """Load lineage.yaml, rebuilding the in-memory adjacency lists if the file
    has changed on disk. Cheap on cache hits — just an mtime check."""
    if yaml is None:
        raise RuntimeError("pyyaml not installed — lineage tools unavailable")
    path = _lineage_path()
    if not path.exists():
        raise RuntimeError(f"{path} not found — run lineage_graph.py after intake.py")
    mtime = path.stat().st_mtime
    with _LINEAGE_LOCK:
        if _LINEAGE_CACHE["path"] == str(path) and _LINEAGE_CACHE["mtime"] == mtime:
            return _LINEAGE_CACHE
        graph = yaml.safe_load(path.read_text()) or {}
        node_index = {(n["name"], n["type"]): n for n in graph.get("nodes", [])}
        # Allow lookup by name only if the name is unique across types
        # (almost always true; DMOs and CIs use disjoint suffixes).
        by_name = defaultdict(list)
        for n in graph.get("nodes", []):
            by_name[n["name"]].append(n)
        out_adj = defaultdict(list)
        in_adj = defaultdict(list)
        for e in graph.get("edges", []):
            out_adj[e["from"]].append(e)
            in_adj[e["to"]].append(e)
        _LINEAGE_CACHE.update(
            path=str(path), mtime=mtime, graph=graph,
            out_adj=out_adj, in_adj=in_adj, node_index=node_index,
            by_name=by_name,
        )
        return _LINEAGE_CACHE


def _resolve_node(name: str) -> dict | None:
    """Return the node dict for `name`, or None if unknown / ambiguous."""
    cache = _load_lineage()
    candidates = cache["by_name"].get(name) or []
    if len(candidates) == 1:
        return candidates[0]
    return None


def _walk(start: str, direction: str, depth: int, edge_types: list[str] | None) -> dict:
    """BFS in `direction` ('upstream' | 'downstream') up to `depth` hops."""
    cache = _load_lineage()
    adj = cache["in_adj"] if direction == "upstream" else cache["out_adj"]
    edge_filter = set(edge_types) if edge_types else None

    if start not in cache["by_name"]:
        return {"_error": f"Node '{start}' not found in lineage"}

    visited_nodes = {start}
    visited_edges = []
    frontier = deque([(start, 0)])
    while frontier:
        node, d = frontier.popleft()
        if d >= depth:
            continue
        for edge in adj.get(node, []):
            if edge_filter and edge["relation"] not in edge_filter:
                continue
            visited_edges.append(edge)
            other = edge["from"] if direction == "upstream" else edge["to"]
            if other not in visited_nodes:
                visited_nodes.add(other)
                frontier.append((other, d + 1))

    nodes = [n for n in cache["graph"].get("nodes", []) if n["name"] in visited_nodes]
    return {
        "focus": start,
        "direction": direction,
        "depth": depth,
        "nodes": nodes,
        "edges": visited_edges,
        "reachable": sorted(visited_nodes - {start}),
    }


@mcp.tool
def get_upstream(name: str, depth: int = 3, edge_types: list[str] | None = None) -> dict:
    """Ancestors of `name` up to `depth` hops away. Returns a focused subgraph
    (just the touched nodes + edges) plus a flat `reachable` list of names.
    `edge_types` filters to specific relations — pass `["read_by"]` for SQL
    lineage only, or omit to walk everything (`populates`, `read_by`,
    `criteria_uses`, `activates`, etc.)."""
    return _walk(name, "upstream", depth, edge_types)


@mcp.tool
def get_downstream(name: str, depth: int = 3, edge_types: list[str] | None = None) -> dict:
    """Descendants of `name` up to `depth` hops. Same shape as `get_upstream`.
    Use to answer 'if I change this, what's affected?'."""
    return _walk(name, "downstream", depth, edge_types)


@mcp.tool
def find_orphans(node_type: str) -> dict:
    """Nodes of `node_type` that aren't connected the way you'd expect:
      - CI / Segment / Activation: no incoming OR no outgoing edge — likely
        abandoned or misconfigured.
      - DMO / Stream: no outgoing edge (nothing reads / depends on them).
    Pass node_type as one of: DMO, CI, Stream, Segment, Activation."""
    cache = _load_lineage()
    out_adj = cache["out_adj"]
    in_adj = cache["in_adj"]
    nodes = [n for n in cache["graph"].get("nodes", []) if n["type"] == node_type]
    if not nodes:
        return {"_error": f"No nodes of type '{node_type}' in lineage"}

    findings = []
    for n in nodes:
        name = n["name"]
        outgoing = len(out_adj.get(name, []))
        incoming = len(in_adj.get(name, []))
        reasons = []
        if node_type in ("CI", "Segment", "Activation"):
            if incoming == 0:
                reasons.append("no upstream sources")
            if outgoing == 0:
                reasons.append("nothing downstream consumes it")
        else:  # DMO, Stream
            if outgoing == 0:
                reasons.append("no downstream consumers")
        if reasons:
            findings.append({"name": name, "type": node_type,
                            "outDegree": outgoing, "inDegree": incoming,
                            "reasons": reasons})
    return {"nodeType": node_type, "totalScanned": len(nodes),
            "orphans": findings, "orphanCount": len(findings)}


@mcp.tool
def shortest_path(from_name: str, to_name: str) -> dict:
    """Shortest directed path between two nodes. Returns the chain of nodes
    and the edges connecting them, or `{path: null}` if disconnected. Use to
    answer 'how does data flow from this stream / DMO into this activation?'."""
    cache = _load_lineage()
    if from_name not in cache["by_name"] or to_name not in cache["by_name"]:
        return {"_error": "from_name or to_name not in lineage"}
    out_adj = cache["out_adj"]

    # BFS recording predecessor edges
    parent = {from_name: None}
    parent_edge = {}
    queue = deque([from_name])
    while queue:
        node = queue.popleft()
        if node == to_name:
            break
        for edge in out_adj.get(node, []):
            nxt = edge["to"]
            if nxt not in parent:
                parent[nxt] = node
                parent_edge[nxt] = edge
                queue.append(nxt)

    if to_name not in parent:
        return {"from": from_name, "to": to_name, "path": None,
                "reason": "no directed path exists"}

    # Reconstruct the chain
    chain = []
    edges = []
    cur = to_name
    while cur is not None:
        chain.append(cur)
        if cur in parent_edge:
            edges.append(parent_edge[cur])
        cur = parent.get(cur)
    chain.reverse()
    edges.reverse()
    return {"from": from_name, "to": to_name, "hops": len(chain) - 1,
            "path": chain, "edges": edges}


@mcp.tool
def lineage_summary() -> dict:
    """Org-wide rollup: counts by type and relation, top-10 most-fanned-out
    nodes (most outgoing edges), top-10 most-depended-on nodes (most incoming),
    and the count of `unresolved` edges (gaps the graph couldn't bridge —
    typically streams whose DLO→DMO link isn't on the public API)."""
    cache = _load_lineage()
    g = cache["graph"]
    out_adj, in_adj = cache["out_adj"], cache["in_adj"]
    by_name_type = {(n["name"], n["type"]): n for n in g.get("nodes", [])}

    # Recompute counts at call time so they stay honest if anyone hand-edits
    # the YAML between runs. Cheap — single pass over the loaded graph.
    counts_by_type: dict = {}
    for n in g.get("nodes", []):
        counts_by_type[n["type"]] = counts_by_type.get(n["type"], 0) + 1
    counts_by_relation: dict = {}
    for e in g.get("edges", []):
        counts_by_relation[e["relation"]] = counts_by_relation.get(e["relation"], 0) + 1

    out_ranked = sorted(
        [(name, len(edges)) for name, edges in out_adj.items()],
        key=lambda x: -x[1])[:10]
    in_ranked = sorted(
        [(name, len(edges)) for name, edges in in_adj.items()],
        key=lambda x: -x[1])[:10]

    def _label(name):
        for (n, _t), node in by_name_type.items():
            if n == name:
                return f"{name} ({node['type']})"
        return name

    return {
        "counts": {
            "nodes": len(g.get("nodes", [])),
            "edges": len(g.get("edges", [])),
            "unresolved": len(g.get("unresolved", [])),
            "byType": counts_by_type,
            "byRelation": counts_by_relation,
        },
        "topFanOut": [{"name": _label(n), "outDegree": d} for n, d in out_ranked],
        "topFanIn": [{"name": _label(n), "inDegree": d} for n, d in in_ranked],
        "unresolvedCount": len(g.get("unresolved", [])),
    }


def main():
    global ORG_ALIAS, DATA_DIR
    parser = argparse.ArgumentParser(description="Data 360 MCP server (stdio)")
    parser.add_argument("--org", help="sf org alias (or set D360_ANALYST_ORG_ALIAS env var)")
    parser.add_argument("--data-dir", help="Path to the client Data360 folder for lineage tools "
                                            "(or set D360_ANALYST_DATA_DIR env var)")
    args, _ = parser.parse_known_args()
    if args.org:
        ORG_ALIAS = args.org
    if args.data_dir:
        DATA_DIR = args.data_dir

    # stdio is the default transport for Claude Code / Claude Desktop
    mcp.run()


if __name__ == "__main__":
    main()
