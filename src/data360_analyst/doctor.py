"""data360 doctor — environment and auth diagnostics.

Reports what the toolkit sees, so users don't have to infer it from docs:

  - Python version and the toolkit's own import.
  - sf CLI presence + version.
  - Which access-token path will be used, on either side of the 2026-05-27 sf CLI
    credential-redaction change (the new `sf org auth show-access-token` vs. the
    legacy `sf org display` accessToken field). See sf_auth.py for the resolver
    this mirrors.
  - Python dependencies (sqlglot, pyyaml, fastmcp, markdown) import cleanly.
  - With --org, resolves a token end-to-end and reports which path succeeded.
    The token value is never printed.

Exit status is non-zero if any check fails, so it is usable in setup scripts.
"""

import argparse
import importlib
import shutil
import sys

from data360_analyst import sf_auth

OK, WARN, FAIL = "ok", "warn", "fail"
_LABEL = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[fail]"}


def _sf_version():
    """Return sf CLI version string, or None if sf isn't on PATH / errored."""
    if not shutil.which("sf"):
        return None
    result = sf_auth._run_sf(["sf", "--version"])
    if result.returncode != 0:
        return None
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""


def _new_token_path_available():
    """True if `sf org auth show-access-token` exists (the post-change path)."""
    result = sf_auth._run_sf(
        ["sf", "org", "auth", "show-access-token", "--help"]
    )
    return result.returncode == 0


def _check_python():
    v = sys.version_info
    detail = f"Python {v.major}.{v.minor}.{v.micro}"
    status = OK if (v.major, v.minor) >= (3, 9) else WARN
    return ("Python", status, detail if status == OK else detail + " (3.9+ recommended)")


def _check_toolkit_import():
    try:
        mod = importlib.import_module("data360_analyst")
        return ("Toolkit import", OK, f"data360_analyst from {mod.__file__}")
    except Exception as exc:  # pragma: no cover - defensive
        return ("Toolkit import", FAIL, str(exc))


def _check_dependencies():
    # import name -> distribution name (for the install hint)
    deps = {"sqlglot": "sqlglot", "yaml": "pyyaml", "fastmcp": "fastmcp", "markdown": "markdown"}
    missing = []
    for import_name, dist in deps.items():
        try:
            importlib.import_module(import_name)
        except Exception:
            missing.append(dist)
    if missing:
        return ("Dependencies", FAIL, "missing: " + ", ".join(missing) + " — run setup.sh or `pip install -e .`")
    return ("Dependencies", OK, "sqlglot, pyyaml, fastmcp, markdown import")


def _check_sf_cli():
    version = _sf_version()
    if version is None:
        return [(
            "sf CLI",
            FAIL,
            "not found on PATH — install Salesforce CLI "
            "(https://developer.salesforce.com/tools/salesforcecli)",
        )]
    checks = [("sf CLI", OK, version)]
    if _new_token_path_available():
        checks.append((
            "Auth path",
            OK,
            "new: `sf org auth show-access-token` available (works post-2026-05-27 redaction)",
        ))
    else:
        checks.append((
            "Auth path",
            WARN,
            "legacy only: `sf org auth show-access-token` not found — will parse accessToken "
            "from `sf org display`; on post-2026-05-27 CLI run "
            "`sf plugins install @salesforce/plugin-auth`",
        ))
    return checks


def _check_org(org_alias):
    """Resolve a token end-to-end and report the path taken (token not printed)."""
    display = sf_auth._run_sf(["sf", "org", "display", "--target-org", org_alias, "--json"])
    if display.returncode != 0:
        return ("Org auth", FAIL, f"sf org display failed for '{org_alias}': {display.stderr.strip()}")
    try:
        result = sf_auth._parse_json_stdout(display.stdout)["result"]
    except Exception as exc:
        return ("Org auth", FAIL, f"unparseable sf org display JSON: {exc}")

    instance_url = result.get("instanceUrl", "?")

    if _new_token_path_available():
        new_cmd = sf_auth._run_sf([
            "sf", "org", "auth", "show-access-token",
            "--target-org", org_alias, "--json", "--no-prompt",
        ])
        if new_cmd.returncode == 0:
            try:
                token = sf_auth._parse_json_stdout(new_cmd.stdout)["result"]["accessToken"]
                if token:
                    return ("Org auth", OK, f"token via new path (show-access-token); instanceUrl={instance_url}")
            except Exception:
                pass

    legacy = result.get("accessToken")
    if legacy and not str(legacy).startswith("[REDACTED"):
        return ("Org auth", OK, f"token via legacy path (org display); instanceUrl={instance_url}")

    return ("Org auth", FAIL, f"no usable token for '{org_alias}' via either path; instanceUrl={instance_url}")


def run(org_alias=None):
    """Run all checks; return (results, exit_code)."""
    results = [_check_python(), _check_toolkit_import(), _check_dependencies()]
    results.extend(_check_sf_cli())
    if org_alias:
        results.append(_check_org(org_alias))
    exit_code = 1 if any(status == FAIL for _, status, _ in results) else 0
    return results, exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="data360 doctor",
        description="Diagnose the toolkit environment and which sf auth path will be used.",
    )
    parser.add_argument("--org", help="also resolve a token for this sf org alias (token never printed)")
    args = parser.parse_args(argv)

    results, exit_code = run(args.org)
    width = max(len(label) for label, _, _ in results)
    print("data360 doctor\n")
    for label, status, detail in results:
        print(f"  {_LABEL[status]} {label.ljust(width)}  {detail}")
    print()
    print("This toolkit is read-only: it issues SELECT/describe/GET calls only, never writes to the org."
          if exit_code == 0 else "One or more checks failed — see [fail] rows above.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
