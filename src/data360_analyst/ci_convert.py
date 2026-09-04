#!/usr/bin/env python3
"""
Data 360 CI SQL Converter
Mechanically rewrites Query Editor (Hyper / Trino) SQL — and SQL flagged by
ci_audit.py — into CI editor-compatible form.

This is the scripted counterpart to the data360-sql-convert skill. The skill
documents the rules; this script applies them. Where a rule can be auto-fixed
deterministically, the converter rewrites it and emits an "auto" note. Where
the fix requires human judgment (self-joins, currency target, DLO→DMO mapping,
etc.) the converter leaves the SQL alone and emits a "flag" note explaining
what to do.

Usage (recommended — matches the rest of the toolkit):
    python3 ci_convert.py --output-dir <client-data360-folder>
    # reads <output-dir>/queries/*.sql
    # writes <output-dir>/queries-converted/*.sql
    # writes <output-dir>/reports/ci-convert.md

Single-file modes:
    python3 ci_convert.py --input file.sql --stdout
    python3 ci_convert.py --input file.sql --diff
    python3 ci_convert.py --input file.sql --in-place --backup

Read-only by default. Never overwrites the source unless --in-place is set.

Passes (mirrors data360-sql-convert skill):
    1. Structural        — CTEs, top-level DISTINCT/ORDER BY, EXISTS, IN-bare-col
    2. Identifiers       — DMO alias strip, alias=field rename flag, __c suffix
    3. Functions         — _UNSUPPORTED_FUNCTIONS swaps, COUNT(DISTINCT), EXTRACT
    4. Expressions       — AVG(CASE), CASE-NULL types, NTILE alias reuse
    5. JOINs             — LEFT-JOIN-WHERE pushdown, FKQ flag, NULL-key flag
    6. WHERE cleanup     — alias/aggregate-in-WHERE flags
    7. SELECT FIRST-wrap — non-grouped dimensions
    8. CONCAT provenance — flag-only (requires CI split)

Phase 1 implements passes 1-4. Phases 2+ add 5-8.
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import sqlglot
    from sqlglot import exp
except ImportError:
    print("ERROR: sqlglot not installed. Use the bundled .venv or `pip install sqlglot`.")
    sys.exit(1)

# Reuse helpers and constants from the audit module so detection and
# rewriting share a single source of truth.
from data360_analyst.ci_audit import (
    DIALECT,
    _UNSUPPORTED_FUNCTIONS,
    audit_file as _audit_file,
    parse_file as _parse_file,
)


# ── Result types ────────────────────────────────────────────────────────────


# Severity meanings:
#   "auto"   — rewrite applied; included for transparency.
#   "flag"   — fix requires human judgment; SQL was NOT modified.
#   "manual" — the converter is unsure (ambiguous source); review needed.
NOTE_SEVERITIES = ("auto", "flag", "manual")


@dataclass
class ConversionNote:
    rule: str
    severity: str
    message: str
    snippet: str | None = None  # original fragment, for context

    def __post_init__(self):
        if self.severity not in NOTE_SEVERITIES:
            raise ValueError(f"unknown severity: {self.severity!r}")


@dataclass
class ConversionResult:
    converted_sql: str
    notes: list[ConversionNote] = field(default_factory=list)
    # Audit rules that still fire on the converted SQL — populated by the
    # driver after re-running ci_audit.audit_file on the output.
    remaining_violations: list[str] = field(default_factory=list)

    def auto_count(self):
        return sum(1 for n in self.notes if n.severity == "auto")

    def flag_count(self):
        return sum(1 for n in self.notes if n.severity in ("flag", "manual"))


# ── AST-pass infrastructure ─────────────────────────────────────────────────


def _apply(tree, transformer, notes):
    """Run a transformer over the tree. The transformer is called once with
    `(tree, notes)` and may either mutate the tree in place or return a new
    root node.
    """
    result = transformer(tree, notes)
    return result if result is not None else tree


# ── Pass 2 (subset) — identifier hygiene reachable from the AST ─────────────


def _strip_double_quoted_identifiers(tree, notes):
    """Drop the `quoted` flag on identifiers. The CI editor rejects
    double-quoted DMO names and treats `"value"` as an identifier rather
    than a string literal — bare identifiers always parse correctly.

    sqlglot's spark dialect preserves `quoted=True` and re-emits as
    backticks, so we have to clear the flag explicitly.
    """
    for ident in tree.find_all(exp.Identifier):
        if ident.args.get("quoted"):
            notes.append(ConversionNote(
                rule="double_quoted_identifier",
                severity="auto",
                message="Removed double-quote/backtick wrapping from identifier "
                        "(CI editor rejects quoted identifiers).",
                snippet=ident.name,
            ))
            ident.set("quoted", False)
    return tree


def _strip_dmo_table_aliases(tree, notes):
    """`FROM ssot__Account__dlm AS a ... a.field` → use the full DMO name
    everywhere. The CI editor's FQK validator surfaces ambiguous-column
    errors when DMOs are aliased; the canvas guide explicitly recommends
    full names. Ignores subquery aliases (`FROM (...) AS sub`) — those
    are necessary scoping, not the failure mode.
    """
    rewrites = {}  # alias-name (lowercased) → DMO name
    for tbl in tree.find_all(exp.Table):
        name = tbl.name or ""
        if not (name.endswith("__dlm") or name.endswith("__cio")):
            continue
        alias_node = tbl.args.get("alias")
        if not alias_node:
            continue
        alias_name = alias_node.this.name if alias_node.this else None
        if not alias_name or alias_name == name:
            continue
        rewrites[alias_name.lower()] = name
        tbl.set("alias", None)

    if not rewrites:
        return tree

    for col in tree.find_all(exp.Column):
        tbl_ref = col.args.get("table")
        if tbl_ref is None:
            continue
        ref = tbl_ref.name if hasattr(tbl_ref, "name") else str(tbl_ref)
        full = rewrites.get(ref.lower())
        if full:
            col.set("table", exp.to_identifier(full))

    notes.append(ConversionNote(
        rule="dmo_table_alias",
        severity="auto",
        message=f"Stripped DMO/CIO table alias(es) and rewrote column "
                f"qualifiers: {', '.join(sorted(rewrites.values()))}.",
    ))
    return tree


# ── Pass 3 — function swaps (CI-rejected → CI-supported) ────────────────────


def _expr_to_string(node):
    return node.sql(dialect=DIALECT) if isinstance(node, exp.Expression) else str(node)


def _rewrite_unsupported_functions(tree, notes):
    """Mechanically swap CI-rejected functions for the canonical CI-supported
    form. Only handles substitutions that don't require user input — flags
    DISTANCE / TRY_CONVERT_CURRENCY arity / non-trivial transforms instead.
    """
    seen = set()

    def transform(node):
        # COALESCE / NVL — sqlglot normalizes both to exp.Coalesce.
        # 2-arg form is handled by the post-render textual swap (no AST
        # rewrite needed; sqlglot would just re-emit COALESCE anyway).
        # 3+ arg form must be expanded into nested IFNULL via Anonymous.
        if isinstance(node, exp.Coalesce):
            args = [node.this] + (node.expressions or [])
            args = [a.copy() for a in args if a is not None]
            if len(args) < 3:
                return node
            result = args[-1]
            for a in reversed(args[:-1]):
                result = exp.Anonymous(this="IFNULL", expressions=[a, result])
            seen.add("coalesce")
            return result

        # NVL2(a, b, c) → CASE WHEN a IS NOT NULL THEN b ELSE c END.
        # sqlglot normalizes to exp.Nvl2 (typed); also handle the rare
        # Anonymous fallback for safety.
        is_nvl2_typed = isinstance(node, exp.Nvl2)
        is_nvl2_anon = (
            isinstance(node, exp.Anonymous)
            and (node.name or "").lower() == "nvl2"
        )
        if is_nvl2_typed or is_nvl2_anon:
            if is_nvl2_typed:
                # exp.Nvl2 stores: this=a, true=b, false=c
                a = node.this
                b = node.args.get("true")
                c = node.args.get("false")
                args = [x for x in (a, b, c) if x is not None]
            else:
                args = node.expressions or []
            if len(args) == 3:
                a, b, c = (x.copy() for x in args)
                seen.add("nvl2")
                return exp.Case(
                    ifs=[exp.If(this=exp.Not(this=exp.Is(this=a, expression=exp.Null())), true=b)],
                    default=c,
                )

        # EXTRACT(YEAR FROM dt) → YEAR(dt) (and similar for MONTH/DAY/HOUR/QUARTER)
        if isinstance(node, exp.Extract):
            unit = node.this
            unit_name = (unit.name if hasattr(unit, "name") else str(unit)).upper()
            target_class = {
                "YEAR": exp.Year,
                "MONTH": exp.Month,
                "DAY": exp.Day,
                "HOUR": exp.Hour,
                "QUARTER": exp.Quarter,
            }.get(unit_name)
            src = node.args.get("expression")
            if target_class and src is not None:
                seen.add("extract")
                return target_class(this=src.copy())

        # TRY_CAST(x AS T) → CAST(x AS T) — sqlglot exposes a typed
        # exp.TryCast node. Mechanical rewrite + flag the runtime risk.
        if isinstance(node, exp.TryCast):
            inner = node.this
            target = node.to
            if inner is not None and target is not None:
                seen.add("try_cast")
                notes.append(ConversionNote(
                    rule="try_cast_runtime_risk",
                    severity="flag",
                    message="TRY_CAST → CAST applied. CAST throws at runtime on "
                            "bad values (no graceful-null fallback). Verify the "
                            "input is clean or add upstream filtering.",
                    snippet=node.sql(dialect=DIALECT)[:120],
                ))
                return exp.Cast(this=inner.copy(), to=target.copy())

        # DECODE(x, 1, 'A', 2, 'B', 'C') → CASE expression. sqlglot
        # normalizes to exp.DecodeCase whose .expressions are the
        # value/result pairs and trailing default.
        if isinstance(node, exp.DecodeCase):
            args = [a.copy() for a in node.expressions or []]
            if len(args) >= 3:
                expr = args[0]
                pairs = args[1:]
                ifs = []
                while len(pairs) >= 2:
                    val = pairs.pop(0)
                    result = pairs.pop(0)
                    ifs.append(exp.If(
                        this=exp.EQ(this=expr.copy(), expression=val),
                        true=result,
                    ))
                default = pairs[0] if pairs else None
                if ifs:
                    seen.add("decode")
                    return exp.Case(ifs=ifs, default=default)

        # MEDIAN(col) → PERCENTILE(col, 0.5)
        if isinstance(node, exp.Median):
            inner = node.this
            if inner is not None:
                seen.add("median")
                return exp.Anonymous(
                    this="PERCENTILE",
                    expressions=[inner.copy(), exp.Literal.number("0.5")],
                )

        # Anonymous function-name swaps where the rewrite is non-trivial /
        # judgment-dependent — emit a flag note instead of auto-rewriting.
        if isinstance(node, exp.Anonymous):
            name = (node.name or "").lower()
            if name == "distance":
                notes.append(ConversionNote(
                    rule="distance_function",
                    severity="flag",
                    message="DISTANCE(...) is unsupported. Replace with bounding-box "
                            "BETWEEN predicates (1° lat/lng ≈ 111 km / 69 mi). "
                            "Requires lat/lng radius inputs — left untouched.",
                    snippet=node.sql(dialect=DIALECT)[:120],
                ))
            elif name == "split_part":
                notes.append(ConversionNote(
                    rule="split_part",
                    severity="flag",
                    message="SPLIT_PART(...) is unsupported. Replace with "
                            "REGEXP_REPLACE — requires per-call delimiter choice; "
                            "left untouched.",
                    snippet=node.sql(dialect=DIALECT)[:120],
                ))
            elif name == "replace":
                notes.append(ConversionNote(
                    rule="replace_function",
                    severity="flag",
                    message="REPLACE(...) is unsupported. Use REGEXP_REPLACE — "
                            "left untouched (escape semantics differ).",
                    snippet=node.sql(dialect=DIALECT)[:120],
                ))
            elif name == "try_convert_currency":
                arity = len(node.expressions or [])
                if arity != 3:
                    notes.append(ConversionNote(
                        rule="try_convert_currency_arity",
                        severity="flag",
                        message=f"TRY_CONVERT_CURRENCY arity={arity}; CI editor "
                                "requires exactly 3 args (amount, 'SRC_ISO', 'TGT_ISO'). "
                                "Add the source/target ISO codes manually.",
                        snippet=node.sql(dialect=DIALECT)[:120],
                    ))

        return node

    new_tree = tree.transform(transform)
    if seen:
        for fn in sorted(seen):
            notes.append(ConversionNote(
                rule=f"unsupported_function:{fn}",
                severity="auto",
                message=f"Replaced {fn.upper()}(...) with CI editor-supported "
                        f"equivalent ({_UNSUPPORTED_FUNCTIONS.get(fn, '?')}).",
            ))
    return new_tree


def _rewrite_count_distinct(tree, notes):
    """`COUNT(DISTINCT col)` → `APPROX_COUNT_DISTINCT(col)`. The validator
    rejects COUNT(DISTINCT) with an explicit pointer to the substitute.
    """
    fired = False

    def transform(node):
        nonlocal fired
        if isinstance(node, exp.Count):
            inner = node.args.get("this")
            if isinstance(inner, exp.Distinct):
                # Distinct.expressions is a list; CI editor supports
                # APPROX_COUNT_DISTINCT(col) on a single column.
                cols = inner.expressions or []
                if len(cols) == 1:
                    fired = True
                    return exp.ApproxDistinct(this=cols[0].copy())
                if len(cols) > 1:
                    notes.append(ConversionNote(
                        rule="count_distinct_multi_col",
                        severity="flag",
                        message=f"COUNT(DISTINCT a, b, ...) over {len(cols)} columns — "
                                "APPROX_COUNT_DISTINCT only takes one column. Rewrite "
                                "manually (e.g. CONCAT keys before counting).",
                        snippet=node.sql(dialect=DIALECT)[:120],
                    ))
        return node

    new_tree = tree.transform(transform)
    if fired:
        notes.append(ConversionNote(
            rule="count_distinct",
            severity="auto",
            message="Replaced COUNT(DISTINCT col) with APPROX_COUNT_DISTINCT(col).",
        ))
    return new_tree


# ── Pass 4 — expression-level traps ─────────────────────────────────────────


def _rewrite_avg_case(tree, notes):
    """`AVG(CASE WHEN c THEN x ELSE NULL END)` →
    `SUM(CASE WHEN c THEN x ELSE 0 END) / NULLIF(COUNT(CASE WHEN c THEN 1 END), 0)`.

    The validator rejects AVG(CASE ...) nesting outright. Only rewrite
    when the CASE has exactly one WHEN branch with a numeric THEN — more
    complex shapes get a flag note.
    """
    fired = False

    def transform(node):
        nonlocal fired
        if not isinstance(node, exp.Avg):
            return node
        inner = node.this
        if not isinstance(inner, exp.Case):
            return node
        ifs = inner.args.get("ifs") or []
        if len(ifs) != 1:
            notes.append(ConversionNote(
                rule="avg_case_complex",
                severity="flag",
                message="AVG(CASE ...) with multiple WHEN branches — too complex "
                        "to auto-rewrite. Convert manually to "
                        "SUM(CASE ...) / NULLIF(COUNT(CASE ...), 0).",
                snippet=node.sql(dialect=DIALECT)[:140],
            ))
            return node
        branch = ifs[0]
        cond = branch.this
        true_val = branch.args.get("true")
        if cond is None or true_val is None:
            return node

        # SUM(CASE WHEN cond THEN x ELSE 0 END)
        sum_case = exp.Case(
            ifs=[exp.If(this=cond.copy(), true=true_val.copy())],
            default=exp.Literal.number("0"),
        )
        sum_expr = exp.Sum(this=sum_case)
        # COUNT(CASE WHEN cond THEN 1 END)
        count_case = exp.Case(
            ifs=[exp.If(this=cond.copy(), true=exp.Literal.number("1"))],
        )
        count_expr = exp.Count(this=count_case)
        # NULLIF(COUNT(...), 0)
        nullif = exp.Nullif(this=count_expr, expression=exp.Literal.number("0"))
        fired = True
        return exp.Div(this=sum_expr, expression=nullif)

    new_tree = tree.transform(transform)
    if fired:
        notes.append(ConversionNote(
            rule="avg_case_nesting",
            severity="auto",
            message="Rewrote AVG(CASE ...) as "
                    "SUM(CASE ...) / NULLIF(COUNT(CASE ...), 0).",
        ))
    return new_tree


def _rewrite_case_null_branches(tree, notes):
    """`CASE WHEN cond THEN <numeric> ELSE NULL END` →
    `CASE WHEN cond THEN <numeric> ELSE 0 END` (or `0.0` / `''`).

    The validator rejects CASE with mixed NULL + concrete-typed branches.
    Only rewrites when sibling branches are unambiguously typed literals;
    otherwise flags for manual review.
    """
    fired = False

    def classify(node):
        if isinstance(node, exp.Null):
            return "null"
        if isinstance(node, exp.Literal):
            return "string" if node.is_string else "number"
        if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
            return "number" if not node.this.is_string else "string"
        return "other"

    def transform(node):
        nonlocal fired
        if not isinstance(node, exp.Case):
            return node
        ifs = node.args.get("ifs") or []
        default = node.args.get("default")
        types = set()
        for if_ in ifs:
            t = if_.args.get("true")
            if t is not None:
                types.add(classify(t))
        if default is not None:
            types.add(classify(default))

        if "null" not in types:
            return node
        concrete = types - {"null", "other"}
        if not concrete:
            return node
        # Mixed types other than null+concrete-singleton → flag, don't guess.
        if len(concrete) > 1:
            notes.append(ConversionNote(
                rule="case_mixed_branch_types",
                severity="flag",
                message="CASE branches mix NULL and multiple concrete types "
                        f"({sorted(concrete)}). Pick a target type and replace "
                        "NULL with the typed zero (`0`, `0.0`, `''`).",
                snippet=node.sql(dialect=DIALECT)[:140],
            ))
            return node

        kind = next(iter(concrete))
        if kind == "number":
            replacement = exp.Literal.number("0")
        elif kind == "string":
            replacement = exp.Literal.string("")
        else:
            return node  # boolean / other — leave alone

        # Replace explicit Null branches in-place.
        changed = False
        for if_ in ifs:
            t = if_.args.get("true")
            if isinstance(t, exp.Null):
                if_.set("true", replacement.copy())
                changed = True
        if isinstance(default, exp.Null):
            node.set("default", replacement.copy())
            changed = True
        if changed:
            fired = True
        return node

    new_tree = tree.transform(transform)
    if fired:
        notes.append(ConversionNote(
            rule="case_mixed_types",
            severity="auto",
            message="Replaced explicit NULL in CASE branches with typed zero "
                    "(`0` / `''`) to satisfy CI editor type-consistency check.",
        ))
    return new_tree


# ── Pass 1 — structural top-level shape ─────────────────────────────────────


def _strip_top_level_order_by(tree, notes):
    """Remove top-level ORDER BY. CI output is unordered; the validator
    rejects top-level ORDER BY. Window-function ORDER BY (inside OVER(...))
    is preserved automatically — that's a different node arg.
    """
    if not isinstance(tree, exp.Select):
        return tree
    if tree.args.get("order"):
        order_sql = tree.args["order"].sql(dialect=DIALECT)
        tree.set("order", None)
        notes.append(ConversionNote(
            rule="top_level_order_by",
            severity="auto",
            message="Removed top-level ORDER BY — CI output is unordered. "
                    "Sort downstream in segments / BI, or use a window "
                    "function (ROW_NUMBER, RANK) for rank columns.",
            snippet=order_sql[:120],
        ))
    return tree


def _flag_self_joins(tree, notes):
    """Same DMO appearing twice in a single FROM/JOIN scope. CI editor
    rejects self-joins; the canvas guide says to pre-build a copy in an
    upstream CI/transform. Flag-only — no auto-fix possible.
    """
    for select in tree.find_all(exp.Select):
        seen = {}
        candidates = []
        from_node = select.args.get("from_") or select.args.get("from")
        if from_node is not None:
            for tbl in from_node.find_all(exp.Table):
                candidates.append(tbl)
        for join in select.args.get("joins") or []:
            for tbl in join.find_all(exp.Table):
                candidates.append(tbl)
        for tbl in candidates:
            name = tbl.name or ""
            if not (name.endswith("__dlm") or name.endswith("__cio")):
                continue
            seen[name.lower()] = seen.get(name.lower(), 0) + 1
        for n, count in seen.items():
            if count > 1:
                notes.append(ConversionNote(
                    rule="self_join",
                    severity="flag",
                    message=f"DMO `{n}` appears {count}× in the same scope. "
                            "Self-joins are unsupported — pre-build the second "
                            "copy in an upstream CI / transform.",
                ))
    return tree


def _flag_dlo_in_from(tree, notes):
    """References to DLOs (`__dll`) in FROM/JOIN. Only DMOs are valid in
    CI SQL. Flag-only — requires looking up the DLO→DMO mapping in the
    org's intake sidecars.
    """
    for tbl in tree.find_all(exp.Table):
        name = tbl.name or ""
        if name.endswith("__dll"):
            notes.append(ConversionNote(
                rule="dlo_in_from",
                severity="flag",
                message=f"DLO `{name}` referenced in FROM/JOIN — CI editor "
                        "only accepts DMOs (`__dlm`). Replace with the DMO "
                        "this DLO maps to (check `object-model/mappings/`).",
            ))
    return tree


def _alias_in_subquery(tree, notes):
    """`IN (SELECT col FROM x)` → `IN (SELECT col AS col FROM x)`. The CI
    editor rejects bare inner columns in IN-subqueries.
    """
    fired = False
    for in_node in tree.find_all(exp.In):
        q = in_node.args.get("query")
        if q is None:
            continue
        inner = q if isinstance(q, exp.Select) else getattr(q, "this", None)
        if not isinstance(inner, exp.Select):
            continue
        for i, proj in enumerate(inner.expressions or []):
            if isinstance(proj, exp.Alias):
                continue
            if not isinstance(proj, exp.Column):
                continue
            col_name = proj.name
            if not col_name:
                continue
            # Alias must differ from the source field name — the CI editor
            # rejects `Id__c AS Id__c`. Append a stable suffix so the rewrite
            # is deterministic.
            inner.expressions[i] = exp.Alias(
                this=proj.copy(),
                alias=exp.to_identifier(f"{col_name}_alias"),
            )
            fired = True
    if fired:
        notes.append(ConversionNote(
            rule="in_subquery_unaliased",
            severity="auto",
            message="Added explicit aliases to IN(SELECT ...) inner columns.",
        ))
    return tree


def _flag_top_level_cte(tree, notes):
    """Flag top-level CTEs. The mechanical rewrite (CTE → subquery in FROM)
    is non-trivial — requires substituting every reference, preserving
    GROUP BY/HAVING semantics, and handling multi-CTE chains. Flag-only
    in Phase 1; auto-rewrite is a Phase-2 candidate.
    """
    if not isinstance(tree, exp.Select):
        return tree
    with_node = tree.args.get("with") or tree.args.get("with_")
    if with_node is None:
        return tree
    cte_names = []
    for cte in with_node.expressions or []:
        cte_names.append(cte.alias or "<unnamed>")
    notes.append(ConversionNote(
        rule="top_level_cte",
        severity="flag",
        message=f"Top-level WITH/CTE detected ({len(cte_names)}: {', '.join(cte_names)}). "
                "CI editor requires CTEs to be inlined as subqueries in FROM. "
                "Rewrite manually: `WITH x AS (Q) SELECT ... FROM x` becomes "
                "`SELECT ... FROM (Q) AS x`.",
    ))
    return tree


def _flag_top_level_distinct(tree, notes):
    """Flag top-level DISTINCT. Mechanical rewrite (wrap in subquery + add
    GROUP BY) is sometimes ambiguous about which columns to group on, so
    flag-only.
    """
    if not isinstance(tree, exp.Select):
        return tree
    if tree.args.get("distinct"):
        notes.append(ConversionNote(
            rule="top_level_distinct",
            severity="flag",
            message="Top-level DISTINCT is rejected. Wrap the SELECT in a "
                    "subquery: `SELECT ... FROM (SELECT DISTINCT ...) AS sub "
                    "GROUP BY ...`.",
        ))
    return tree


def _flag_exists_subquery(tree, notes):
    """Flag EXISTS (...) subqueries. Mechanical rewrite to JOIN/IN requires
    correlation analysis — flag-only.
    """
    for ex in tree.find_all(exp.Exists):
        notes.append(ConversionNote(
            rule="exists_subquery",
            severity="flag",
            message="EXISTS (...) is unsupported. Rewrite as INNER JOIN "
                    "(when correlated) or `IN (SELECT col AS col FROM x)` "
                    "(when uncorrelated).",
            snippet=ex.sql(dialect=DIALECT)[:120],
        ))
    return tree


def _flatten_dpipe(node):
    """Flatten left-associative `a || b || c` chains (parsed as nested
    DPipe nodes) into a single argument list.
    """
    args = []
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, exp.DPipe):
            # Right then left so the final list is left-to-right.
            stack.append(n.expression)
            stack.append(n.this)
        else:
            args.append(n)
    return args


def _rewrite_dpipe_to_concat(tree, notes):
    """`a || b || c` → `CONCAT(a, b, c)`. The DPipe operator is rejected by
    the CI editor; CONCAT is the supported equivalent.
    """
    fired = False

    def transform(node):
        nonlocal fired
        if isinstance(node, exp.DPipe):
            # Skip nested DPipes — only rewrite at the top of a chain.
            parent = node.parent
            if isinstance(parent, exp.DPipe):
                return node
            args = _flatten_dpipe(node)
            fired = True
            return exp.Concat(expressions=[a.copy() for a in args])
        return node

    new_tree = tree.transform(transform)
    if fired:
        notes.append(ConversionNote(
            rule="concat_operator",
            severity="auto",
            message="Replaced `||` string-concat operator with `CONCAT(...)`.",
        ))
    return new_tree


# ── Conversion entry points ─────────────────────────────────────────────────


# Order matters: identifier hygiene first (so subsequent passes operate on
# clean names), then structural top-level fixes, then function swaps, then
# expression-level rewrites. Mirrors the 8-pass ordering in the
# data360-sql-convert skill.
_PASSES = (
    # Pass 2 — identifiers
    _strip_double_quoted_identifiers,
    _strip_dmo_table_aliases,
    # Pass 1 — structural
    _strip_top_level_order_by,
    _flag_top_level_cte,
    _flag_top_level_distinct,
    _flag_self_joins,
    _flag_dlo_in_from,
    _flag_exists_subquery,
    _alias_in_subquery,
    # Pass 3 — functions
    _rewrite_unsupported_functions,
    _rewrite_count_distinct,
    _rewrite_dpipe_to_concat,
    # Pass 4 — expressions
    _rewrite_avg_case,
    _rewrite_case_null_branches,
)


def convert_sql(sql: str) -> ConversionResult:
    """Convert a single SQL string. Returns the converted SQL plus a list
    of notes describing what was rewritten (auto) and what needs human
    review (flag).

    Empty / whitespace-only input is returned unchanged with no notes —
    keeps the CLI safe to call on placeholder files.
    """
    notes: list[ConversionNote] = []
    if not sql.strip():
        return ConversionResult(converted_sql=sql, notes=notes)

    try:
        trees = sqlglot.parse(sql, read=DIALECT)
    except Exception as e:
        notes.append(ConversionNote(
            rule="parse_error",
            severity="manual",
            message=f"sqlglot could not parse this file ({e}); converter "
                    "skipped. Fix the syntax error and re-run.",
        ))
        return ConversionResult(converted_sql=sql, notes=notes)

    rewritten = []
    for tree in trees:
        if tree is None:
            continue
        for transformer in _PASSES:
            tree = _apply(tree, transformer, notes)
        rewritten.append(tree.sql(dialect=DIALECT, pretty=True))

    converted = ";\n\n".join(rewritten)
    if sql.rstrip().endswith(";"):
        converted += ";"

    # Post-render: sqlglot's spark dialect re-emits exp.Anonymous("IFNULL")
    # as `COALESCE` and re-parses `IFNULL(...)` into `exp.Coalesce`. The
    # validator rejects COALESCE, so swap textually as the very last step.
    # Use a word boundary regex to avoid matching identifiers like
    # `MY_COALESCE_FN`. Only swap 2-arg COALESCE — leave 3+ alone (those
    # are unrewritable and would already have produced an IFNULL nest).
    converted = _post_render_coalesce_to_ifnull(converted)
    return ConversionResult(converted_sql=converted, notes=notes)


import re as _re

_RX_COALESCE_2ARG = _re.compile(
    r"\bCOALESCE\(\s*([^,()]+(?:\([^)]*\))?[^,()]*)\s*,\s*"
    r"([^,()]+(?:\([^)]*\))?[^,()]*)\s*\)",
    _re.IGNORECASE,
)


def _post_render_coalesce_to_ifnull(sql: str) -> str:
    """Convert 2-arg COALESCE(a, b) → IFNULL(a, b) in rendered output.

    This is a textual post-pass because sqlglot's spark dialect normalizes
    IFNULL ↔ COALESCE in both directions and there's no way to express
    "emit IFNULL" via a typed node. Multi-arg COALESCE(a, b, c) is rendered
    by `_rewrite_unsupported_functions` as a nested IFNULL chain via
    Anonymous nodes; this regex only matches the 2-arg form.

    The regex is conservative — it won't match if either argument contains
    a top-level comma or unbalanced parens. Anything it misses leaves the
    output recognizable to a downstream re-run of the audit.
    """
    prev = None
    out = sql
    while prev != out:
        prev = out
        out = _RX_COALESCE_2ARG.sub(r"IFNULL(\1, \2)", out)
    return out


def convert_file(path: Path) -> ConversionResult:
    """Convert a CI SQL file. Returns the result; caller decides where to
    write.
    """
    return convert_sql(path.read_text())


# ── Audit re-run on converted output (Step 5) ───────────────────────────────


# Audit findings the converter directly targets — when these still fire on
# converted SQL, that's a real "we couldn't fix this" signal worth surfacing.
# The full audit also reports correctness traps (leap-year, hardcoded
# RecordType IDs, etc.) that are out of the converter's scope, so we filter
# down to compliance-related categories only.
_REMAINING_AUDIT_KEYS = (
    "unsupported_functions",
    "count_distinct",
    "count_star",
    "try_convert_currency_arity",
    "dlo_in_from",
    "dmo_table_aliases",
    "self_joins",
    "top_level_distinct",
    "top_level_order_by",
    "top_level_cte",
    "exists_subquery",
    "in_subquery_unaliased",
    "alias_equals_field_name",
    "concat_operator",
    "double_quoted_identifiers",
    "datediff_in_case",
    "avg_case_nesting",
    "case_mixed_types",
    "ntile_alias_reuse",
    "concat_aggregate_provenance",
)


def _summarize_remaining(findings) -> list[str]:
    """Reduce a ci_audit findings dict to a flat list of human-readable
    "still broken" entries. Skips empty findings and out-of-scope checks.
    """
    if not findings or "parse_error" in findings:
        return []
    out = []
    for key in _REMAINING_AUDIT_KEYS:
        v = findings.get(key)
        if not v:
            continue
        if isinstance(v, list):
            count = len(v)
        elif isinstance(v, dict):
            count = sum(1 for x in v.values() if x)
        elif isinstance(v, int):
            count = v
        else:
            count = 1
        if count:
            out.append(f"{key} ({count})")
    return out


def _audit_converted(sql: str) -> list[str]:
    """Parse + audit a converted SQL string in-memory. Returns the
    compliance-relevant findings as flat strings; empty list means the
    converter handled everything the audit cares about.
    """
    if not sql.strip():
        return []
    try:
        trees = sqlglot.parse(sql, read=DIALECT)
    except Exception as e:
        return [f"parse_error_after_convert ({e})"]
    findings = _audit_file(trees, sql, ci_filter_index=None)
    return _summarize_remaining(findings)


# ── Notes report (Step 4) ───────────────────────────────────────────────────


def _format_severity_section(label: str, notes: list[ConversionNote]) -> list[str]:
    if not notes:
        return []
    lines = [f"**{label}** ({len(notes)})", ""]
    by_rule: dict[str, list[ConversionNote]] = {}
    for n in notes:
        by_rule.setdefault(n.rule, []).append(n)
    for rule in sorted(by_rule.keys()):
        bucket = by_rule[rule]
        lines.append(f"- `{rule}` × {len(bucket)} — {bucket[0].message}")
        # Surface up to 2 distinct snippets per rule for context.
        snippets = []
        for n in bucket:
            if n.snippet and n.snippet not in snippets:
                snippets.append(n.snippet)
            if len(snippets) >= 2:
                break
        for s in snippets:
            lines.append(f"  - `{s}`")
    lines.append("")
    return lines


def format_notes_report(results: dict[str, ConversionResult]) -> str:
    """Markdown report — one section per file with auto / flag / remaining
    sub-headings. Files with zero notes appear in a clean-list footer so the
    header counts stay honest.
    """
    total_auto = sum(r.auto_count() for r in results.values())
    total_flag = sum(r.flag_count() for r in results.values())
    total_remaining = sum(len(r.remaining_violations) for r in results.values())
    clean = sorted(name for name, r in results.items() if not r.notes and not r.remaining_violations)

    lines = [
        "# CI SQL Convert",
        "",
        f"_Converted {len(results)} file(s) — "
        f"{total_auto} auto-fix(es), {total_flag} manual flag(s), "
        f"{total_remaining} remaining violation(s) after conversion._",
        "",
        "Severities:",
        "- **auto** — converter rewrote the SQL; nothing further required.",
        "- **flag** / **manual** — converter could not auto-fix; review before paste.",
        "- **remaining** — the audit still flags this category on the converted output.",
        "",
    ]

    dirty = sorted(
        name for name, r in results.items()
        if r.notes or r.remaining_violations
    )
    for name in dirty:
        r = results[name]
        lines.append(f"## {name}")
        lines.append("")
        auto = [n for n in r.notes if n.severity == "auto"]
        flag = [n for n in r.notes if n.severity in ("flag", "manual")]
        lines += _format_severity_section("Auto-applied", auto)
        lines += _format_severity_section("Flagged for review", flag)
        if r.remaining_violations:
            lines.append(f"**Remaining audit violations** ({len(r.remaining_violations)})")
            lines.append("")
            for v in r.remaining_violations:
                lines.append(f"- `{v}`")
            lines.append("")

    if clean:
        lines.append("## Clean files")
        lines.append("")
        lines.append(f"_{len(clean)} file(s) needed no conversion and audit-clean post-convert:_")
        lines.append("")
        for name in clean:
            lines.append(f"- {name}")
        lines.append("")

    return "\n".join(lines)


# ── Driver / CLI (Step 4) ───────────────────────────────────────────────────


def _convert_path(path: Path, run_audit: bool = True) -> ConversionResult:
    """Read a file, convert, and (optionally) re-run the audit on the output.
    Centralized so both batch and single-file modes share the same pipeline.
    """
    result = convert_file(path)
    if run_audit:
        result.remaining_violations = _audit_converted(result.converted_sql)
    return result


def _unified_diff(orig: str, new: str, filename: str) -> str:
    import difflib
    lines = difflib.unified_diff(
        orig.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    )
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Convert Query Editor SQL to CI editor-compatible SQL.",
        epilog=(
            "Pass --output-dir <root> for batch mode (reads <root>/queries/*.sql, "
            "writes <root>/queries-converted/*.sql + <root>/reports/ci-convert.md). "
            "Or pass --input <file.sql> with --stdout / --diff / --in-place for "
            "one-off conversions."
        ),
    )
    parser.add_argument("--output-dir", help="Client Data360 root (batch mode).")
    parser.add_argument("--input", help="Single SQL file (single-file mode).")
    parser.add_argument("--stdout", action="store_true",
                        help="Write converted SQL to stdout (single-file mode).")
    parser.add_argument("--diff", action="store_true",
                        help="Print unified diff to stdout (single-file mode).")
    parser.add_argument("--in-place", action="store_true",
                        help="Overwrite the input file (single-file mode).")
    parser.add_argument("--backup", action="store_true",
                        help="Keep a .bak alongside --in-place rewrites.")
    parser.add_argument("--no-audit", action="store_true",
                        help="Skip the post-convert audit re-run.")
    args = parser.parse_args()

    if not args.output_dir and not args.input:
        parser.error("specify --output-dir (batch) or --input (single-file)")

    if args.input:
        in_path = Path(args.input).expanduser()
        if not in_path.is_file():
            print(f"ERROR: input file not found: {in_path}", file=sys.stderr)
            sys.exit(1)
        result = _convert_path(in_path, run_audit=not args.no_audit)

        # Default output mode for single-file: print converted SQL to stdout
        # and notes to stderr. --diff and --in-place override.
        if args.diff:
            sys.stdout.write(_unified_diff(in_path.read_text(),
                                           result.converted_sql, in_path.name))
        elif args.in_place:
            if args.backup:
                in_path.with_suffix(in_path.suffix + ".bak").write_text(in_path.read_text())
            in_path.write_text(result.converted_sql)
            print(f"Rewrote {in_path}", file=sys.stderr)
        else:
            # --stdout is the default single-file behavior — flag is accepted
            # for explicitness but not required.
            sys.stdout.write(result.converted_sql)

        if result.notes or result.remaining_violations:
            print("", file=sys.stderr)
            print(f"Notes: {result.auto_count()} auto, "
                  f"{result.flag_count()} flag, "
                  f"{len(result.remaining_violations)} remaining.",
                  file=sys.stderr)
            for n in result.notes:
                print(f"  [{n.severity}] {n.rule}: {n.message}", file=sys.stderr)
            for v in result.remaining_violations:
                print(f"  [remaining] {v}", file=sys.stderr)
        sys.exit(0)

    # Batch mode.
    root = Path(args.output_dir).expanduser()
    q_dir = root / "queries"
    out_dir = root / "queries-converted"
    report_path = root / "reports" / "ci-convert.md"

    if not q_dir.is_dir():
        print(f"ERROR: queries dir not found: {q_dir}", file=sys.stderr)
        sys.exit(1)

    sql_files = sorted(q_dir.glob("*.sql"))
    if not sql_files:
        print(f"No .sql files under {q_dir}", file=sys.stderr)
        sys.exit(0)

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    results: dict[str, ConversionResult] = {}
    for sql in sql_files:
        result = _convert_path(sql, run_audit=not args.no_audit)
        results[sql.name] = result
        (out_dir / sql.name).write_text(result.converted_sql)

    report_path.write_text(format_notes_report(results))

    total_auto = sum(r.auto_count() for r in results.values())
    total_flag = sum(r.flag_count() for r in results.values())
    total_remaining = sum(len(r.remaining_violations) for r in results.values())
    print(
        f"Converted {len(results)} file(s) → {out_dir}\n"
        f"Report: {report_path}\n"
        f"  auto-fixes: {total_auto}\n"
        f"  flagged:    {total_flag}\n"
        f"  remaining:  {total_remaining}"
    )


if __name__ == "__main__":
    main()
