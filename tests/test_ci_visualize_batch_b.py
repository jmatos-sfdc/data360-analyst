"""Batch B — scope-aware semantic lineage. Golden-fixture oracle + cross-fixture invariants.

These tests are the independent oracle for the Batch B refactor. The goldens under
tests/fixtures/ci-visualize/ are authored by hand from the CI SQL, BEFORE the resolver exists,
and are read-only for the implementer. If a golden looks wrong, the implementer stops and
reports it — never edits it. That is what defeats the "write the test to match the code" trap.

Two layers:
  1. Fixture-specific structural facts (scope tree, external-ID lineage, join ownership,
     regression anchors) — assert the specific things Batch B must get right on each real CI.
  2. Cross-fixture invariants — properties that must hold for EVERY element in EVERY fixture, so
     a resolver cannot special-case its way to green (the overfitting backstop).

Until Batch B lands, model["scopes"] etc. do not exist and every test here fails. That is the
intended red bar; do not weaken these to make them pass — implement the resolver instead.
"""

import json
from pathlib import Path

import pytest

from data360_analyst import ci_visualize

ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "ci-visualize"
# Synthetic CI SQL committed alongside the goldens. Self-contained: no dependency on any
# client corpus, and nothing client-specific ships in this repo.
QUERIES = FIXTURES / "sql"

GOLDENS = sorted(FIXTURES.glob("*.golden.json"))


def _load_golden(path):
    return json.loads(path.read_text())


def _build(ci_name):
    """Build the model for a corpus CI from its on-disk SQL."""
    sql_path = QUERIES / f"{ci_name}.sql"
    if not sql_path.exists():
        pytest.skip(f"corpus SQL not on disk: {sql_path}")
    sql = sql_path.read_text()
    model = ci_visualize.build_model(sql, ci_name)
    assert "error" not in model, f"{ci_name} failed to parse: {model.get('error')}"
    return model, sql


def _scope_by_alias(model):
    """Map boundAlias -> scope dict. Batch B must expose model['scopes'] with boundAlias."""
    scopes = model.get("scopes")
    assert scopes, "Batch B model must expose a non-empty 'scopes' list (not present — pre-Batch-B model)"
    out = {}
    for s in scopes:
        alias = s.get("boundAlias")
        assert alias is not None, f"every scope needs a boundAlias: {s}"
        out[alias] = s
    return out


def _scopeid_of(model, alias):
    return _scope_by_alias(model)[alias].get("scopeId")


def _span_bounds(span):
    """Normalize a span to (start, end). Accepts [s,e] or {start,end}."""
    if span is None:
        return None
    if isinstance(span, dict):
        return span.get("start"), span.get("end")
    return span[0], span[1]


def _scope_where_range(model, sql, scope_alias):
    """Return (start,end) char range of the WHERE clause owned by the given scope.

    Batch B must let us locate a scope's WHERE. The contract: each scope in model['scopes']
    exposes a 'whereSpan' (start/end into originalSql) when it has a WHERE, else None.
    """
    s = _scope_by_alias(model)[scope_alias]
    return _span_bounds(s.get("whereSpan"))


# ---------------------------------------------------------------------------
# Layer 1 — fixture-specific structural facts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("golden_path", GOLDENS, ids=[p.stem for p in GOLDENS])
def test_scope_tree_matches_golden(golden_path):
    g = _load_golden(golden_path)
    model, _ = _build(g["ci"])
    by_alias = _scope_by_alias(model)

    for exp_scope in g["scopes"]:
        alias = exp_scope["boundAlias"]
        assert alias in by_alias, f"{g['ci']}: expected a scope bound to alias '{alias}'"
        got = by_alias[alias]
        assert got.get("kind") == exp_scope["kind"], f"{g['ci']}: scope '{alias}' kind"

        # parent linkage by alias (resolve the parent scopeId back to its boundAlias)
        parent_alias = exp_scope["parentAlias"]
        if parent_alias is None:
            assert got.get("parentScopeId") is None, f"{g['ci']}: root scope must have null parent"
        else:
            parent_sid = got.get("parentScopeId")
            assert parent_sid is not None, f"{g['ci']}: scope '{alias}' must have a parent"
            parent_matches = [s for s in model["scopes"] if s.get("scopeId") == parent_sid]
            assert parent_matches, f"{g['ci']}: parentScopeId of '{alias}' does not resolve"
            assert parent_matches[0].get("boundAlias") == parent_alias, (
                f"{g['ci']}: scope '{alias}' parent should be '{parent_alias}', "
                f"got '{parent_matches[0].get('boundAlias')}'"
            )

        # base sources joined directly in this scope
        got_sources = set(got.get("baseSources", []))
        for src in exp_scope["baseSources"]:
            assert src in got_sources, f"{g['ci']}: scope '{alias}' should include base source '{src}'"


