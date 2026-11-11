"""Make the toolkit usable from any project folder.

Two subcommands:

  data360 install-skills [--copy] [--force]
      Link (or copy) the toolkit's Claude Code skills into ~/.claude/skills so
      they load in every project, not only when Claude Code runs inside this repo.

  data360 init-project <dir> --org <alias> [--no-mcp] [--force]
      Turn a folder into a Data 360 analysis project: a CLAUDE.md that tells
      Claude where the snapshot lives, a .claude/settings.json that pre-approves
      the read-only tools, and the MCP server registered for that folder only
      (local scope, so no approval prompt and no effect on other projects).
      It does not snapshot the org; run `data360 intake --org <alias>
      --output-dir <dir>` next (the data360-setup skill does both).
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SKILLS_SRC = Path(__file__).resolve().parents[2] / ".claude" / "skills"
SKILLS_DEST = Path.home() / ".claude" / "skills"

PROJECT_CLAUDE_MD = """\
# Data 360 analysis: {org}

This folder is a Data 360 analysis project for the org `{org}`, set up with the data360-analyst toolkit.

- The snapshot lives here: `object-model/` (YAML sidecars), `queries/` (CI SQL), `reports/` (audit, complexity,
  lineage, dashboard). `object-model/index.yaml` says when it was taken.
- Answer from the snapshot first with the `data360` CLI, e.g. `data360 analyze --snapshot .`,
  `data360 analyze --snapshot . --ask "identity resolution"`, `data360 ci-audit --output-dir .`,
  `data360 ci-complexity --output-dir .`, `data360 ci-convert --input <file.sql>`. Say once that the answer comes
  from the snapshot.
- Go live (the `data360` MCP tools, or `data360 intake` to refresh) only when asked, or when the answer can't come
  from the snapshot (row counts, current job status).
- Live SQL returns counts and totals only. When `run_sql` or `get_ci_rows` comes back `_blocked`, the query would
  return record-level values (customer data): show the `query_editor_sql` for the user to run in Query Editor and
  don't rewrite the query to get past the check. For every row as a file, the user runs
  `! data360 export-sql-csv --org {org} --sql <file.sql> --out <file.csv>` themselves; don't run it or read the CSV.
- To explain one CI on a page someone can hand on (its semantic model and the SQL, annotated):
  `data360 ci-visualize --client-root . --name <CI>__cio --out reports/<CI>-onboarding.html`.
- Lead with the answer in a few short lines. When asked for proof, show the SQL lines behind the finding and the
  confirm query from `reports/ci-audit.md`.
