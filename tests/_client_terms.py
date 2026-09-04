"""Loader for the gitignored confidential-term list used by the guard tests.

The actual terms live in ``local/forbidden-terms.txt`` (gitignored) so no client
name is ever committed to this public repo. On a clean clone / CI the file is
absent and this returns ``[]`` — guards that use it skip their client-term
assertion, which is safe because a clean repo has nothing to catch.
"""

from pathlib import Path

_LOCAL = Path(__file__).parent.parent / "local" / "forbidden-terms.txt"


def client_terms():
    """Confidential terms (lowercased), or [] when the local file is absent."""
    if not _LOCAL.exists():
        return []
    return [
        line.strip().lower()
        for line in _LOCAL.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
