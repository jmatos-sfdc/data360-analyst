"""Tests for mcp_server.py — the pure/testable core: token redaction, slim
projection, and the lazy-refresh-on-401 retry logic (Phase B trust-hardening).

Network is fully mocked; no live org is contacted.
"""

import io
import urllib.error

import pytest

from data360_analyst import mcp_server


# ── _scrub — never leak a session token in an error body ────────────────────

def test_scrub_redacts_session_token():
    tok = "00D" + "A" * 60
    body = f'{{"error":"bad","auth":"Bearer {tok}"}}'
    out = mcp_server._scrub(body)
    assert tok not in out
    assert "[REDACTED]" in out


def test_scrub_truncates_long_body():
    assert len(mcp_server._scrub("x" * 5000)) == 2000


def test_scrub_leaves_clean_body_untouched():
    assert mcp_server._scrub('{"error":"not found"}') == '{"error":"not found"}'


# ── _slim — compact projection keeps only requested fields ──────────────────

def test_slim_projects_only_requested_fields():
    item = {"name": "X", "label": "Ex", "category": "Profile", "junk": 1}
    out = mcp_server._slim(item, ("name", "category"))
    assert out == {"name": "X", "category": "Profile"}


# ── _send — refresh token once on 401, don't loop ───────────────────────────

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._payload


def _http_401():
    return urllib.error.HTTPError(
        "https://x/api", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"expired"}')
    )


@pytest.fixture
def wired(monkeypatch):
    """Set an org alias and a cached auth tuple so _send skips the sf shell-out."""
    monkeypatch.setattr(mcp_server, "ORG_ALIAS", "demo")
    monkeypatch.setattr(mcp_server, "_auth", lambda: ("tok-stale", "https://x", "64.0"))
    refreshes = {"n": 0}

    def fake_refresh(stale):
        refreshes["n"] += 1
        return "tok-fresh"

    monkeypatch.setattr(mcp_server, "_refresh_after_401", fake_refresh)
    return refreshes


def test_send_retries_once_then_succeeds(monkeypatch, wired):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_401()
        return _FakeResponse(b'{"ok":true}')

    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", fake_urlopen)

    result = mcp_server._send(lambda t, u, v: _DummyReq())
    assert result == {"ok": True}
    assert calls["n"] == 2          # original + one retry
    assert wired["n"] == 1          # refreshed exactly once


def test_send_second_401_surfaces_error(monkeypatch, wired):
    def always_401(req, timeout=None):
        raise _http_401()

    monkeypatch.setattr(mcp_server.urllib.request, "urlopen", always_401)

    result = mcp_server._send(lambda t, u, v: _DummyReq())
    assert result["_error"] == 401
    assert "expired" in result["_body"]
    assert wired["n"] == 1          # refreshed once, did not loop


def test_send_without_org_alias_raises(monkeypatch):
    monkeypatch.setattr(mcp_server, "ORG_ALIAS", "")
    with pytest.raises(RuntimeError, match="Org alias not set"):
        mcp_server._send(lambda t, u, v: _DummyReq())


class _DummyReq:
    timeout = 60