- The toolkit is read-only against the org. Don't change files in `object-model/` by hand; re-run intake instead.
"""

PROJECT_SETTINGS = {
    "permissions": {
        "allow": ["Bash(data360:*)", "Read", "Grep", "Glob", "Skill", "mcp__data360"],
        # Edit rules cover every file-writing tool. The CSV export writes customer rows, so only the user runs it.
        "deny": ["Edit(object-model/**)", "Bash(data360 export-sql-csv:*)"],
    }
}


def install_skills(dest=SKILLS_DEST, src=SKILLS_SRC, copy=False, force=False, out=print):
    """Link or copy each skill folder into `dest`. Returns {name: action}."""
    if not src.is_dir():
        raise SystemExit(f"skills not found at {src}; install-skills needs a clone of the repo")
    dest.mkdir(parents=True, exist_ok=True)
    actions = {}
    for skill in sorted(p for p in src.iterdir() if (p / "SKILL.md").is_file()):
        target = dest / skill.name
        if target.is_symlink() and target.resolve() == skill.resolve() and not copy:
            actions[skill.name] = "already linked"
            continue
        if target.exists() or target.is_symlink():
            if not force:
                actions[skill.name] = "skipped (exists; --force replaces it)"
                continue
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
        if copy:
            shutil.copytree(skill, target)
            actions[skill.name] = "copied"
        else:
            target.symlink_to(skill, target_is_directory=True)
            actions[skill.name] = "linked"
    for name, action in actions.items():
        out(f"  {name:32} {action}")
    return actions


def _venv_python():
    """The venv's stable `python` name, so a rebuild on a newer Python doesn't break the registration."""
    stable = Path(sys.prefix) / "bin" / "python"
    return str(stable) if stable.exists() else sys.executable


def mcp_command(org, project_dir):
    """The `claude mcp add` argv that registers the server for this folder only."""
    return ["claude", "mcp", "add", "--scope", "local", "data360", "--",
            _venv_python(), "-m", "data360_analyst.mcp_server",
            "--org", org, "--data-dir", str(project_dir)]


def init_project(project_dir, org, register_mcp=True, force=False, out=print, run=subprocess.run):
    """Write the project files and register the MCP server. Returns the files written."""
    project_dir = Path(project_dir).expanduser().resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    written = []
    files = {
        project_dir / "CLAUDE.md": PROJECT_CLAUDE_MD.format(org=org),
        project_dir / ".claude" / "settings.json": json.dumps(PROJECT_SETTINGS, indent=2) + "\n",
    }
    for path, body in files.items():
        if path.exists() and not force:
            out(f"  keep     {path.relative_to(project_dir)} (exists; --force overwrites)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        written.append(path)
        out(f"  wrote    {path.relative_to(project_dir)}")
    for sub in ("queries", "reports"):
        (project_dir / sub).mkdir(exist_ok=True)

    cmd = mcp_command(org, project_dir)
    if not register_mcp:
        out("  skipped  MCP registration (--no-mcp). To register later, from this folder:\n    " + " ".join(cmd))
    elif not shutil.which("claude"):
        out("  skipped  MCP registration: `claude` not on PATH. From this folder, run:\n    " + " ".join(cmd))
    else:
        # Local scope is keyed to the working directory, so run it from the project folder.
        run(["claude", "mcp", "remove", "--scope", "local", "data360"], cwd=project_dir,
            capture_output=True, text=True)
        r = run(cmd, cwd=project_dir, capture_output=True, text=True)
        if r.returncode == 0:
            out("  added    MCP server `data360` for this folder (local scope); restart Claude Code to load it")
        else:
            out(f"  FAILED   MCP registration: {(r.stderr or r.stdout).strip()[:300]}")
    return written


def main_install_skills(argv=None):
    p = argparse.ArgumentParser(prog="data360 install-skills",
                                description="Make the toolkit's Claude Code skills available in every project.")
    p.add_argument("--copy", action="store_true", help="copy instead of symlink (no live updates from the repo)")
    p.add_argument("--force", action="store_true", help="replace skills that already exist under ~/.claude/skills")
    p.add_argument("--dest", default=str(SKILLS_DEST), help=argparse.SUPPRESS)
    a = p.parse_args(argv)
    print(f"Installing skills into {a.dest}")
    install_skills(Path(a.dest), copy=a.copy, force=a.force)
    print("Restart Claude Code, then in any project folder ask: \"set up data360 analyst for this project\".")


def main_init_project(argv=None):
    p = argparse.ArgumentParser(prog="data360 init-project",
                                description="Set up a folder as a Data 360 analysis project.")
    p.add_argument("dir", nargs="?", default=".", help="project folder (default: current folder)")
    p.add_argument("--org", required=True, help="sf org alias")
    p.add_argument("--no-mcp", action="store_true", help="don't register the MCP server")
    p.add_argument("--force", action="store_true", help="overwrite CLAUDE.md and .claude/settings.json")
    a = p.parse_args(argv)
    print(f"Setting up {Path(a.dir).expanduser().resolve()} for org '{a.org}'")
    init_project(a.dir, a.org, register_mcp=not a.no_mcp, force=a.force)
    print(f"\nNext: data360 intake --org {a.org} --output-dir {a.dir}")
