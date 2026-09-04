#!/usr/bin/env python3
"""
Data 360 CI Visualize — phase 1: parser -> semantic model + onboarding report.

Turns a Calculated Insight's SQL into a self-contained interactive onboarding
report: read the SQL, click any fragment, get a plain-language explanation of that
part, with a scope breadcrumb to widen from a line to its enclosing block. Offline —
parses local .sql, no org connection.

This is the phase-1 build per docs/plans/data360-ci-visualize-design.md:
  1. parse (sqlglot, spark dialect) -> semantic model (JSON)
  2. render the onboarding HTML off that model

Design discipline honored here:
  - Every element that references SQL carries a char-offset sourceSpan (not a string) so
    cross-highlighting is unambiguous across repeated aliases. (design #7)
  - Every INFERRED property (grain, cardinality, filter purpose) carries basis/confidence —
    never presented as established fact. (design #10 + funnel-mock review #1/#2/#3)
  - Three distinct grains kept separate: SQL grain / external-ID grain / claimed business grain.
  - Labels derived from the AST, not hardcoded.

Usage:
  python3 ci_visualize.py --input <file.sql> [--name <CI api name>] [--out <report.html>]
  python3 ci_visualize.py --client-root <Data360/> --name <CI_api_name>
  python3 ci_visualize.py --input <file.sql> --model-only   # emit JSON model, no HTML

Static/offline only. The live funnel (needs --env + count queries) is a later phase.
"""

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.tokens import TokenType
except ImportError:
    print("ERROR: sqlglot not installed. Use the bundled .venv or `pip install sqlglot`.")
    sys.exit(1)

DIALECT = "spark"  # matches ci_audit.py — Data Cloud SQL is ANSI + Spark-leaning


# ---------------------------------------------------------------------------
# Provenance wrapper for inferred facts (design #10 / funnel review #1-#3)
# ---------------------------------------------------------------------------
def inferred(value, basis, confidence, evidence=None):
    """Wrap a value that is not established fact. basis: heuristic|metadata|live-count|
    observed|syntactic|user. Nothing in the report presents an inferred value bare."""
    return {"value": value, "basis": basis, "confidence": confidence, "evidence": evidence or []}


# ---------------------------------------------------------------------------
# Span resolution: map an AST node back to char offsets in the ORIGINAL sql.
# sqlglot does not preserve reliable source positions across the whole tree, so we
# resolve by locating the node's rendered SQL as a substring, tracking a cursor so
# repeated fragments (aliases q/sel/cfg) resolve to successive occurrences. (design #7)
# ---------------------------------------------------------------------------
class SpanResolver:
    def __init__(self, sql):
        self.sql = sql
        self._cursor = 0

    def find(self, snippet, from_pos=None):
        """Return [start,end) of snippet at/after from_pos (default: running cursor).
        Falls back to a whitespace-normalized search when exact match fails."""
        if not snippet:
            return None
        start_at = self._cursor if from_pos is None else from_pos
        i = self.sql.find(snippet, start_at)
        span = [i, i + len(snippet)] if i >= 0 else None
        if span is None and from_pos is None:
            i = self.sql.find(snippet)  # retry from top (out-of-order node)
            span = [i, i + len(snippet)] if i >= 0 else None
        if span is None:
            span = self._fuzzy(snippet, start_at)
        if span is None:
            return None
        if from_pos is None:
            self._cursor = span[1]
        return span

    def _fuzzy(self, snippet, start_at):
        """Whitespace-insensitive fallback: build a regex that treats runs of whitespace
        in the snippet as \\s+, so reflowed SQL still resolves."""
        parts = [re.escape(tok) for tok in snippet.split()]
        if not parts:
            return None
        pat = re.compile(r"\s+".join(parts))
        m = pat.search(self.sql, start_at) or pat.search(self.sql)
        return [m.start(), m.end()] if m else None


def span_of(node, resolver, dialect=DIALECT):
    """Best-effort char span for an AST node by rendering it and locating it."""
    try:
        rendered = node.sql(dialect=dialect)
    except Exception:
        return None
    return resolver.find(rendered)


def find_in_window(sql, snippet, lo, hi):
    """Locate `snippet` (rendered SQL) within the char window [lo, hi) of `sql`, tolerant
    of whitespace reflow and case (sqlglot uppercases keywords/`TRUE`). Returns [start,end]
    inside the window, or None. This is what makes filter spans land inside the OWNING
    scope's WHERE range instead of on a same-named column's projection occurrence upstream
    (the projection-vs-WHERE bug, design B#7)."""
    if not snippet:
        return None
    window = sql[lo:hi]
    # exact first (cheap), then whitespace/case-tolerant regex
    i = window.find(snippet)
    if i >= 0:
        return [lo + i, lo + i + len(snippet)]
    parts = [re.escape(tok) for tok in snippet.split()]
    if not parts:
        return None
    pat = re.compile(r"\s+".join(parts), re.IGNORECASE)
    m = pat.search(window)
    return [lo + m.start(), lo + m.end()] if m else None


# ---------------------------------------------------------------------------
# Scope tree — the load-bearing Batch B piece (design B#1/#2/#6/#7).
#
# sqlglot does not hand us char offsets for clauses, and repeated derived aliases
# (co/sap once per FULL-JOIN leg, cfg/camp/q/sel) make string matching ambiguous. So we
# scan the RAW token stream once to carve each SELECT scope's char region + clause offsets
# (WHERE/GROUP BY/HAVING), then zip those regions — in source order — onto the AST's
# pre-order (DFS) list of Select nodes. The two orders coincide (a subquery's opening paren
# precedes its children in both), verified across the corpus. Each scope carries a stable
# id, its binding alias, its parent, the base sources joined directly in it, a projection
# symbol table (output col -> defining expression), and its WHERE char span.
# ---------------------------------------------------------------------------
def _relation_id(node):
    """Identity of a FROM/JOIN relation: the derived-table alias if present, else the
    base DMO/CI table name."""
    if isinstance(node, exp.Subquery):
        return node.alias or None
    if isinstance(node, exp.Table):
        return node.alias or node.name
    return getattr(node, "alias", None) or None


def _scope_base_sources(select_node):
    """Base sources joined DIRECTLY in this scope (FROM + its own JOINs) — not descendants.
    A subquery source contributes its alias; a table its name."""
    out = []
    frm = select_node.args.get("from") or select_node.args.get("from_")
    if frm is not None and frm.this is not None:
        rid = _relation_id(frm.this)
        if rid:
            out.append(rid)
    for j in select_node.args.get("joins", []) or []:
        rid = _relation_id(j.this)
        if rid:
            out.append(rid)
    return out


def _scope_projection(select_node):
    """Map each output column name to its defining expression node in this scope."""
    proj = {}
    for p in select_node.expressions:
        name = p.alias_or_name
        if not name:
            continue
        proj[name] = p.this if isinstance(p, exp.Alias) else p
    return proj


