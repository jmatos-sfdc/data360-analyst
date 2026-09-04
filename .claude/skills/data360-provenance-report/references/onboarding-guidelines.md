# Onboarding Guidelines

Reader-facing onboarding explains why each node exists without replacing the
technical description. Ground every statement before writing it.

## Depth By Role

| Role | Default depth | Content |
|---|---|---|
| Source / DLO / DMO | Short | What it stores and which branch consumes it. |
| Passthrough CI | Short | What it preserves and why the layer exists. |
| Transforming CI | Full | Inputs, joins, filters, calculations, grain, downstream use. |
| Aggregating CI | Full | Input/output grain, aggregation, downstream use. |
| DPE | Medium | Purpose, targets, sequence, key dependencies. |
| Writeback | Short | Sequence, source CI, target, upsert key, mapped field family. |
| Target object/field | Short | What receives the write and consumer meaning. |
| Derived field | Short | Formula/external writer and explicit DPE boundary. |
| Consumer endpoint | Medium | What the user sees and where it comes from. |

Use judgment; clarity matters more than fixed word counts.

## Full CI Shape

```html
<div class="anchor">One-sentence framing.</div>
<h4>Where the data comes from</h4>
<ul><li><code>Source__dlm</code> — source and relevant filters.</li></ul>
<h4>What this insight does</h4>
<p>Joins, filters, calculations, and non-obvious semantics.</p>
<h4>Grain and output</h4>
<p><strong>One row per:</strong> ...</p>
<h4>Where it goes next</h4>
<p>Downstream CI or writeback.</p>
<h4>Why it matters</h4>
<p>The concrete question this node helps answer.</p>
```

Short nodes normally use one or two paragraphs. Consumer endpoints use:

```html
<p><strong>What the user sees:</strong> ...</p>
<p><strong>Where it comes from:</strong> ...</p>
```

## Writing Rules

- Use `<code>` for API names and expressions.
- Lead full-tier content with `div.anchor`.
- Use `<h4>` inside node onboarding; the inspector title is already `<h3>`.
- Explain FULL/LEFT joins, fallback behavior, grain changes, and filters when they
  affect correctness.
- Distinguish structural/grouping dependencies from measure flow.
- Distinguish prior plan from prior actual, target lookup external ID from target
  field, and written fields from formulas or external writers.
- Do not identify an off-DPE writer merely because a field is absent from the DPE.
- Qualify inferred claims and omit unsupported narrative.
- Do not include customer examples, values, internal paths, tool names, temporary
  owners, or implementation-process commentary.

## Enrich Mode Guardrail

Preserve IDs, edges, groups, endpoints, and mappings. Enrichment changes
`onboardingHtml`, associated evidence references, and claim status only unless the
user separately requests lineage corrections.
