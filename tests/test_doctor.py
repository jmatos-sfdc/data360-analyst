"""Tests for the data360 doctor diagnostics."""

import subprocess

import pytest

from data360_analyst import doctor


def _fake_run(mapping):
    """Build a fake sf_auth._run_sf keyed on a substring of the command."""

    def run(args):
        joined = " ".join(args)
        for needle, (rc, out, err) in mapping.items():
            if needle in joined:
                return subprocess.CompletedProcess(args, rc, out, err)
        return subprocess.CompletedProcess(args, 1, "", "unmatched")

    return run


def _status(results, label):
    return next(status for lbl, status, _ in results if lbl == label)


def test_sf_missing_fails(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
    results, code = doctor.run()
    assert _status(results, "sf CLI") == doctor.FAIL
    assert code == 1


def test_new_auth_path_reported_ok(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/sf")
    monkeypatch.setattr(doctor.sf_auth, "_run_sf", _fake_run({
        "--version": (0, "@salesforce/cli/2.150.6", ""),
        "show-access-token --help": (0, "help", ""),
    }))
    results, code = doctor.run()
    assert _status(results, "sf CLI") == doctor.OK
    assert _status(results, "Auth path") == doctor.OK
    assert code == 0


def test_legacy_only_auth_path_warns(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/sf")
    monkeypatch.setattr(doctor.sf_auth, "_run_sf", _fake_run({
        "--version": (0, "@salesforce/cli/2.40.0", ""),
        "show-access-token --help": (127, "", "not found"),
    }))
    results, code = doctor.run()
    assert _status(results, "Auth path") == doctor.WARN
    # A warning is not a failure.
    assert code == 0


def test_org_check_new_path_ok_and_hides_token(monkeypatch, capsys):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/sf")
    monkeypatch.setattr(doctor.sf_auth, "_run_sf", _fake_run({
        "--version": (0, "@salesforce/cli/2.150.6", ""),
        "show-access-token --help": (0, "help", ""),
        "org display": (0, '{"result":{"instanceUrl":"https://x.my.salesforce.com","accessToken":"[REDACTED]"}}', ""),
        "show-access-token --target-org": (0, '{"result":{"accessToken":"00Dsecrettoken"}}', ""),
    }))
    code = doctor.main(["--org", "myorg"])
    out = capsys.readouterr().out
    assert code == 0
    assert "token via new path" in out
    assert "00Dsecrettoken" not in out  # token value never printed


def test_org_check_legacy_token(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/sf")
    monkeypatch.setattr(doctor.sf_auth, "_run_sf", _fake_run({
        "--version": (0, "@salesforce/cli/2.40.0", ""),
        "show-access-token --help": (127, "", "not found"),
        "org display": (0, '{"result":{"instanceUrl":"https://x.my.salesforce.com","accessToken":"00Dlegacytoken"}}', ""),
    }))
    label, status, detail = doctor._check_org("myorg")
    assert status == doctor.OK
    assert "legacy path" in detail
    assert "00Dlegacytoken" not in detail


def test_org_check_no_token_fails(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/bin/sf")
    monkeypatch.setattr(doctor.sf_auth, "_run_sf", _fake_run({
        "show-access-token --help": (127, "", "not found"),
        "org display": (0, '{"result":{"instanceUrl":"https://x.my.salesforce.com","accessToken":"[REDACTED]"}}', ""),
    }))
    label, status, detail = doctor._check_org("myorg")
    assert status == doctor.FAIL


def test_dependencies_present():
    label, status, detail = doctor._check_dependencies()
    assert status == doctor.OK