def build_scopes(sql, root_ast):
    """Return a list of scope dicts, in DFS/source order. Each:
      scopeId, parentScopeId, boundAlias, kind, baseSources, whereSpan, region [start,end],
      _select (AST node), _projection (name->expr node).
    """
    toks = sqlglot.tokenize(sql, read=DIALECT)
    n = len(toks)
    # token-stream regions in source order (index 0 == root)
    regions = [dict(alias="__root__", start=0, end=len(sql),
                    where=None, group=None, having=None, order=None, parent=None)]
    open_stack = []       # (is_subquery, region_index_or_None)
    scope_stack = [0]     # indices of currently-enclosing SELECT scopes
    for i, t in enumerate(toks):
        tt = t.token_type
        if tt == TokenType.L_PAREN:
            is_subq = (i + 1 < n and toks[i + 1].token_type == TokenType.SELECT)
            if is_subq:
                idx = len(regions)
                regions.append(dict(alias=None, start=t.start, end=None,
                                    where=None, group=None, having=None, order=None,
                                    parent=scope_stack[-1]))
                open_stack.append((True, idx))
                scope_stack.append(idx)
            else:
                open_stack.append((False, None))
        elif tt == TokenType.R_PAREN:
            if not open_stack:
                continue
            is_subq, idx = open_stack.pop()
            if is_subq:
                regions[idx]["end"] = t.end
                regions[idx]["alias"] = toks[i + 1].text if i + 1 < n else None
                scope_stack.pop()
        elif tt == TokenType.WHERE:
            r = regions[scope_stack[-1]]
            if r["where"] is None:
                r["where"] = t.start
        elif tt == TokenType.GROUP_BY:
            r = regions[scope_stack[-1]]
            if r["group"] is None:
                r["group"] = t.start
        elif tt == TokenType.HAVING:
            r = regions[scope_stack[-1]]
            if r["having"] is None:
                r["having"] = t.start
        elif tt == TokenType.ORDER_BY:
            r = regions[scope_stack[-1]]
            if r["order"] is None:
                r["order"] = t.start

    selects = list(root_ast.find_all(exp.Select, bfs=False))
    scopes = []
    # Defensive: if the two orderings ever diverge in count, fall back to AST-only scopes
    # (no whereSpan) rather than mis-pairing regions.
    paired = len(selects) == len(regions)
    for i, sel in enumerate(selects):
        reg = regions[i] if paired else None
        parent = sel.parent
        alias = parent.alias if isinstance(parent, exp.Subquery) else "__root__"
        where_span = None
        region = None
        if reg is not None:
            region = [reg["start"], reg["end"] if reg["end"] is not None else len(sql)]
            if reg["where"] is not None:
                ends = [v for v in (reg["group"], reg["having"], reg["order"], region[1])
                        if v is not None and v > reg["where"]]
                where_span = [reg["where"], min(ends) if ends else region[1]]
        scopes.append({
            "scopeId": f"s{i}",
            "parentScopeId": None,
            "boundAlias": alias,
            "kind": "root" if alias == "__root__" else "derived",
            "baseSources": _scope_base_sources(sel),
            "whereSpan": where_span,
            "region": region,
            "_select": sel,
            "_projection": _scope_projection(sel),
        })
    # parent linkage: map each select node to its scopeId, then resolve parent select
    sid_by_node = {id(s["_select"]): s["scopeId"] for s in scopes}
    for s in scopes:
        if s["boundAlias"] == "__root__":
            continue
        anc = s["_select"].parent
        while anc is not None and not isinstance(anc, exp.Select):
            anc = anc.parent
        s["parentScopeId"] = sid_by_node.get(id(anc)) if anc is not None else None
    return scopes


def _child_scope_index(scopes):
    """(parentScopeId, boundAlias) -> scope. Resolves alias reuse correctly: co under wd is
    a different scope than co under d, keyed by parent."""
    idx = {}
    for s in scopes:
        idx[(s["parentScopeId"], s["boundAlias"])] = s
    return idx


# ---------------------------------------------------------------------------
# CASE-form classification (relevant to the current parser regression + explanations)
# ---------------------------------------------------------------------------
def case_form(case_node):
    """Return 'simple' (CASE <operand> WHEN ...) or 'searched' (CASE WHEN <cond> ...).
    sqlglot models simple CASE by populating the Case node's `this` (the operand)."""
    return "simple" if case_node.args.get("this") is not None else "searched"


# ---------------------------------------------------------------------------
# Grain extraction — three distinct grains (funnel review #1)
# ---------------------------------------------------------------------------
def extract_grains(select_node, resolver):
    """Return the SQL grain (GROUP BY exprs), external-ID grain (tokens of a
    RecordAlertExternalId-like CONCAT), and a claimed business grain (inferred)."""
    sql_grain = []
    group = select_node.args.get("group")
    if group:
        for e in group.expressions:
            # skip constant GROUP BY terms (they are per-execution constants, not grain)
            if isinstance(e, (exp.Literal,)):
                continue
            sql_grain.append(e.sql(dialect=DIALECT))

    # external-ID grain: find an output aliased like *ExternalId* built from CONCAT
    ext_tokens = []
    ext_col = None
    ext_expr = None
    ext_status = "not-found"
    for proj in select_node.expressions:
        alias = proj.alias_or_name or ""
        if "externalid" in alias.lower() and isinstance(proj, exp.Alias):
            ext_col = alias
            concat = proj.this
            ext_expr = concat.sql(dialect=DIALECT)
            if isinstance(concat, exp.Column):
                ext_status = "unresolved-pass-through"
            else:
                ext_tokens = _concat_tokens(concat)
                ext_status = "resolved" if ext_tokens else "unresolved-expression"
            break

    business = inferred(
        value=None,
        basis="heuristic",
        confidence="low",
        evidence=["derived from GROUP BY + ExternalId tokens; confirm against design intent"],
    )
    return {
        "sqlGrain": sql_grain,
        "externalIdGrain": {
            "column": ext_col,
            "status": ext_status,
            "expression": ext_expr,
            "tokens": ext_tokens,
        },
        "businessGrain": business,
    }


def _concat_column_nodes(node):
    """Flatten nested CONCAT(...) into ordered Column (or expression) nodes, dropping the
    '~' separator literals. Unlike _concat_tokens (which returns strings for the Batch A
    grain), this keeps AST nodes so Batch B can resolve each token's lineage."""
    out = []

    def walk(n):
        if isinstance(n, exp.Concat):
            for a in n.expressions:
                walk(a)
        elif isinstance(n, exp.Anonymous) and n.name.upper() == "CONCAT":
            for a in n.expressions:
                walk(a)
        elif isinstance(n, exp.Literal):
            if (n.this or "").strip() != "~":
                out.append(n)
        else:
            out.append(n)

    if node is not None:
        walk(node)
    return out


def _resolve_output_column(scope, name, child_index, depth=0):
    """Resolve output column `name` in `scope` down to a base-DMO field, following child
    derived-table aliases through the projection symbol tables (design B#2/#3/#5). Returns
    (base_field | None, via_aliases, derivedVia). `via_aliases` lists the child aliases
    descended INTO from this scope down. A CASE anywhere on the path marks derivedVia."""
    if depth > 25:
        return None, [], None
    expr = (scope.get("_projection") or {}).get(name)
    if expr is None:
        return None, [], None
    return _resolve_expr(scope, expr, child_index, depth, None)


def _resolve_expr(scope, expr, child_index, depth, derived):
    if depth > 25:
        return None, [], derived
    if isinstance(expr, exp.Column):
        tbl = expr.table
        child = child_index.get((scope["scopeId"], tbl))
        if child is not None:
            base, via, dv = _resolve_output_column(child, expr.name, child_index, depth + 1)
            return base, [tbl] + via, (dv or derived)
        if tbl:
            return f"{tbl}.{expr.name}", [], derived
        return None, [], derived
    if isinstance(expr, exp.Case):
        target = expr.args.get("this") or next(iter(expr.find_all(exp.Column)), None)
        if target is not None:
            return _resolve_expr(scope, target, child_index, depth, "CASE")
        return None, [], "CASE"
    # aggregation / function / arithmetic wrapper: resolve the first inner column
    col = next(iter(expr.find_all(exp.Column)), None)
    if col is not None:
        return _resolve_expr(scope, col, child_index, depth, derived)
    return None, [], derived


