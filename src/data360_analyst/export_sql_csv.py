#!/usr/bin/env python3
"""Export a full Data Cloud SQL result set to CSV, past the row caps.

The ad-hoc Query Editor caps display at 1000 rows, and a single POST to
/ssot/query-sql caps its response at ~90k rows — both truncate large results
SILENTLY. This pages the complete result set and writes it to a CSV.

Use when a query returns more rows than a single interactive response holds
(exports, tie-outs, bulk pulls) and you need every row, not a display page.

Pagination contract (verified 2026-08-10 against a 151,618-row query on a UAT
sandbox, API v67.0):
  - POST /ssot/query-sql {sql}         -> first chunk in `data`, plus
                                          `status.queryId` and `status.rowCount`.
  - GET  /ssot/query-sql/{qid}/rows?offset=N -> the chunk starting at row N.
  - Two end-of-data signals, both handled: a GET returns an empty `data` array,
    OR a GET at/beyond the last row returns HTTP 400. `status.rowCount` is only
    an ESTIMATE for GROUP BY / aggregate queries, so it is used for progress
    display only — NEVER as the stop condition (that truncates when the estimate
    runs low, e.g. 120k estimate for a 584k actual).

Auth reuses the toolkit's sf_auth (sibling module); the org alias is required.

Usage:
    python3 export_sql_csv.py --org myorg-uat --sql query.sql --out result.csv
    # multi-statement file: pick the Nth ';'-separated statement (comments stripped)
    python3 export_sql_csv.py --org myorg-uat --sql extracts.sql --stmt 2 --out e2.csv

Data scope: run against the target org yourself; rows land in the CSV on disk.
"""
import argparse
import csv
import json
import sys
import urllib.parse
import urllib.request
import urllib.error

from data360_analyst.sf_auth import get_token_and_url


def _version(token, instance_url):
    req = urllib.request.Request(f"{instance_url}/services/data/",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return max(v["version"] for v in json.load(r))


class _OffsetPastEnd(Exception):
    """Raised when the rows endpoint 400s on an offset at/after the true end of data."""


def _call(method, url, token, body=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # The /rows?offset= endpoint 400s when offset is at/beyond the last row (one of the
        # two EOF signals — the other is an empty data array). Surface it distinctly so the
        # pager can treat it as end-of-data rather than a hard failure.
        if e.code == 400 and "/rows?offset=" in url:
            raise _OffsetPastEnd() from None
        body_txt = e.read().decode()[:300]
        print(f"HTTP {e.code} on {method} {url}\n{body_txt}", file=sys.stderr)
        raise


def export(sql, out_path, org):
    token, instance_url = get_token_and_url(org)
    version = _version(token, instance_url)
    base = f"{instance_url}/services/data/v{version}/ssot/query-sql"
    print(f"auth ok — {instance_url} v{version}", file=sys.stderr)

    # First chunk (POST) — also carries the estimated total in status.rowCount.
    resp = _call("POST", base, token, {"sql": sql})
    status = resp.get("status", {})
    total = status.get("rowCount", 0)
    query_id = status.get("queryId")
    meta = resp.get("metadata", [])
    cols = [m["name"] for m in meta]

    collected = 0
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(cols)

        data = resp.get("data", [])
        writer.writerows(data)
        collected += len(data)
        print(f"  chunk 1: +{len(data)} (total {collected} / ~{total} est.)", file=sys.stderr)

        # Page by offset until the endpoint signals end-of-data (empty chunk or a 400 at
        # the offset). status.rowCount is an ESTIMATE for aggregate queries, so it is NOT
        # the stop condition — keep going until the data genuinely runs out.
        while True:
            rows_url = f"{base}/{urllib.parse.quote(query_id)}/rows?offset={collected}"
            try:
                page = _call("GET", rows_url, token)
            except _OffsetPastEnd:
                break  # offset at/beyond the last row — end of data
            data = page.get("data", [])
            if not data:
                break
            writer.writerows(data)
            collected += len(data)
            print(f"  +{len(data)} (total {collected} / ~{total} est.)", file=sys.stderr)

    print(f"\nDONE — {collected} rows written to {out_path} "
          f"(estimate was ~{total}; exact count is the {collected} written)", file=sys.stderr)
    return collected


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--org", required=True, help="sf CLI org alias (e.g. myorg-uat, myorg-dev)")
    ap.add_argument("--sql", required=True, help="path to a .sql file")
    ap.add_argument("--out", required=True, help="output CSV path")
    ap.add_argument("--stmt", type=int, default=None,
                    help="if the file has multiple ';'-separated statements, pick the Nth (1-based)")
    args = ap.parse_args()

    from pathlib import Path
    text = Path(args.sql).read_text()
    if args.stmt is not None:
        # Strip ALL comment lines FIRST, then split on ';'. Splitting first is wrong:
        # a ';' inside comment prose (e.g. "Jorge runs; results feed...") would break a
        # statement mid-comment and leak the fragment into the SQL.
        code_lines = [ln for ln in text.splitlines() if not ln.strip().startswith("--")]
        code = "\n".join(code_lines)
        stmts = [s.strip() for s in code.split(";") if s.strip()]
        if args.stmt > len(stmts):
            print(f"only {len(stmts)} statements found", file=sys.stderr)
            sys.exit(1)
        sql = stmts[args.stmt - 1]
    else:
        sql = text.strip().rstrip(";")

    export(sql, args.out, args.org)


if __name__ == "__main__":
    main()
