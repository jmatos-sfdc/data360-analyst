# Validation Checklist

## Structural

Run:

```bash
data360 provenance-validate <config.json|yaml>
```

Confirm no errors for IDs, references, roles, edge types, writeback properties,
safe HTML, evidence, outbound content, or data-scope guards.

## Factual

Compare every configured claim with the authority in `evidence-model.md`:

- DPE sources, sequences, operations, targets, keys, mappings, and relationships
- CI SQL inputs, joins, filters, formulas, and grain
- CI output fields and types
- DLO/DMO mappings and relationships
- Target field metadata and formulas
- UI labels/groups and automation behavior from approved sources
- Derived/off-DPE writer and lineage-boundary statements

Report findings as Blocker, Important, Cosmetic, or Unverifiable. Validation is
read-only unless fixes are separately requested.

## Reader Content

- No customer rows, names, IDs, or per-customer values
- No local paths, memory syntax, tool names, or AI/process references
- No temporary ownership or undated in-flight status
- Inferred claims are qualified
- Non-DPE nodes are not described as DPE writes
- Grouping/relationship edges are not described as traced data flow

## Rendering

Render only after structural validation passes:

```bash
data360 provenance-render \
  --config <config.json|yaml> \
  --output <report>/index.html \
  --normalized-config <report>/provenance.json
```

Run Python compilation, tests relevant to changed toolkit code, and JavaScript
syntax checks when the shell changes.

## Browser

Use `docs/plans/data360-provenance-report-manual-smoke.md` as the deterministic
manual checklist. Browser automation is additive. Never report an unrun visual
case as passed.

At minimum before delivery verify:

- Report and graph load without console errors
- Endpoint selection opens inspector and highlights the intended upstream path
- Group/filter changes do not restore stale selection
- Navigator search and keyboard selection work
- Inspector relationships follow edge traversal semantics
- Theme, minimap, pane collapse, and widths persist
- Zoom, fit, reset, pan, and minimap navigation work
- Help traps focus and Escape restores it
- CDN failure produces the fallback
- Supported desktop viewport has no document-level horizontal overflow

## Upgrade Comparison

Compare old and new:

- Group and endpoint counts
- Node and edge counts
- Layer labels and ordering
- Edge semantic classifications
- DPE sequence and mappings
- Reader-facing help and onboarding
- Interaction behavior

Keep the old artifact until the user accepts the generated replacement.