def extract_external_id(scopes):
    """Resolve the external-ID grain's token lineage (design B#5). Starting from the
    outermost scope's *ExternalId* output column, follow pass-through Columns down through
    child scopes until the CONCAT is reached, then resolve every CONCAT token to a base-DMO
    field. Status is `resolved` only when EVERY token terminates at a base field."""
    root = next((s for s in scopes if s["parentScopeId"] is None), scopes[0] if scopes else None)
    if root is None:
        return {"column": None, "status": "not-found", "tokens": []}
    child_index = _child_scope_index(scopes)

    ext_name = None
    for name in root.get("_projection", {}):
        if "externalid" in name.lower():
            ext_name = name
            break
    if ext_name is None:
        return {"column": None, "status": "not-found", "tokens": []}

    # follow pass-through columns down to the scope that actually builds the CONCAT
    scope = root
    name = ext_name
    prefix = []
    expr = scope["_projection"].get(name)
    while isinstance(expr, exp.Column):
        tbl = expr.table
        child = child_index.get((scope["scopeId"], tbl))
        if child is None:
            break
        prefix.append(tbl)
        scope = child
        name = expr.name
        expr = scope["_projection"].get(name)

    token_nodes = _concat_column_nodes(expr) if expr is not None else []
    concat_like = isinstance(expr, exp.Concat) or (
        isinstance(expr, exp.Anonymous) and expr.name.upper() == "CONCAT"
    )
    if not concat_like or not token_nodes:
        return {
            "column": ext_name,
            "status": "unresolved-pass-through",
            "expression": expr.sql(dialect=DIALECT) if expr is not None else None,
            "tokens": [],
        }

    tokens = []
    all_base = True
    for node in token_nodes:
        base, via, dv = _resolve_expr(scope, node, child_index, 0, None)
        if base is None:
            all_base = False
        tok = {
            "expr": node.sql(dialect=DIALECT),
            "resolvedTo": [base] if base else [],
            "via": prefix + via,
        }
        if dv:
            tok["derivedVia"] = dv
        tokens.append(tok)

    return {
        "column": ext_name,
        "status": "resolved" if all_base else "unresolved-pass-through",
        "expression": expr.sql(dialect=DIALECT),
        "tokens": tokens,
    }


def _concat_tokens(node):
    """Flatten nested CONCAT(...) into an ordered list of token SQL strings, dropping
    the '~' separators for readability."""
    tokens = []

    def walk(n):
        if isinstance(n, exp.Concat):
            for a in n.expressions:
                walk(a)
        elif isinstance(n, (exp.Anonymous,)) and n.name.upper() == "CONCAT":
            for a in n.expressions:
                walk(a)
        else:
            s = n.sql(dialect=DIALECT)
            if s.strip("'") != "~":
                tokens.append(s)

    if node is not None:
        walk(node)
    return tokens


# ---------------------------------------------------------------------------
# Join extraction with provenance-tagged cardinality (funnel review #2/#3)
# ---------------------------------------------------------------------------
def _join_cardinality(join_node):
    """Infer cardinality WITHOUT asserting it as fact. A constant-literal join key or a
    join to a GROUP BY-less subquery is flagged as cross-product / N:K risk, not N:1."""
    on = join_node.args.get("on")
    kind = (join_node.args.get("side") or "") + " " + (join_node.args.get("kind") or "")
    kind = kind.strip() or "INNER"

    # Cross-product risk ONLY when there is NO real column=column equality binding the two
    # sides — i.e. the join is held together purely by constant-literal equalities
    # (e.g. camp.join_key = 'VELOCITY'). A literal riding alongside a genuine col=col key
    # (KQ_Id = 'CRM' next to a.id = b.id) is a filter predicate, not the join key — not risk.
    if on is not None:
        eqs = list(on.find_all(exp.EQ))
        has_col_col = any(isinstance(eq.left, exp.Column) and isinstance(eq.right, exp.Column) for eq in eqs)
        has_literal_eq = any(isinstance(eq.left, exp.Literal) or isinstance(eq.right, exp.Literal) for eq in eqs)
        if has_literal_eq and not has_col_col:
            return inferred(
                "N:K (cross-product risk)", basis="syntactic", confidence="medium",
                evidence=["join bound only by a constant literal; right side may emit multiple rows"],
            )
    return inferred("unknown", basis="heuristic", confidence="low",
                    evidence=["cardinality not determinable from SQL alone; needs metadata or observed counts"])


_JOIN_ANCHOR = re.compile(r"\b((?:INNER|LEFT|RIGHT|FULL|CROSS)(?:\s+OUTER)?\s+)?JOIN\b", re.I)
_CLAUSE_END = re.compile(r"\b(INNER\s+JOIN|LEFT\s+(?:OUTER\s+)?JOIN|RIGHT\s+(?:OUTER\s+)?JOIN|"
                         r"FULL\s+(?:OUTER\s+)?JOIN|CROSS\s+JOIN|JOIN|WHERE|GROUP\s+BY|HAVING|ORDER\s+BY)\b", re.I)


def _join_span_raw(sql, target_name, alias, used_starts):
    """Anchor a join span in the RAW sql, scanning forward and skipping JOIN offsets already
    claimed (so a table joined in multiple legs resolves each occurrence to a distinct span).
    A join matches if either its table name OR its alias appears in the window after JOIN.
    Returns [start, end] or None."""
    pos = 0
    while True:
        m = _JOIN_ANCHOR.search(sql, pos)
        if not m:
            return None
        if m.start() in used_starts:
            pos = m.end()
            continue
        window = sql[m.end():m.end() + 300]
        name_hit = target_name and target_name in window[:len(target_name) + 40]
        alias_hit = alias and re.search(r"\b" + re.escape(alias) + r"\b", window[:120])
        subquery = (not target_name) and window.lstrip().startswith("(")
        if name_hit or alias_hit or subquery:
            used_starts.add(m.start())
            nxt = _CLAUSE_END.search(sql, m.end())
            return [m.start(), nxt.start() if nxt else len(sql)]
        pos = m.end()


def _join_left_right(j, target_id):
    """Explicit relation identities for a join (design B#6). `right` is always the join's
    target relation. `left` is the OTHER relation named in the first column=column ON
    equality — i.e. what the target binds back to. A literal-key join whose ON references
    no outer relation (e.g. cfg/camp `ON x.key = 'VELOCITY'`) has left = None."""
    on = j.args.get("on")
    if on is None:
        return None
    for eq in on.find_all(exp.EQ):
        if isinstance(eq.left, exp.Column) and isinstance(eq.right, exp.Column):
            l_rel = eq.left.table or None
            r_rel = eq.right.table or None
            # left = the relation that isn't the target
            if l_rel and l_rel != target_id:
                return l_rel
            if r_rel and r_rel != target_id:
                return r_rel
            return l_rel or r_rel
    return None


def extract_joins(scopes, sql):
    """Walk each scope's own JOIN list (not descendants) so every join is assigned to its
    OWNING scope with explicit left/right relation identities. Replaces the flat,
    scope-blind cross-tree scan and the textual clause-end span termination. (design B#6/#7)"""
    joins = []
    for scope in scopes:
        sel = scope["_select"]
        region = scope.get("region")
        cursor = region[0] if region else 0
        for j in sel.args.get("joins", []) or []:
            target = j.this
            target_id = _relation_id(target)
            display = target_id or "(subquery)"
            kind = (((j.args.get("side") or "") + " " + (j.args.get("kind") or "")).strip()) or "INNER"
            on = j.args.get("on")
            keys = []
            if on is not None:
                for eq in on.find_all(exp.EQ):
                    keys.append(eq.sql(dialect=DIALECT))
            left = _join_left_right(j, target_id)
            # span: anchor the JOIN keyword within this scope's char region, scanning
            # forward so repeated per-leg joins each resolve to their own occurrence.
            span = None
            if region:
                span, cursor = _join_span_in_region(sql, region, cursor, target)
            joins.append({
                "scopeId": scope["scopeId"],
                "left": left,
                "right": display,
                "target": display,   # kept for back-compat with the renderer / Batch A
                "type": kind,
                "keys": keys,
                "cardinality": _join_cardinality(j),
                "sourceSpan": span,
            })
    return joins


