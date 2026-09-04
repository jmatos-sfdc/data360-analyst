# Batch B golden fixtures — the oracle

These fixtures are the **independent oracle** for the Batch B scope-aware lineage refactor of
`ci_visualize.py`. They are authored by hand from reading the CI SQL, *before* the resolver
code exists, and they are **read-only for the implementer**. If the implementation cannot satisfy
a fixture, the implementer must stop and report it — never edit a fixture to make a test pass.
That separation (oracle author ≠ implementation author) is the whole point: it defeats the
"write the test to match the code" trap.

The SQL under `sql/` is **synthetic** — hand-written to exercise each structural feature (nested
scopes, literal-key subqueries, anti-joins, HAVING, external-ID CONCAT lineage, 3-leg FULL JOIN,
per-leg alias reuse). Nothing here is drawn from any client org, so the suite is self-contained
and ships no client-specific schema, names, or IDs. Each `sql/<CI>.sql` pairs with a
`<CI>.golden.json` of the same stem.

## Why structural facts, not full-JSON snapshots

Each `<CI>.golden.json` asserts the *specific facts Batch B must get right*, not a byte-exact
copy of the emitted model. A snapshot of a 3-leg FULL JOIN model (20 fields × 3 legs × 6 joins)
would be enormous, itself error-prone to hand-author, and — worse — would "pass" as long as the
output is *stable*, even if stably wrong. Targeted structural assertions plus the cross-fixture
invariants in `test_ci_visualize_batch_b.py` are a stronger, more honest oracle.

## Scopes are keyed by SQL-stable alias

Nested scopes reuse aliases, but within one CI each derived-table alias (`q`, `sel`, `dpa`,
`cfg`, `camp`, `wd`, `d`, `ad`, `co`, `sap`, `spc_threshold`, `spc_inner`) is unique and stable.
The outermost SELECT is keyed `__root__`. The golden asserts by alias; the test maps
`boundAlias -> scopeId` from `model["scopes"]`, so the implementation is free to choose any
internal scopeId scheme as long as it exposes `boundAlias`.

## Model-schema contract Batch B must expose

For these fixtures to be checkable, the Batch B model must add:

- `model["scopes"]`: `[ { "scopeId", "parentScopeId", "boundAlias", "kind": "root|derived",
  "baseSources": [<DMO/CI names joined directly in this scope>] } ]`
  - `parentScopeId` is `null` only for the `__root__` scope; every other scope's parent must
    resolve to a real scopeId.
- every `filter`: add `"scopeId"` (the scope whose WHERE the predicate lives in). The existing
  integer `"scope"` field may stay for back-compat but is not what the oracle checks.
- every `join`: add `"scopeId"` (owning scope), `"left"` and `"right"` relation identities
  (alias or DMO name of each side).
- `grain.externalId`: resolved token lineage —
  `{ "column", "status": "resolved|unresolved-*", "tokens": [ { "expr", "resolvedTo":
  [<base DMO field(s)>], "via": [<alias chain from outer scope down to the base>] } ] }`.
  Status must be `resolved` when every token terminates at a base-DMO field (even through
  multiple pass-through projections).

## Fixture inventory & coverage note

| Fixture | Shape exercised |
|---|---|
| `Growth_Promo_Alert_V1` | 3 nested scopes (`q`>`sel`), 3 literal-key derived subqueries (`dpa`/`cfg`/`camp`), simple-CASE, external-ID CONCAT resolving through two pass-through layers, projection-vs-WHERE span collision (`tier_level__c`) |
| `Prospect_Eligibility` | nested `spc_threshold`>`spc_inner` subquery, HAVING gate, LEFT-join anti-join (`ssot__Opportunity__dlm ... IS NULL`) |
| `Monthly_Sales_Summary` | 3-leg FULL JOIN over `Sales_Ledger__dlm`, per-leg `co`/`sap` sub-subqueries, `Region_Assignment__dlm IS NULL` anti-join in the `wd` leg |
| `Order_Totals` | 3-leg FULL JOIN, per-leg `co`/`sap` sub-subqueries, `HOUR_ADD`/`CDPMONTH` date logic in outer GROUP BY |

**Coverage gap (do not mistake this set for broad coverage):** the set skews to multi-leg
FULL JOIN CIs — `Monthly_Sales_Summary` and `Order_Totals` are the same shape. There is no
trivial single-scope fixture and no UNION (as opposed to FULL JOIN) fixture. The set covers
scope nesting, literal-key subqueries, anti-joins, HAVING, and external-ID lineage — the
structural variety Batch B must handle — but a green bar here is not proof the resolver is
correct on arbitrary CIs. A `--model-only` smoke pass across a real query corpus is the
intended follow-up before trusting it broadly.
