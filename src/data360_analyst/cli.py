"""data360 — console entry point.

Dispatches to the toolkit's individual scripts as subcommands, e.g.:

    data360 intake --org my-alias --output-dir ~/Projects/clients/acme/Data360
    data360 ci-audit --output-dir ~/Projects/clients/acme/Data360

Each subcommand runs the target module exactly as `python -m data360_analyst.<module>`
would — same argparse, same behavior — so this stays a thin dispatcher, not a
reimplementation. Richer subcommands (`demo`, `analyze`) are added on top of this map
in later phases.
"""

import runpy
import sys
from pathlib import Path

_MODULES = {
    "intake": "data360_analyst.intake",
    "ci-audit": "data360_analyst.ci_audit",
    "ci-convert": "data360_analyst.ci_convert",
    "ci-concordance": "data360_analyst.ci_concordance",
    "ci-visualize": "data360_analyst.ci_visualize",
    "dmo-graph": "data360_analyst.dmo_graph",
    "lineage-graph": "data360_analyst.lineage_graph",
    "cluster-cis": "data360_analyst.cluster_cis_by_dmo",
    "diagram-crosscheck": "data360_analyst.diagram_crosscheck",
    "dashboard": "data360_analyst.dashboard",
    "export-sql-csv": "data360_analyst.export_sql_csv",
    "mcp-server": "data360_analyst.mcp_server",
    "provenance-render": "data360_analyst.render_provenance_report",
    "provenance-validate": "data360_analyst.validate_provenance_config",
    "provenance-extract-legacy": "data360_analyst.extract_legacy_provenance_config",
    "provenance-enrich": "data360_analyst.enrich_provenance_evidence",
    "provenance-compare": "data360_analyst.compare_provenance_reports",
}


def _run_module(subcommand, argv):
    """Run a mapped subcommand exactly as `python -m ...` would, with argv set."""
    old = sys.argv
    sys.argv = [subcommand, *argv]
    try:
        runpy.run_module(_MODULES[subcommand], run_name="__main__")
    finally:
        sys.argv = old


def _demo():
    """Run the full offline pipeline against examples/demo-org/ and open the dashboard.

    Works on a throwaway copy so the committed fixture stays clean. dmo-graph is
    excluded — it fetches CIs live from an org, which the demo intentionally has none.
    """
    import shutil
    import tempfile
    import time
    import webbrowser

    # cli.py lives at src/data360_analyst/cli.py; repo root is two parents up.
    repo_root = Path(__file__).resolve().parents[2]
    src = repo_root / "examples" / "demo-org"
    if not src.is_dir():
        print(
            f"demo snapshot not found at {src}\n"
            "The demo needs the examples/demo-org/ folder — clone the repo and run "
            "from the repo root (an installed-only copy without examples/ can't demo).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    work = Path(tempfile.mkdtemp(prefix="data360-demo-"))
    dest = work / "demo-org"
    shutil.copytree(src, dest)

    start = time.time()
    print(f"Running demo pipeline on a copy of {src.name} ({dest})\n")
    _run_module("ci-audit", ["--output-dir", str(dest)])
    _run_module("lineage-graph", ["--output-dir", str(dest)])
    _run_module("dashboard", ["--data-dir", str(dest)])

    html = dest / "reports" / "dashboard.html"
    print(f"\nDemo complete in {time.time() - start:.1f}s — no org or auth required.")
    print(f"Dashboard: {html}")
    try:
        webbrowser.open(html.as_uri())
    except Exception:
        pass  # headless / no browser — path is printed above


def _analyze(argv):
    """`data360 analyze` — snapshot an org (or reuse one) and answer high-value
    questions in the terminal instead of only writing report files.

    Modes:
      analyze <org>                live intake into a temp workspace, then answer
      analyze <org> --client acme  live intake into the per-client Data360 folder
      analyze --snapshot <dir>     offline — answer from an existing snapshot
      ... --ask "which DMOs matter" print only the section matching the question
    """
    import argparse
    import tempfile

    from data360_analyst import analyze as analyze_mod

    parser = argparse.ArgumentParser(prog="data360 analyze")
    parser.add_argument("org", nargs="?", help="sf org alias to snapshot live")
    parser.add_argument("--snapshot", help="answer from an existing snapshot dir (offline; no org)")
    parser.add_argument("--client", help="write the live snapshot to ~/Projects/clients/<name>/Data360")
    parser.add_argument("--ask", help="natural-language question; prints only the matching section")
    args = parser.parse_args(argv)

    if args.snapshot:
        data_dir = Path(args.snapshot).expanduser()
        if not data_dir.is_dir():
            print(f"snapshot not found: {data_dir}", file=sys.stderr)
            raise SystemExit(1)
    elif args.org:
        from data360_analyst import intake
        if args.client:
            data_dir = Path.home() / "Projects" / "clients" / args.client / "Data360"
        else:
            data_dir = Path(tempfile.mkdtemp(prefix="data360-analyze-"))
            print(f"No --client given; snapshotting to a temp workspace: {data_dir}\n")
        data_dir.mkdir(parents=True, exist_ok=True)
        intake.generate(args.org, str(data_dir))
        print()
    else:
        print("usage: data360 analyze <org> | --snapshot <dir> [--client <name>] [--ask \"...\"]",
              file=sys.stderr)
        raise SystemExit(1)

    answers = analyze_mod.analyze_snapshot(data_dir)
    print(analyze_mod.render_answers(answers, question=args.ask))


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "demo":
        _demo()
        return

    if len(sys.argv) >= 2 and sys.argv[1] == "analyze":
        _analyze(sys.argv[2:])
        return

    if len(sys.argv) < 2 or sys.argv[1] not in _MODULES:
        names = ", ".join(["analyze", "demo", *sorted(_MODULES)])
        print(
            f"usage: data360 <subcommand> [args...]\n\navailable subcommands: {names}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    subcommand = sys.argv.pop(1)
    sys.argv[0] = subcommand
    runpy.run_module(_MODULES[subcommand], run_name="__main__")


if __name__ == "__main__":
    main()