@pytest.mark.parametrize("golden_path", GOLDENS, ids=[p.stem for p in GOLDENS])
def test_external_id_lineage_matches_golden(golden_path):
    g = _load_golden(golden_path)
    ext_exp = g["externalId"]
    model, _ = _build(g["ci"])
    ext_got = (model.get("grain") or {}).get("externalId") or model.get("grain", {}).get("externalIdGrain")
    assert ext_got is not None, f"{g['ci']}: model must carry grain.externalId"

    assert ext_got.get("status") == ext_exp["status"], (
        f"{g['ci']}: externalId status expected '{ext_exp['status']}', got '{ext_got.get('status')}'"
    )
    if ext_exp["status"] != "resolved":
        return

    # every expected token must resolve to its base-DMO field through the alias chain
    got_tokens = {t.get("expr"): t for t in ext_got.get("tokens", [])}
    for tok in ext_exp["tokens"]:
        expr = tok["expr"]
        assert expr in got_tokens, f"{g['ci']}: externalId token '{expr}' missing"
        resolved = got_tokens[expr].get("resolvedTo") or got_tokens[expr].get("resolvesToBase")
        resolved = resolved if isinstance(resolved, list) else [resolved]
        assert tok["resolvesToBase"] in resolved, (
            f"{g['ci']}: token '{expr}' should resolve to '{tok['resolvesToBase']}', got {resolved}"
        )


@pytest.mark.parametrize("golden_path", GOLDENS, ids=[p.stem for p in GOLDENS])
def test_join_ownership_matches_golden(golden_path):
    g = _load_golden(golden_path)
    model, _ = _build(g["ci"])
    joins = model.get("joins", [])

    # index joins by (owning-scope-alias, right-relation) for lookup
    def right_of(j):
        return j.get("right") or j.get("target")

    def owning_alias(j):
        sid = j.get("scopeId")
        for s in model["scopes"]:
            if s.get("scopeId") == sid:
                return s.get("boundAlias")
        return None

    got = {(owning_alias(j), right_of(j)): j for j in joins}
    for ej in g.get("joins", []):
        key = (ej["owningScope"], ej["right"])
        assert key in got, f"{g['ci']}: expected join to '{ej['right']}' owned by scope '{ej['owningScope']}'"
        gj = got[key]
        assert gj.get("left") == ej["left"], (
            f"{g['ci']}: join to '{ej['right']}' left side expected '{ej['left']}', got '{gj.get('left')}'"
        )
        assert (gj.get("type") or "").upper().startswith(ej["type"]), (
            f"{g['ci']}: join to '{ej['right']}' type expected '{ej['type']}', got '{gj.get('type')}'"
        )


@pytest.mark.parametrize("golden_path", GOLDENS, ids=[p.stem for p in GOLDENS])
def test_regression_anchor_filter_span_within_owning_where(golden_path):
    """The projection-vs-WHERE bug and the anti-join drops: a filter's resolved span must fall
    inside its OWNING scope's WHERE clause range, never on a same-named column's projection."""
    g = _load_golden(golden_path)
    model, sql = _build(g["ci"])

    for anchor in g.get("regressionAnchors", []):
        needle = " ".join(anchor["filterPredicateContains"].split()).lower()
        match = None
        for f in model.get("filters", []):
            ftext = " ".join((f.get("text") or "").split()).lower()
            if needle in ftext:
                match = f
                break
        assert match is not None, f"{g['ci']}: no filter matching '{anchor['filterPredicateContains']}'"

        # owned by the expected scope
        owner_alias = None
        for s in model["scopes"]:
            if s.get("scopeId") == match.get("scopeId"):
                owner_alias = s.get("boundAlias")
        assert owner_alias == anchor["owningScope"], (
            f"{g['ci']}: filter '{anchor['filterPredicateContains']}' should be owned by "
            f"scope '{anchor['owningScope']}', got '{owner_alias}'"
        )

        # span must fall within that scope's WHERE range
        where_range = _scope_where_range(model, sql, anchor["owningScope"])
        assert where_range and where_range[0] is not None, (
            f"{g['ci']}: owning scope '{anchor['owningScope']}' must expose a whereSpan"
        )
        span = _span_bounds(match.get("sourceSpan"))
        assert span and span[0] is not None, f"{g['ci']}: filter must carry a sourceSpan"
        assert where_range[0] <= span[0] and span[1] <= where_range[1], (
            f"{g['ci']}: filter '{anchor['filterPredicateContains']}' span {span} falls outside its "
            f"owning WHERE range {where_range} — likely anchored on a projection occurrence (the bug)"
        )


