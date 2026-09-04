"""Tests for intake.py — focused on schema-drift detection (Phase B).

Drift detection can't run a full live intake in a test, so it exercises the
pure `detect_schema_drift` function and the `emit_yaml_sidecars` write path
directly, feeding raw artifact dicts the way the live endpoints would return
them. The end-to-end case is the plan's E-step: write a snapshot with a field
present, re-run with that field dropped, assert intake warns.
"""

import yaml

from data360_analyst import intake


def _emit(tmp_path, cis, api_version="64.0"):
    return intake.emit_yaml_sidecars(
        str(tmp_path),
        org_meta={
            "client": "demo",
            "orgAlias": "demo",
            "instanceUrl": "https://example.my.salesforce.com",
            "apiVersion": api_version,
        },
        dmos=[{"name": "ssot__Account__dlm", "label": "Account", "category": "Profile", "fields": []}],
        dlos=[],
        mappings=[],
        cis=cis,
        transforms=[],
        streams=[],
        segments=[],
        activations=[],
        ir_items=[],
    )


def _ci(with_measures=True):
    doc = {
        "apiName": "Customer_Value__cio",
        "displayName": "Customer Value",
        "calculatedInsightStatus": "Active",
        "expression": "SELECT 1",
        "dimensions": [],
    }
    if with_measures:
        doc["measures"] = [{"apiName": "total", "type": "Number"}]
    return doc


# ── detect_schema_drift (pure) ──────────────────────────────────────────────

def test_no_prior_schema_never_warns():
    items = {"cis": [_ci()]}
    schema, warnings = intake.detect_schema_drift(None, items, "64.0")
    assert warnings == []
    assert "measures" in schema["fields"]["cis"]


def test_dropped_field_warns():
    prior = {"apiVersion": "64.0", "fields": {"cis": ["apiName", "measures", "expression"]}}
    items = {"cis": [_ci(with_measures=False)]}
    schema, warnings = intake.detect_schema_drift(prior, items, "64.0")
    assert any("measures" in w for w in warnings)
    assert "measures" not in schema["fields"]["cis"]


def test_field_still_present_does_not_warn():
    prior = {"apiVersion": "64.0", "fields": {"cis": ["apiName", "measures", "expression"]}}
    items = {"cis": [_ci(with_measures=True)]}
    _, warnings = intake.detect_schema_drift(prior, items, "64.0")
    assert warnings == []


def test_empty_category_is_not_drift():
    # A category with zero artifacts this run must not read as "everything dropped".
    prior = {"apiVersion": "64.0", "fields": {"cis": ["apiName", "measures"]}}
    schema, warnings = intake.detect_schema_drift(prior, {"cis": []}, "64.0")
    assert warnings == []
    assert schema["fields"]["cis"] == ["apiName", "measures"]  # carried forward


def test_api_version_change_warns():
    prior = {"apiVersion": "64.0", "fields": {"cis": ["apiName"]}}
    _, warnings = intake.detect_schema_drift(prior, {"cis": [_ci()]}, "65.0")
    assert any("64.0" in w and "65.0" in w for w in warnings)


# ── end-to-end write path (the plan's E-step) ───────────────────────────────

def test_rerun_with_dropped_field_warns_and_flags_index(tmp_path):
    # Run 1: healthy snapshot with `measures` present — establishes fingerprint.
    stats1 = _emit(tmp_path, [_ci(with_measures=True)])
    assert not stats1["drift"]

    manifest = yaml.safe_load((tmp_path / "object-model" / "_manifest.yaml").read_text())
    assert "measures" in manifest[intake._SCHEMA_KEY]["fields"]["cis"]

    # Run 2: endpoint dropped `measures` — must warn, not silently write malformed.
    stats2 = _emit(tmp_path, [_ci(with_measures=False)])
    assert stats2["drift"]
    assert any("measures" in w for w in stats2["drift"])

    index = yaml.safe_load((tmp_path / "object-model" / "index.yaml").read_text())
    assert index["schemaDrift"]
    assert any("measures" in w for w in index["schemaDrift"])
