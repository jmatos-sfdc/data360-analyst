import json
from types import SimpleNamespace

from data360_analyst import project_setup as ps


def _skills(tmp_path, *names):
    src = tmp_path / "src"
    for n in names:
        (src / n).mkdir(parents=True)
        (src / n / "SKILL.md").write_text("---\nname: %s\n---\n" % n)
    (src / "not-a-skill").mkdir()
    return src


def test_install_skills_links_each_skill(tmp_path):
    src = _skills(tmp_path, "a", "b")
    dest = tmp_path / "dest"
    actions = ps.install_skills(dest, src, out=lambda *_: None)
    assert actions == {"a": "linked", "b": "linked"}
    assert (dest / "a").is_symlink() and (dest / "a").resolve() == (src / "a").resolve()
    assert not (dest / "not-a-skill").exists()
    assert ps.install_skills(dest, src, out=lambda *_: None) == {"a": "already linked", "b": "already linked"}


def test_install_skills_keeps_existing_copy_unless_forced(tmp_path):
    src = _skills(tmp_path, "a")
    dest = tmp_path / "dest"
    (dest / "a").mkdir(parents=True)
    (dest / "a" / "SKILL.md").write_text("old")
    assert ps.install_skills(dest, src, out=lambda *_: None)["a"].startswith("skipped")
    assert (dest / "a" / "SKILL.md").read_text() == "old"
    assert ps.install_skills(dest, src, force=True, out=lambda *_: None)["a"] == "linked"
    assert (dest / "a").is_symlink()


def test_repo_skills_all_have_skill_md():
    names = [p.name for p in ps.SKILLS_SRC.iterdir() if p.is_dir()]
    assert "data360-setup" in names
    assert all((ps.SKILLS_SRC / n / "SKILL.md").is_file() for n in names)


def test_init_project_writes_files_and_registers_mcp_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.shutil, "which", lambda _: "/usr/bin/claude")
    calls = []

    def fake_run(cmd, cwd=None, **_):
        calls.append((cmd, cwd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    proj = tmp_path / "proj"
    ps.init_project(proj, "my-org", out=lambda *_: None, run=fake_run)
    assert "`my-org`" in (proj / "CLAUDE.md").read_text()
    settings = json.loads((proj / ".claude" / "settings.json").read_text())
    assert "Bash(data360:*)" in settings["permissions"]["allow"]
    assert (proj / "queries").is_dir() and (proj / "reports").is_dir()
    add = calls[-1][0]
    assert add[:6] == ["claude", "mcp", "add", "--scope", "local", "data360"]
    assert add[add.index("--org") + 1] == "my-org"
    assert add[add.index("--data-dir") + 1] == str(proj.resolve())
    assert calls[-1][1] == proj.resolve()


def test_init_project_keeps_existing_claude_md(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("mine")
    written = ps.init_project(tmp_path, "o", register_mcp=False, out=lambda *_: None)
    assert (tmp_path / "CLAUDE.md").read_text() == "mine"
    assert tmp_path / "CLAUDE.md" not in written
