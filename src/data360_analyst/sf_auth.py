"""Shared sf CLI auth helper for the Data 360 toolkit.

Resolves an access token + instance URL for a given org alias by shelling out
to the sf CLI. Works on both sides of the May 27, 2026 sf CLI breaking change
that removes accessToken from `sf org display` output:

  - Pre-change: parse accessToken directly from `sf org display --json`.
  - Post-change: call the new `sf org auth show-access-token --json --no-prompt`
    and use `sf org display --json` only for instanceUrl.

Caller code should not care which path was taken.
"""

import json
import re
import subprocess
import sys

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class SfCliMissing(RuntimeError):
    """sf CLI not found on PATH."""


class SfAuthError(RuntimeError):
    """sf CLI invocation failed or returned an unusable response."""


def _run_sf(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True)
    except FileNotFoundError:
        raise SfCliMissing(
            "sf CLI not found on PATH; install Salesforce CLI to use the Data 360 toolkit "
            "(https://developer.salesforce.com/tools/salesforcecli)."
        )
    return result


def _parse_json_stdout(stdout):
    """Parse `sf ... --json` stdout, tolerating ANSI color codes sf writes
    inline even in --json mode, plus any CLI update-nag line ahead of the
    JSON payload."""
    cleaned = _ANSI_RE.sub("", stdout)
    brace = cleaned.find("{")
    return json.loads(cleaned[brace:] if brace > 0 else cleaned)


def get_token_and_url(org_alias):
    """Return (access_token, instance_url) for the given sf org alias.

    Raises SfAuthError if neither the new nor legacy path produces a token.
    """
    display = _run_sf(["sf", "org", "display", "--target-org", org_alias, "--json"])
    if display.returncode != 0:
        raise SfAuthError(
            f"sf org display failed for '{org_alias}': {display.stderr.strip()}"
        )
    try:
        display_result = _parse_json_stdout(display.stdout)["result"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise SfAuthError(f"sf org display returned unparseable JSON: {exc}")

    instance_url = display_result.get("instanceUrl")
    if not instance_url:
        raise SfAuthError(f"sf org display did not return instanceUrl for '{org_alias}'")

    new_cmd = _run_sf([
        "sf", "org", "auth", "show-access-token",
        "--target-org", org_alias, "--json", "--no-prompt",
    ])
    if new_cmd.returncode == 0:
        try:
            token = _parse_json_stdout(new_cmd.stdout)["result"]["accessToken"]
            if token:
                return token, instance_url
        except (json.JSONDecodeError, KeyError):
            pass

    legacy_token = display_result.get("accessToken")
    if legacy_token and not str(legacy_token).startswith("[REDACTED"):
        return legacy_token, instance_url

    raise SfAuthError(
        f"Could not retrieve access token for '{org_alias}'. "
        f"On sf CLI versions released after May 27, 2026, run "
        f"`sf plugins install @salesforce/plugin-auth` to ensure "
        f"`sf org auth show-access-token` is available, then retry."
    )


def get_token_and_url_or_exit(org_alias):
    """CLI-friendly wrapper: print error and exit(1) on failure."""
    try:
        return get_token_and_url(org_alias)
    except (SfCliMissing, SfAuthError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
