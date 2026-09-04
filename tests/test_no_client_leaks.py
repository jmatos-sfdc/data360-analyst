"""Repo-wide guard: no client-specific terms in packaged, publishable files.

Sweeps everything that ships publicly — all skills, src, docs, the README, and
test fixtures — for the confidential term list in ``local/forbidden-terms.txt``.
That list is gitignored, so no client name is committed to this public repo; on a
clean clone the list is empty and this guard skips (nothing to catch).
"""

import subprocess
from pathlib import Path

import pytest

from tests._client_terms import client_terms

ROOT = Path(__file__).parent.parent

# Only scan files that ship publicly. Deliberately excludes tests/*.py (the guard
# harnesses reference terms only via the gitignored loader, never as literals).
SCAN_PREFIXES = (".claude/skills/", "src/", "docs/", "tests/fixtures/")
SCAN_FILES = {"README.md"}


def _tracked_scan_files():
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for rel in out:
        if rel in SCAN_FILES or rel.startswith(SCAN_PREFIXES):
            p = ROOT / rel
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}:
                continue
            yield rel, p


def test_no_client_specific_terms_in_public_files():
    terms = client_terms()
    if not terms:
        pytest.skip("local/forbidden-terms.txt absent — clean clone, nothing to check")
    hits = []
    for rel, path in _tracked_scan_files():
        for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            low = line.lower()
            if any(t in low for t in terms):
                hits.append(f"{rel}:{i}: {line.strip()}")
    assert not hits, "client-specific terms found in public files:\n" + "\n".join(hits)