@pytest.mark.parametrize("golden_path", GOLDENS, ids=[p.stem for p in GOLDENS])
def test_findings_flags_match_golden(golden_path):
    g = _load_golden(golden_path)
    model, _ = _build(g["ci"])
    rules = [f.get("rule") for f in model.get("findings", [])]
    fexp = g.get("findings", {})
    if fexp.get("simpleCaseExpected"):
        assert "simple-case" in rules, f"{g['ci']}: expected a simple-case finding"
    if fexp.get("hardcodedIdExpected"):
        assert "hardcoded-setup-id" in rules, f"{g['ci']}: expected a hardcoded-setup-id finding"


# ---------------------------------------------------------------------------
# Layer 2 — cross-fixture invariants (the overfitting backstop)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("golden_path", GOLDENS, ids=[p.stem for p in GOLDENS])
def test_invariant_every_scope_has_id_and_resolvable_parent(golden_path):
    g = _load_golden(golden_path)
    model, _ = _build(g["ci"])
    scopes = model.get("scopes", [])
    ids = {s.get("scopeId") for s in scopes}
    assert None not in ids, f"{g['ci']}: every scope must have a scopeId"
    assert len(ids) == len(scopes), f"{g['ci']}: scopeIds must be unique"

    roots = [s for s in scopes if s.get("parentScopeId") is None]
    assert len(roots) == 1, f"{g['ci']}: exactly one root scope, got {len(roots)}"
    for s in scopes:
        pid = s.get("parentScopeId")
        if pid is not None:
            assert pid in ids, f"{g['ci']}: scope '{s.get('boundAlias')}' parent {pid} does not resolve"


@pytest.mark.parametrize("golden_path", GOLDENS, ids=[p.stem for p in GOLDENS])
def test_invariant_every_filter_owned_and_span_within_its_where(golden_path):
    """For EVERY filter (not just the named regression anchors): it has an owning scopeId, and its
    span lies within that scope's WHERE range. This is the general form of the projection-vs-WHERE
    fix — a resolver cannot pass it by special-casing one predicate."""
    g = _load_golden(golden_path)
    model, sql = _build(g["ci"])
    scope_where = {}
    for s in model.get("scopes", []):
        scope_where[s.get("scopeId")] = _span_bounds(s.get("whereSpan"))

    for f in model.get("filters", []):
        sid = f.get("scopeId")
        assert sid in scope_where, f"{g['ci']}: filter '{f.get('text')}' has no resolvable owning scopeId"
        wr = scope_where[sid]
        assert wr and wr[0] is not None, (
            f"{g['ci']}: filter '{f.get('text')}' owned by a scope with no whereSpan"
        )
        span = _span_bounds(f.get("sourceSpan"))
        assert span and span[0] is not None, f"{g['ci']}: filter '{f.get('text')}' missing sourceSpan"
        assert wr[0] <= span[0] and span[1] <= wr[1], (
            f"{g['ci']}: filter '{f.get('text')}' span {span} outside owning WHERE {wr}"
        )


@pytest.mark.parametrize("golden_path", GOLDENS, ids=[p.stem for p in GOLDENS])
def test_invariant_every_join_has_owning_scope_and_right_identity(golden_path):
    g = _load_golden(golden_path)
    model, _ = _build(g["ci"])
    ids = {s.get("scopeId") for s in model.get("scopes", [])}
    for j in model.get("joins", []):
        assert j.get("scopeId") in ids, f"{g['ci']}: join to '{j.get('right') or j.get('target')}' has no owning scope"
        assert (j.get("right") or j.get("target")), f"{g['ci']}: join missing a right relation identity"


@pytest.mark.parametrize("golden_path", GOLDENS, ids=[p.stem for p in GOLDENS])
def test_invariant_reused_leg_aliases_scope_to_a_leg_not_root(golden_path):
    """Multi-leg alias-reuse trap: co/sap are declared once per FULL-JOIN leg. Each such scope's
    parent must be a leg scope (a derived child of root), never root itself and never collapsed
    into a single shared scope."""
    g = _load_golden(golden_path)
    if "FULL JOIN" not in g.get("shape", ""):
        pytest.skip("not a multi-leg fixture")
    model, _ = _build(g["ci"])
    scopes = model.get("scopes", [])
    by_id = {s.get("scopeId"): s for s in scopes}
    root_id = next(s["scopeId"] for s in scopes if s.get("parentScopeId") is None)
    leg_ids = {s["scopeId"] for s in scopes if s.get("parentScopeId") == root_id}

    reused = [s for s in scopes if s.get("boundAlias") in ("co", "sap")]
    assert reused, f"{g['ci']}: expected co/sap scopes in a multi-leg CI"
    for s in reused:
        pid = s.get("parentScopeId")
        assert pid in leg_ids, (
            f"{g['ci']}: scope '{s.get('boundAlias')}' ({s.get('scopeId')}) parent should be a leg "
            f"scope, got {pid} (root={root_id})"
        )