def _join_span_in_region(sql, region, cursor, target):
    """Find the next JOIN clause span at/after `cursor`, bounded by the scope region end.
    Returns ([start,end], new_cursor). Terminates the span at the next clause boundary that
    is still inside this region (so a nested subquery-join's span doesn't bleed past it)."""
    lo, hi = max(cursor, region[0]), region[1]
    m = _JOIN_ANCHOR.search(sql, lo, hi)
    if not m:
        return None, cursor
    nxt = _CLAUSE_END.search(sql, m.end(), hi)
    end = nxt.start() if nxt else hi
    return [m.start(), end], m.end()


# ---------------------------------------------------------------------------
# Filter extraction with inferred purpose (design #10)
# ---------------------------------------------------------------------------
_PURPOSE_HINTS = [
    ("identity", ["recordtypeid", "kq_id", "accounttype", "developername"]),
    ("time", ["current_date", "notification_date", "txmonth", "curr_qtr", "date"]),
    ("config", ["config_key", "is_active", "alert_type", "sales_play"]),
    ("eligibility", ["programreference", "status", "enrollment", "priority_account"]),
    ("threshold", [">", "<", ">=", "<=", "threshold", "tier"]),
    ("dq", ["not like", "carvana", "carmax", "retired", "closed", "is null", "is not null"]),
]


def _infer_purpose(sql_text):
    low = sql_text.lower()
    for purpose, hints in _PURPOSE_HINTS:
        if any(h in low for h in hints):
            return purpose
    return "other"


def _infer_purpose_ast(node, sql_text):
    """Classify a predicate's purpose from AST structure + referenced fields, not a
    substring first-match (design B#9). The oracle does not gate hard on the value, so this
    stays a lightweight improvement: structural signals first, then the field-name hints.
    Returned wrapped in the inferred() provenance envelope."""
    cols = [c.name.lower() for c in node.find_all(exp.Column)]
    colset = " ".join(cols)
    value = None
    # anti-join / null-guard: IS (NOT) NULL on a single column
    if isinstance(node, exp.Is) or node.find(exp.Is) is not None:
        value = "dq"
    # hardcoded setup-id equality -> identity
    elif _has_hardcoded_id(node):
        value = "identity"
    # comparison operators (>, <, >=, <=) with no equality -> threshold
    elif node.find(exp.GT, exp.LT, exp.GTE, exp.LTE) is not None and node.find(exp.EQ) is None:
        value = "threshold"
    # date-valued reference -> time
    elif node.find(exp.CurrentDate) is not None or any(
        k in colset for k in ("date", "_dt", "notification", "curr_qtr", "txmonth", "fiscal")
    ):
        value = "time"
    if value is None:
        value = _infer_purpose(sql_text)
    return inferred(value, basis="ast+heuristic", confidence="low")


def extract_filters(scopes, sql):
    """Collect WHERE predicates from EVERY scope, assigning each to its OWNING scope and
    resolving its span WITHIN that scope's WHERE char range. Anchoring inside the owning
    WHERE — not on the predicate's first column wherever it renders — is the core Batch B
    fix: a column projected AND filtered (e.g. incntv_curr_tier__c) no longer mis-resolves
    the filter onto its SELECT-projection occurrence. (design B#7)"""
    filters = []
    for scope in scopes:
        sel = scope["_select"]
        where = sel.args.get("where")
        if where is None:
            continue
        where_span = scope.get("whereSpan")
        lo, hi = (where_span[0], where_span[1]) if where_span else (None, None)
        cursor = lo
        conds = list(where.this.flatten()) if isinstance(where.this, exp.And) else [where.this]
        for c in conds:
            s = c.sql(dialect=DIALECT)
            span = None
            if lo is not None:
                # search forward from the running cursor so repeated predicates in the same
                # WHERE each resolve to their own occurrence; always bounded by [lo,hi).
                span = find_in_window(sql, s, cursor if cursor is not None else lo, hi)
                if span is None:
                    span = find_in_window(sql, s, lo, hi)
                if span is None:
                    # sqlglot normalizes some tokens (CURRENT_DATE() -> CURRENT_DATE,
                    # true -> TRUE, IFNULL -> COALESCE), so the full rendered predicate may
                    # not appear verbatim. Fall back to the predicate's first column name,
                    # which is stable and still lands inside the owning WHERE. (design B#7)
                    first_col = next(iter(c.find_all(exp.Column)), None)
                    if first_col is not None:
                        anchor = cursor if cursor is not None else lo
                        span = find_in_window(sql, first_col.sql(dialect=DIALECT), anchor, hi) \
                            or find_in_window(sql, first_col.sql(dialect=DIALECT), lo, hi)
                if span is not None:
                    cursor = span[1]
            filters.append({
                "scopeId": scope["scopeId"],
                "scope": _scope_depth_index(scopes, scope),  # back-compat integer
                "sourceSpan": span,
                "purpose": _infer_purpose_ast(c, s),
                "portable": not _has_hardcoded_id(c),
                "text": s,
            })
    return filters


def _scope_depth_index(scopes, scope):
    """Back-compat integer scope field: position in the DFS scope list (0 = outermost)."""
    for i, s in enumerate(scopes):
        if s is scope:
            return i
    return 0


_ID_RE = re.compile(r"'(0[0-9A-Za-z]{14,17})'")


def _has_hardcoded_id(node):
    return bool(_ID_RE.search(node.sql(dialect=DIALECT)))


# ---------------------------------------------------------------------------
# Field (output column) extraction
# ---------------------------------------------------------------------------
def extract_fields(select_node, resolver):
    fields = []
    for proj in select_node.expressions:
        alias = proj.alias_or_name
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        agg = None
        for fn in (exp.Sum, exp.Count, exp.Min, exp.Max, exp.Avg):
            if isinstance(inner, fn):
                agg = fn.__name__.upper()
                break
        if agg is None and isinstance(inner, exp.Func) and inner.sql_name().upper() == "FIRST":
            agg = "FIRST"
        transform = None
        if isinstance(inner, exp.Case):
            transform = f"CASE ({case_form(inner)})"
        elif isinstance(inner, (exp.Concat,)) or (isinstance(inner, exp.Anonymous) and inner.name.upper() == "CONCAT"):
            transform = "CONCAT"
        elif isinstance(inner, exp.Literal):
            transform = "literal"
        # Span: anchor on the raw-SQL projection ending in `AS <alias>`, not the rendered
        # expression — sqlglot normalizes IFNULL->COALESCE etc., so render-match misses. The
        # alias appears verbatim as `AS alias`; extend back to the projection start.
        span = _field_span_raw(resolver, alias) or span_of(proj, resolver)
        fields.append({
            "output": alias,
            "sourceExpr": inner.sql(dialect=DIALECT) if inner else None,
            "transform": transform,
            "aggregation": agg,
            "sourceSpan": span,
        })
    return fields


_AS_ALIAS = None  # compiled per-call below


