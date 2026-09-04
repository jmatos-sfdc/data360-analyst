"""Tests for the data360 console dispatcher and the offline `demo` command."""

import subprocess
import sys
from pathlib import Path

import pytest

from data360_analyst import cli


ROOT = Path(__file__).parent.parent
DEMO_ORG = ROOT / "examples" / "demo-org"


def run_cli(args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "data360_analyst.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_no_subcommand_lists_available(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["data360"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_demo_is_advertised_in_usage():
    result = run_cli([])
    assert result.returncode == 1
    assert "demo" in result.stderr


def test_demo_runs_offline_and_leaves_fixture_clean(monkeypatch, capsys):
    # Don't pop a browser during the test run.
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["data360", "demo"])

    before = _fixture_snapshot()
    cli.main()
    after = _fixture_snapshot()

    assert before == after, "demo must not mutate the committed examples/demo-org fixture"

    out = capsys.readouterr().out
    assert "Demo complete" in out
    assert "dashboard.html" in out
    # The dashboard is written into a temp copy, not the repo fixture.
    dashboard_line = next(ln for ln in out.splitlines() if ln.startswith("Dashboard:"))
    dashboard = Path(dashboard_line.split("Dashboard:", 1)[1].strip())
    assert dashboard.is_file()
    assert DEMO_ORG not in dashboard.parents


def test_analyze_is_advertised_in_usage():
    result = run_cli([])
    assert "analyze" in result.stderr


def test_analyze_snapshot_offline(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["data360", "analyze", "--snapshot", str(DEMO_ORG)])
    cli.main()
    out = capsys.readouterr().out
    assert "Backbone DMOs" in out
    assert "Account__dlm" in out


def test_analyze_ask_prints_only_matching_section(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv",
                        ["data360", "analyze", "--snapshot", str(DEMO_ORG),
                         "--ask", "which DMOs matter most?"])
    cli.main()
    out = capsys.readouterr().out
    assert "Backbone DMOs" in out
    assert "Suspect CIs" not in out


def test_analyze_missing_snapshot_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["data360", "analyze", "--snapshot", "/no/such/dir"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def test_analyze_no_org_no_snapshot_exits(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["data360", "analyze"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1


def _fixture_snapshot():
    """(relative path, size) for every file under the demo-org fixture."""
    return sorted(
        (str(p.relative_to(DEMO_ORG)), p.stat().st_size)
        for p in DEMO_ORG.rglob("*")
        if p.is_file()
    )
