"""Packaging and portability tests for the public provenance-report skill."""

import re
from pathlib import Path

import yaml

from tests._client_terms import client_terms


ROOT = Path(__file__).parent.parent
SKILL_DIR = ROOT / ".claude" / "skills" / "data360-provenance-report"
SKILL = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"


def skill_text():
    return SKILL.read_text()


def packaged_text():
    paths = [SKILL, *sorted(REFERENCES.glob("*.md"))]
    return "\n".join(path.read_text() for path in paths)


def test_skill_frontmatter_is_valid_and_named_correctly():
    text = skill_text()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match
    frontmatter = yaml.safe_load(match.group(1))
    assert frontmatter["name"] == "data360-provenance-report"
    assert "build" in frontmatter["description"].lower()
    assert "validate" in frontmatter["description"].lower()


def test_skill_documents_all_four_modes():
    text = skill_text()
    for mode in ("Build Mode", "Validate Mode", "Enrich Mode", "Upgrade Mode"):
        assert f"## {mode}" in text


def test_skill_references_and_asset_exist():
    for name in (
        "evidence-model.md",
        "report-schema.md",
        "onboarding-guidelines.md",
        "validation-checklist.md",
    ):
        assert (REFERENCES / name).is_file()
        assert f"references/{name}" in skill_text()
    assert (SKILL_DIR / "assets" / "provenance-report.html").is_file()


def test_public_skill_does_not_expose_hidden_template_override():
    assert "--template" not in packaged_text()


def test_public_skill_has_no_client_specific_or_memory_references():
    text = packaged_text().lower()
    # Generic leak markers (never confidential — safe to name inline).
    forbidden = [
        "recordalert",
        "record_alert_item",
        "[[",
        "/users/",
        "tickets/",
        "daily-notes/",
    ]
    # Confidential client terms come from the gitignored loader.
    forbidden += client_terms()
    assert [term for term in forbidden if term in text] == []


def test_skill_uses_toolkit_containment_and_metadata_only_rules():
    text = skill_text()
    assert "Data360/" in text
    assert "Do not add account names" in text
    assert "source of truth" in text
    assert "Never infer topology" in text
    assert "Set `endpointHeader` from the endpoint node roles" in text
    assert "click an item to trace" in text


def test_toolkit_indexes_public_skill():
    claude = (ROOT / "CLAUDE.md").read_text()
    orchestrator = (
        ROOT / ".claude" / "skills" / "data360-analyst" / "SKILL.md"
    ).read_text()
    assert "/data360-provenance-report" in claude
    assert "/data360-provenance-report" in orchestrator
    assert "get_dpe" in orchestrator