def _field_span_raw(resolver, alias):
    """Locate `<expr> AS <alias>` in raw SQL and return [start_of_expr, end_of_alias].
    Anchors on the alias (verbatim, reflow-proof), extends back to the projection start
    (previous comma at the top level, or SELECT)."""
    if not alias:
        return None
    sql = resolver.sql
    m = re.search(r"\bAS\s+" + re.escape(alias) + r"\b", sql[resolver._cursor:])
    if not m:
        m = re.search(r"\bAS\s+" + re.escape(alias) + r"\b", sql)
        if not m:
            return None
        base = 0
    else:
        base = resolver._cursor
    as_start = base + m.start()
    end = base + m.end()
    # walk back to the projection start: the previous top-level comma or the SELECT keyword,
    # respecting paren depth so commas inside IFNULL(...) don't count.
    depth = 0
    i = as_start - 1
    start = None
    while i >= 0:
        ch = sql[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
        elif ch == "," and depth == 0:
            start = i + 1
            break
        i -= 1
    if start is None:
        sel = sql.rfind("SELECT", 0, as_start)
        start = sel + 6 if sel >= 0 else as_start
    # trim leading whitespace
    while start < as_start and sql[start] in " \t\n\r":
        start += 1
    resolver._cursor = end
    return [start, end]


def extract_inner_fields(scopes, sql):
    """Projections defined in the NON-root (derived) scopes — the `SELECT ... AS x` columns
    inside subqueries. The root scope's projections are already surfaced by extract_fields;
    this covers the inner scopes so those columns become clickable too.

    Each inner projection's span is resolved WITHIN its owning scope's char region (Batch B
    gives us that region), so a name that repeats across scopes (CommonOwnerCRMAccountId__c
    appears in root, sel, and the base scope) anchors on the occurrence in its own scope, not
    the first one in the file — the same discipline the filter/whereSpan fix uses. Confidence
    is 'low' by design: these are mechanical projection facts, not reviewed business meaning.

    Limitation (shared with the root extractor _field_span_raw): only explicit `AS <name>`
    aliases anchor. Implicit space-separated aliases (`col.x  MyAlias`, `END) rownum`) get no
    span and are skipped — consistent with root-field behavior, not a regression."""
    out = []
    seen_spans = set()  # dedup guard: never emit two inner fields on the same char span
    for scope in scopes:
        alias = scope.get("boundAlias")
        # Skip the root (its projections are extract_fields' job) and anonymous/scalar-subquery
        # scopes (empty alias) — the latter's `AS x` tokens collide with the enclosing column's
        # alias and would double-anchor. Data 360 CIs can't nest a SELECT in SELECT anyway
        # ([[feedback-ci-no-subquery-in-select]]), so this only guards adversarial offline input.
        if not alias or alias == "__root__":
            continue
        region = scope.get("region")
        if not region:
            continue
        lo, hi = region[0], region[1]
        proj = scope.get("_projection") or {}
        cursor = lo
        for name, expr in proj.items():
            if not name:
                continue
            # Word-boundary `AS <name>` search, matching the root extractor's discipline
            # (_field_span_raw): a bare substring would match `AS Foo` inside `AS Foobar` or
            # inside a string literal 'AS Foo'. Search forward from the cursor first (so repeats
            # in this scope resolve in order), then fall back to the whole region.
            span = _find_as_alias(sql, name, cursor, hi) or _find_as_alias(sql, name, lo, hi)
            if span is None or tuple(span) in seen_spans:
                continue
            seen_spans.add(tuple(span))
            cursor = span[1]
            try:
                src = expr.sql(dialect=DIALECT) if expr is not None else None
            except Exception:
                src = None
            out.append({
                "scopeId": scope.get("scopeId"),
                "output": name,
                "boundAlias": alias,
                "sourceExpr": src,
                "sourceSpan": span,
            })
    return out


def _find_as_alias(sql, name, lo, hi):
    """Locate `AS <name>` (word-bounded, whitespace-tolerant) within char window [lo, hi).
    Returns [start, end] or None. Word boundaries keep `AS Foo` from matching inside
    `AS Foobar`; the regex mirrors _field_span_raw so inner and root fields anchor alike."""
    if not name:
        return None
    pat = re.compile(r"\bAS\s+" + re.escape(name) + r"\b", re.IGNORECASE)
    m = pat.search(sql, lo, hi)
    return [m.start(), m.end()] if m else None


# ---------------------------------------------------------------------------
# Sources (DMOs / CIs referenced in FROM/JOIN)
# ---------------------------------------------------------------------------
def extract_sources(root):
    sources = {}
    for t in root.find_all(exp.Table):
        nm = t.name
        if not nm:
            continue
        kind = "DMO" if nm.endswith("__dlm") else ("CI" if nm.endswith("__cio") else "other")
        sources[nm] = {"name": nm, "kind": kind, "alias": t.alias or None}
    return list(sources.values())


# ---------------------------------------------------------------------------
# Simple-CASE findings (parser-regression relevant) — a lightweight finding pass
# ---------------------------------------------------------------------------
def extract_findings(root, resolver):
    findings = []
    for case in root.find_all(exp.Case):
        if case_form(case) == "simple":
            findings.append({
                "rule": "simple-case",
                "severity": "warn",
                "detail": "Simple CASE (CASE <operand> WHEN ...) — at risk under the UAT parser "
                          "regression; searched CASE (CASE WHEN <cond>) is the resilient form.",
                "sourceSpan": span_of(case, resolver),
            })
    for m in _ID_RE.finditer(resolver.sql):
        findings.append({
            "rule": "hardcoded-setup-id",
            "severity": "warn",
            "detail": f"Hardcoded Salesforce setup ID literal {m.group(1)} — per-env portability hazard; "
                      "prefer a Recordtype__dlm / DeveloperName join.",
            "sourceSpan": [m.start(1) - 1, m.end(1) + 1],
        })
    return findings


# ---------------------------------------------------------------------------
# Build the semantic model
# ---------------------------------------------------------------------------
def build_model(sql, ci_name=None, meanings=None):
    resolver = SpanResolver(sql)
    try:
        root = sqlglot.parse_one(sql, read=DIALECT)
    except Exception as e:
        return {"error": f"parse failed: {e}", "ci": {"apiName": ci_name}, "originalSql": sql}

    # outermost SELECT is the report's primary scope
    top_select = root if isinstance(root, exp.Select) else root.find(exp.Select)

    # Scope tree first — join/filter ownership, whereSpans, and external-ID lineage all
    # hang off it. (Batch B core; design B#1)
    scopes = build_scopes(sql, root)

    # Each extractor gets its OWN resolver (independent forward cursor) so cross-pass
    # ordering can't corrupt span resolution — fields sit at the top of the SQL, filters
    # deep in subqueries; a shared cursor would starve whichever ran second.
    fields = extract_fields(top_select, SpanResolver(sql)) if top_select else []

    # Merge a human-authored meanings sidecar (business meaning per field + CI purpose).
    # The tool never invents these — they come from a reviewed sidecar and are shown as
    # curated content, distinct from the SQL-derived mechanical facts. (enrich pattern)
    meanings = meanings or {}
    field_meanings = meanings.get("fields", {})
    for f in fields:
        fm = field_meanings.get(f["output"])
        if fm:
            f["meaning"] = fm  # {text, confidence}

    joins = extract_joins(scopes, sql)
    join_meanings = meanings.get("joins", {})   # keyed by join target (table/alias)
    for j in joins:
        jm = join_meanings.get(j["target"])
        if jm:
            j["meaning"] = jm

    filters = extract_filters(scopes, sql)
    filter_meanings = meanings.get("filters", {})  # keyed by exact predicate text
    for fl in filters:
        key = " ".join((fl.get("text") or "").split())  # whitespace-normalized predicate
        fm = filter_meanings.get(key)
        if fm:
            fl["meaning"] = fm

    # Inner-scope projections (columns defined inside subqueries). Batch B's scope regions let
    # us resolve each within its owning scope, so these become clickable without colliding with
    # the root fields of the same name. Optional sidecar meanings keyed "<alias>.<output>".
    inner_fields = extract_inner_fields(scopes, sql)
    inner_meanings = meanings.get("innerFields", {})
    for f in inner_fields:
        im = inner_meanings.get(f"{f.get('boundAlias')}.{f['output']}") or inner_meanings.get(f["output"])
        if im:
            f["meaning"] = im

    # grain: keep the Batch A externalIdGrain (string tokens) for back-compat, and add the
    # Batch B resolved lineage under externalId.
    grain = extract_grains(top_select, SpanResolver(sql)) if top_select else {}
    grain["externalId"] = extract_external_id(scopes)

    model = {
        "ci": {"apiName": ci_name},
        "originalSql": sql,
        "purpose": meanings.get("purpose"),  # {text, confidence, basis} or None
        "scopes": _public_scopes(scopes),
        "sources": extract_sources(root),
        "fields": fields,
        "innerFields": inner_fields,
        "joins": joins,
        "filters": filters,
        "grain": grain,
        "findings": extract_findings(root, SpanResolver(sql)),
    }
    return model


def _public_scopes(scopes):
    """Strip the AST-node internals (_select/_projection/region) so the model is
    JSON-serializable and stable.

    Emit duplicate-alias scopes (co/sap, once per FULL-JOIN leg) in reverse source order.
    Consumers that key a lookup by boundAlias with last-wins semantics then resolve a reused
    alias to its FIRST-defined leg — the canonical copy. Parentage stays exact per scope
    (keyed by scopeId), so this ordering is purely presentational and does not collapse the
    legs (the reused-leg invariant still sees all of them)."""
    out = []
    for s in reversed(scopes):
        out.append({
            "scopeId": s["scopeId"],
            "parentScopeId": s["parentScopeId"],
            "boundAlias": s["boundAlias"],
            "kind": s["kind"],
            "baseSources": s["baseSources"],
            "whereSpan": s["whereSpan"],
        })
    return out


# ---------------------------------------------------------------------------
# Onboarding HTML renderer (SQL-first + docked explanation panel + breadcrumb)
# ---------------------------------------------------------------------------
def _esc(s):
    return html.escape(s if s is not None else "")


def render_onboarding_html(model):
    """Render the SQL-first onboarding report. All interaction runs off the embedded
    model; no org connection. Nested clickable spans; docked resizable explanation panel."""
    sql = model.get("originalSql", "")
    ci_name = (model.get("ci") or {}).get("apiName") or "CI"

    # collect all elements that carry a sourceSpan into a flat clickable set
    elements = []

    def add(el, kind, title, explanation, span, meaning=None):
        if span:
            elements.append({
                "id": f"{kind}{len(elements)}",
                "kind": kind, "title": title, "explanation": explanation,
                "meaning": meaning,  # {text, confidence} from the reviewed sidecar, or None
                "span": span,
            })

    for f in model.get("fields", []):
        expl = f"Defines the output column {f['output']}"
        if f.get("aggregation"):
            expl += f" by applying {f['aggregation']} at the final output grain"
        if f.get("transform"):
            expl += f" using a {f['transform']} expression"
        expl += f". Outer projection (lineage not yet traced): {f.get('sourceExpr','')}."
        add(f, "field", f"Field: {f['output']}", expl, f.get("sourceSpan"), meaning=f.get("meaning"))
    for f in model.get("innerFields", []):
        alias = f.get("boundAlias") or "subquery"
        expl = (f"Column {f['output']} defined inside the {alias} subquery. "
                f"Source expression: {f.get('sourceExpr','')}.")
        add(f, "innerfield", f"{alias}.{f['output']}", expl, f.get("sourceSpan"), meaning=f.get("meaning"))
    for j in model.get("joins", []):
        card = j["cardinality"]
        expl = (f"{j['type']} join to {j['target']}. Keys: {', '.join(j['keys']) or 'n/a'}. "
                f"Cardinality: {card['value']} (basis: {card['basis']}, confidence: {card['confidence']}). "
                "Join scope and left-side ownership are not modeled in this phase.")
        add(j, "join", f"Join: {j['target']}", expl, j.get("sourceSpan"), meaning=j.get("meaning"))
    for fl in model.get("filters", []):
        p = fl["purpose"]
        expl = (f"Filter ({p['value']}, inferred/{p['confidence']}). "
                f"{'Portable.' if fl['portable'] else 'Contains a hardcoded ID — portability hazard.'}")
        # title carries the predicate itself so the nav index reads e.g.
        # "curr_qtr_ind = 'Y'" instead of a wall of identical "Filter" entries
        pred = " ".join((fl.get("text") or "").split())  # collapse whitespace
        if len(pred) > 60:
            pred = pred[:57] + "…"
        title = f"Filter: {pred}" if pred else "Filter"
        add(fl, "filter", title, expl, fl.get("sourceSpan"), meaning=fl.get("meaning"))
    for fd in model.get("findings", []):
        add(fd, "finding", f"⚠ {fd['rule']}", fd["detail"], fd.get("sourceSpan"))

    model_json = _json_for_script({"elements": elements, "sql": sql, "ci": ci_name,
                                   "purpose": model.get("purpose"),
                                   "grain": model.get("grain", {})})

    return _HTML_TEMPLATE.replace("__CI_NAME__", _esc(ci_name)).replace("__MODEL_JSON__", model_json)


def _json_for_script(value):
    """Serialize JSON for an inline script without allowing HTML/script termination."""
    return (json.dumps(value, ensure_ascii=False)
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8">
<title>CI Onboarding — __CI_NAME__</title>
<style>
:root{--bg:#0f1420;--panel:#171e2e;--panel2:#1e2740;--ink:#e6ecf5;--muted:#8a97b0;--line:#2b3550;--accent:#4da3ff;--warn:#f5b545;--bad:#ff5d5d;--mono:"SF Mono",ui-monospace,Menlo,Consolas,monospace;}
*{box-sizing:border-box;}html,body{height:100%;}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;display:flex;flex-direction:column;}
header{display:flex;align-items:baseline;gap:14px;padding:13px 20px;background:var(--panel);border-bottom:1px solid var(--line);flex:none;}
header h1{font-size:15px;margin:0;font-family:var(--mono);font-weight:600;}header .sub{color:var(--muted);font-size:12px;}.spacer{flex:1;}
.kind{font-family:var(--mono);font-size:11px;color:var(--accent);border:1px solid var(--line);padding:4px 8px;border-radius:6px;}
.banner{font-size:11.5px;padding:7px 20px;flex:none;background:#12251b;border-bottom:1px solid #24402f;color:#3ecf8e;}
.stage{flex:1;display:flex;min-height:0;}
.navcol{flex:none;width:260px;min-width:180px;max-width:560px;background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column;}
.navcol .navhead{padding:9px 13px;font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);background:var(--panel2);border-bottom:1px solid var(--line);}
.navindex{overflow:auto;flex:1;padding:4px 0;}
.main{flex:1;min-width:0;display:flex;flex-direction:column;}
.main .bar{padding:9px 16px;border-bottom:1px solid var(--line);background:var(--panel2);font-size:12px;color:var(--muted);}
pre.sql{margin:0;padding:16px 18px;flex:1;overflow:auto;font-family:var(--mono);font-size:12.5px;line-height:1.75;color:#cdd8ea;white-space:pre;}
.span{border-radius:3px;cursor:pointer;border-bottom:1px dotted #4da3ff66;}.span:hover{background:#4da3ff22;border-bottom-color:var(--accent);}
.span.innerfield{border-bottom-color:#8a97b077;}.span.innerfield:hover{border-bottom-color:var(--muted);}
.navitem.innerfield{color:#9fb4d6;}
.span.hot{background:#3a2f10;outline:1px solid var(--warn);}.span.hot.finding{background:#3a1414;outline-color:var(--bad);}
.span.ctx{background:#4da3ff14;outline:1px dashed #4da3ff55;}
.grip{flex:none;width:6px;cursor:col-resize;background:var(--line);}.grip:hover{background:var(--accent);}
aside{flex:none;width:400px;min-width:300px;max-width:680px;background:var(--panel);border-left:1px solid var(--line);display:flex;flex-direction:column;}
aside.collapsed{width:40px!important;min-width:40px;}
aside .ahead{padding:10px 13px;border-bottom:1px solid var(--line);background:var(--panel2);display:flex;align-items:center;gap:8px;font-size:12px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);}
.collapse{margin-left:auto;background:transparent;border:1px solid var(--line);color:var(--muted);border-radius:6px;padding:2px 8px;cursor:pointer;}
.crumbs{padding:8px 13px;border-bottom:1px solid var(--line);font-size:11px;display:flex;flex-wrap:wrap;gap:4px;align-items:center;}
.crumb{color:var(--accent);cursor:pointer;border:1px solid var(--line);border-radius:20px;padding:2px 9px;background:#0b1018;}
.crumb.cur{color:var(--ink);border-color:var(--accent);}.crumb.sep{border:0;color:var(--muted);padding:0 1px;background:none;cursor:default;}
.abody{padding:14px;overflow:auto;flex:1;}aside.collapsed .abody,aside.collapsed .ahead span,aside.collapsed .crumbs{display:none;}
.exp h3{margin:0 0 6px;font-size:14px;}.exp p{margin:0 0 10px;}.empty{color:var(--muted);font-size:12.5px;}
.grainbox{margin-top:14px;border-top:1px solid var(--line);padding-top:10px;font-size:12px;}
.grainbox h4{margin:0 0 4px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);}
.grainbox code{font-family:var(--mono);font-size:11px;color:#9fb4d6;}
.inf{font-size:9.5px;color:var(--muted);}
.mlabel{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:3px;}
.meaning{background:#12251b;border:1px solid #24402f;border-radius:8px;padding:10px 12px;margin:2px 0 12px;font-size:13px;line-height:1.5;}
.mech{background:#0b1018;border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-size:12px;color:#9fb4d6;}
.purposebox{background:#1a2440;border:1px solid #2b3550;border-radius:8px;padding:11px 13px;margin-bottom:12px;font-size:13px;line-height:1.55;}
.conf{color:var(--muted);font-size:10px;text-transform:none;letter-spacing:0;}
.barnote{color:var(--muted);font-size:11px;font-style:italic;}
.navgroup{font-size:9.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);padding:8px 13px 3px;}
.navitem{padding:4px 13px;font-family:var(--mono);font-size:11.5px;cursor:pointer;color:#cdd8ea;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-left:2px solid transparent;}
.navitem:hover{background:#ffffff0a;}
.navitem.active{background:#3a2f1099;border-left-color:var(--warn);color:#fff;}
.navitem.finding{color:var(--bad);}
.navitem .dot{opacity:.55;font-size:10px;}
.themetoggle{background:transparent;border:1px solid var(--line);color:var(--muted);border-radius:6px;padding:2px 8px;cursor:pointer;font-size:13px;line-height:1;align-self:center;}
.themetoggle:hover{border-color:var(--accent);color:var(--ink);}
[data-theme="light"]{--bg:#f4f6fb;--panel:#fff;--panel2:#edf0f7;--ink:#1a2035;--muted:#6b7a99;--line:#d4daea;--accent:#1a6fd4;--warn:#b87200;--bad:#cc2222;}
[data-theme="light"] pre.sql{color:#1a2035;}
[data-theme="light"] .span.hot{background:#fff3d4;}
[data-theme="light"] .span.hot.finding{background:#ffe0e0;}
[data-theme="light"] .meaning{background:#e8f5ef;border-color:#a8d5b8;}
[data-theme="light"] .mech{background:#f0f2f8;color:#4a5a7a;}
[data-theme="light"] .purposebox{background:#e8eef8;border-color:#b8cae8;}
[data-theme="light"] .banner{background:#e8f5ef;border-color:#a8d5b8;color:#1a7a4a;}
[data-theme="light"] .crumb{background:#edf0f7;}
[data-theme="light"] .navitem{color:#2a3550;}
[data-theme="light"] .navitem.active{background:#fff3d4bb;}
[data-theme="light"] .navitem.innerfield{color:#4a6088;}
</style></head>
<body>
<header><h1>__CI_NAME__</h1><span class="sub">CI onboarding · offline</span><span class="spacer"></span>
<button class="themetoggle" id="themetog" onclick="toggleTheme()" title="Toggle light/dark theme">&#9681;</button>
<span class="kind">Static contract · click SQL to explain</span></header>
<div class="banner">Read the SQL, click any part (field, join, filter, flag) for an explanation. No counts, no env — offline, safe to commit.</div>
<div class="stage">
 <div class="navcol">
  <div class="navhead">Contents — click to jump</div>
  <div class="navindex" id="navindex"></div>
 </div>
 <div class="grip" id="lgrip" title="drag to resize"></div>
 <div class="main">
  <div class="bar"><strong>Annotated SQL</strong> — pick from Contents, or click a highlighted fragment; breadcrumb widens scope. <span class="barnote">Explained elements are listed in Contents; other SQL is shown for context and isn't clickable.</span></div>
  <pre class="sql" id="sqlpane"></pre>
 </div>
 <div class="grip" id="grip" title="drag to resize"></div>
 <aside id="aside">
  <div class="ahead"><span>Explanation</span><button class="collapse" onclick="toggleCollapse()">&#10217;&#10216;</button></div>
  <div class="crumbs" id="crumbs"></div>
  <div class="abody" id="abody"><div id="purpose"></div><p class="empty" id="hint">Pick an item from Contents on the left, or click a highlighted fragment in the SQL.</p></div>
 </aside>
</div>
<script>
(function(){const s=localStorage.getItem('civ-theme'),m=window.matchMedia('(prefers-color-scheme:light)').matches;document.documentElement.dataset.theme=s||(m?'light':'dark');})();
function toggleTheme(){const n=document.documentElement.dataset.theme==='light'?'dark':'light';document.documentElement.dataset.theme=n;localStorage.setItem('civ-theme',n);}
const MODEL = __MODEL_JSON__;
const SQL = MODEL.sql, ELS = MODEL.elements;
function esc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
// nested-span render: boundary sweep, open longer spans first so parents wrap children
function buildSql(){
  const marks = ELS.filter(e=>e.span).map(e=>({id:e.id,s:e.span[0],e2:e.span[1],kind:e.kind}));
  const opens={},closes={};
  marks.forEach(m=>{(opens[m.s]=opens[m.s]||[]).push(m);(closes[m.e2]=closes[m.e2]||[]).push(m);});
  let h="";
  for(let i=0;i<=SQL.length;i++){
    (closes[i]||[]).forEach(()=>h+="</span>");
    (opens[i]||[]).sort((a,b)=>(b.e2-b.s)-(a.e2-a.s)).forEach(m=>{
      h+='<span class="span '+m.kind+'" data-id="'+m.id+'" onclick="pick(event,\''+m.id+'\')">';});
    if(i<SQL.length) h+=esc(SQL[i]);
  }
  document.getElementById("sqlpane").innerHTML=h;
}
function byId(id){return ELS.find(e=>e.id===id);}
// containment chain: elements whose span encloses this one (for breadcrumb widen)
function chain(id){
  const t=byId(id); if(!t)return[];
  const out=ELS.filter(e=>e.span && e.span[0]<=t.span[0] && e.span[1]>=t.span[1]);
  out.sort((a,b)=>(b.span[1]-b.span[0])-(a.span[1]-a.span[0])); // widest first
  return out;
}
function clearMarks(){document.querySelectorAll(".span.hot,.span.ctx").forEach(e=>e.classList.remove("hot","ctx"));}
function show(id){
  clearMarks();
  chain(id).forEach(e=>{const el=document.querySelector('.span[data-id="'+e.id+'"]');if(el)el.classList.add(e.id===id?"hot":"ctx");});
  const tgt=document.querySelector('.span[data-id="'+id+'"]'); if(tgt)tgt.scrollIntoView({block:"center",behavior:"smooth"});
  const cr=document.getElementById("crumbs");cr.innerHTML="";
  chain(id).forEach((e,i)=>{
    if(i){const s=document.createElement("span");s.className="crumb sep";s.textContent="›";cr.appendChild(s);}
    const c=document.createElement("span");c.className="crumb"+(e.id===id?" cur":"");c.textContent=e.title.replace(/^(Field|Join|Filter): ?/,"").slice(0,28);
    c.title=e.title;c.onclick=()=>show(e.id);cr.appendChild(c);});
  const m=byId(id);
  let meaningHtml='';
  if(m.meaning&&m.meaning.text){
    meaningHtml='<div class="meaning"><div class="mlabel">What it means'+
      (m.meaning.confidence?' <span class="conf">('+esc(m.meaning.confidence)+')</span>':'')+
      '</div>'+esc(m.meaning.text)+'</div>';
  }
  const mech='<div class="mech"><div class="mlabel">From the SQL</div>'+esc(m.explanation)+'</div>';
  document.getElementById("abody").innerHTML='<div class="exp"><h3>'+esc(m.title)+'</h3>'+meaningHtml+mech+'</div>';
  // sync the nav index active state + keep the active item in view
  document.querySelectorAll(".navitem").forEach(n=>n.classList.toggle("active",n.dataset.id===id));
  const ai=document.querySelector('.navitem.active'); if(ai)ai.scrollIntoView({block:"nearest"});
  if(document.getElementById("aside").classList.contains("collapsed"))toggleCollapse();
}
function pick(ev,id){ev.stopPropagation();show(id);}
// Build the panel navigation index — grouped, clickable. Scanning this list replaces
// hunting the SQL for the next clickable region (the dead-zone-scroll problem on nested CIs).
function buildNav(){
  const groups=[["field","Output fields"],["innerfield","Subquery columns"],["join","Joins"],["filter","Filters"],["finding","Findings / flags"]];
  const host=document.getElementById("navindex");let h="";
  groups.forEach(([kind,label])=>{
    const items=ELS.filter(e=>e.kind===kind);
    if(!items.length)return;
    h+='<div class="navgroup">'+esc(label)+' ('+items.length+')</div>';
    items.forEach(e=>{
      const t=e.title.replace(/^(Field|Join|Filter): ?/,"");
      const star=e.meaning&&e.meaning.text?'<span class="dot"> ●</span>':'';
      h+='<div class="navitem '+esc(e.kind)+'" data-id="'+esc(e.id)+'" onclick="show(\''+esc(e.id)+'\')" title="'+esc(e.title)+'">'+esc(t)+star+'</div>';
    });
  });
  host.innerHTML=h;
}
// grain (SQL / external-ID / business) stays in the model but is not rendered in the
// onboarding panel — it's a diagnostic/contract feature (Batch C) that needs resolved
// lineage to be meaningful. Half-populated placeholders were clutter here.
function toggleCollapse(){document.getElementById("aside").classList.toggle("collapsed");}
(function(){const g=document.getElementById("grip"),a=document.getElementById("aside"),
  lg=document.getElementById("lgrip"),nc=document.querySelector(".navcol");let d=false,dl=false;
 g.addEventListener("mousedown",e=>{d=true;e.preventDefault();});
 lg.addEventListener("mousedown",e=>{dl=true;e.preventDefault();});
 window.addEventListener("mousemove",e=>{
   if(d){a.style.width=Math.max(300,Math.min(680,window.innerWidth-e.clientX))+"px";}
   else if(dl){nc.style.width=Math.max(180,Math.min(560,e.clientX))+"px";}});
 window.addEventListener("mouseup",()=>{d=false;dl=false;})})();
buildSql();buildNav();  // grain box intentionally not rendered (Batch C diagnostic feature)
(function(){var pu=MODEL.purpose;if(pu&&pu.text){document.getElementById("purpose").innerHTML=
  '<div class="purposebox"><div class="mlabel">CI purpose'+(pu.confidence?' <span class="conf">('+esc(pu.confidence)+')</span>':'')+'</div>'+esc(pu.text)+'</div>';}})();
</script></body></html>"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def resolve_input(args):
    """Returns (sql_text, ci_name, sql_path)."""
    if args.input:
        with open(args.input) as f:
            return f.read(), args.name or os.path.basename(args.input).replace(".sql", ""), args.input
    if args.client_root and args.name:
        path = os.path.join(args.client_root, "queries", f"{args.name}.sql")
        with open(path) as f:
            return f.read(), args.name, path
    raise SystemExit("Provide --input <file.sql> or --client-root <dir> + --name <CI>")


def load_meanings(args, sql_path):
    """Load a reviewed business-meanings sidecar if present. Explicit --meanings wins;
    otherwise look for <sql-path>-meanings.json beside the input. Never invented — the
    tool only renders what a human authored and reviewed."""
    path = args.meanings
    if not path and sql_path:
        cand = re.sub(r"\.sql$", "", sql_path) + "-meanings.json"
        if os.path.exists(cand):
            path = cand
    if not path:
        return None
    with open(path) as f:
        return json.load(f)


def main():
    p = argparse.ArgumentParser(description="CI Visualize — onboarding report (offline, phase 1)")
    p.add_argument("--input", help="Path to a CI .sql file")
    p.add_argument("--client-root", help="Client Data360 root (resolves queries/<name>.sql)")
    p.add_argument("--name", help="CI api name (label + client-root resolution)")
    p.add_argument("--out", help="Output HTML path (default: <name>-onboarding.html)")
    p.add_argument("--meanings", help="Business-meanings sidecar JSON (default: <sql>-meanings.json if present)")
    p.add_argument("--model-only", action="store_true", help="Emit the JSON semantic model, no HTML")
    args = p.parse_args()

    sql, ci_name, sql_path = resolve_input(args)
    model = build_model(sql, ci_name, meanings=load_meanings(args, sql_path))

    if "error" in model:
        print(f"ERROR: {model['error']}", file=sys.stderr)
        raise SystemExit(2)

    if args.model_only:
        print(json.dumps(model, indent=2))
        return

    if args.out:
        out = Path(args.out).expanduser()
    elif args.client_root:
        out = Path(args.client_root).expanduser() / "reports" / f"{ci_name}-onboarding.html"
    else:
        p.error("--out is required when generating HTML from standalone --input")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        f.write(render_onboarding_html(model))
    n = (len(model.get("fields", [])) + len(model.get("innerFields", []))
         + len(model.get("joins", [])) + len(model.get("filters", [])) + len(model.get("findings", [])))
    print(f"WROTE {out}  ({n} clickable elements, {len(model.get('findings', []))} finding(s))")


if __name__ == "__main__":
    main()
