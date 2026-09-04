---
name: data360-provenance-report
description: Build, validate, enrich, or upgrade an interactive provenance report for one Salesforce Data 360 Data Processing Engine. Use when the user asks to create a DPE provenance report, trace DPE writebacks into target fields or consumers, validate an existing provenance report, add onboarding explanations, or migrate an older hybrid graph report to the toolkit shell.
triggers:
  - "build provenance report"
  - "create DPE provenance report"
  - "trace DPE writebacks"
  - "validate provenance report"
  - "add onboarding to provenance report"
  - "upgrade provenance report"
---

# Data360 Provenance Report

Generate a metadata-only, client-shippable hybrid report for one Data Processing
Engine (DPE). The left pane contains reader-defined trace endpoints; the graph
shows upstream sources, Calculated Insights, writebacks, target objects/fields,
and optional derived or consumer endpoints.

The report configuration is the source of truth. The renderer validates the
configuration and produces the HTML artifact from the packaged shell.

## Modes

Choose the mode from the request. Ask one short clarifying question when the mode,
DPE, environment, client folder, or output path is ambiguous.

| Mode | Use for |
|---|---|
| `build` | Create a new report from a live DPE and its upstream/downstream metadata. |
| `validate` | Compare an existing report/config with current live metadata; report findings without editing. |
| `enrich` | Add or refresh onboarding content while preserving graph structure and mappings. |
| `upgrade` | Extract an older compatible hybrid report into config and render it with the current shell. |

## Required Inputs

- Client `Data360/` folder
- Authenticated org alias and reader-facing environment label
- DPE `DeveloperName`
- Output path under `<client>/Data360/reports/`
- Intended audience
- Lineage boundary:
  - `strict_writeback`: selected DPE mappings only
  - `consumer_complete`: independently evidenced derived/off-DPE/consumer lineage

For `validate`, accept the existing HTML path, normalized config path, or both.
For `upgrade`, confirm the source uses the compatible endpoint-list + vis-network
graph + inspector family.

## Read Before Working

- `references/evidence-model.md`
- `references/report-schema.md`
- `references/onboarding-guidelines.md` for `build` and `enrich`
- `references/validation-checklist.md`

## Containment And Data Scope

- Keep all client artifacts under that client's `Data360/` folder.
- Do not add account names, customer IDs, transaction rows, per-customer values,
  examples from customer-keyed CIs, or a value-preview column.
- Use synthetic data only in toolkit tests and examples.
- If client policy is stricter, client policy wins.
- Do not overwrite an accepted report during `build` or `upgrade`; write to a new
  path or keep the prior artifact until acceptance.

## Evidence Discipline

Use the source closest to each claim:

- DPE body: source CIs, writeback sequence, target objects, operation, upsert key,
  relationship metadata, and exact source-to-target mappings
- CI SQL: real inputs, joins, filters, formulas, and grain transformations
- CI metadata: output dimensions, measures, and types
- DLO/DMO mapping and metadata: ingestion/model lineage and relationships
- `sf sobject describe`: target fields, types, formulas, external IDs, requiredness,
  and relationships
- UI capture/metadata or approved design: user-visible grouping and labels
- Automation metadata or approved design: triggers and orchestration
- Approved domain reference: business meaning

Never infer topology from `_Cat`, `_DPSect`, `PY`, or similar names. Never infer a
field mapping from naming similarity. Mark unsupported context as `inferred` or
`unverified`; do not present it as live-verified state.

## Build Mode

1. Resolve client, environment, data space, DPE, audience, output, and lineage
   boundary.
2. Fetch the DPE body with `get_dpe`. Inventory `Metadata.datasources[]` and
   `Metadata.writebacks[]`.
3. Fetch every source CI's SQL and metadata. Walk real SQL references upstream;
   do not synthesize a standard layer sequence.
4. Resolve DLO-to-DMO mappings and DMO relationships when they are part of the
   chain.
5. Describe every target object and mapped field with `sf sobject describe`.
6. Request authoritative UI/design evidence for endpoint groups, visible labels,
   trigger behavior, or business meaning that metadata does not establish.
7. Propose reader groups and trace endpoints. Obtain confirmation before encoding
   opinionated UI grouping without existing evidence.
   Set `endpointHeader` from the endpoint node roles: consumer/UI measures use
   `Measure`; CRM object/field endpoints use `CRM Field`; other target fields use
   `Target Field`. Keep `endpointInstruction` generic and sentence-style:
   `click an item to trace`.
8. Author a JSON or YAML config using `references/report-schema.md`. Add evidence
   registry entries and claim statuses.
9. Add role-appropriate onboarding content using
   `references/onboarding-guidelines.md`.
10. Validate:

```bash
data360 provenance-validate <config.json|yaml>
```

11. Render HTML and normalized JSON:

```bash
data360 provenance-render \
  --config <config.json|yaml> \
  --output <client>/Data360/reports/<slug>-provenance/index.html \
  --normalized-config <client>/Data360/reports/<slug>-provenance/provenance.json
```

12. Run an independent read-only factual validation using
    `references/validation-checklist.md`. If the host supports a separate agent,
    use it; otherwise perform the same grounded comparison in the main loop. The
    validator reports findings and does not edit.
13. Fix findings, rerun structural validation, and render again.
14. Run the manual smoke checklist referenced by the validation guide. Attempt
    browser automation when available, but report any unrun visual case honestly.
15. Report artifact paths, evidence scope, warnings, unverifiable claims, and test
    results.

## Validate Mode

1. Locate the normalized config. If only HTML exists, extract the JSON from the
   `provenance-config` script element without executing the page.
2. Run `validate_provenance_config.py`.
3. Refresh the live DPE, CI SQL/metadata, mappings, DMO metadata, target describes,
   and approved non-metadata sources referenced by the config.
4. Compare graph nodes, edge semantics, endpoint links, sequence, mappings, keys,
   fields, formulas, filters, grain claims, labels, and onboarding statements.
5. Report findings first, ordered as Blocker, Important, Cosmetic, Unverifiable.
6. Do not edit the report unless the user separately requests fixes.

## Enrich Mode

1. Validate the existing config before editing.
2. Preserve node IDs, edges, groups, endpoints, mappings, and lineage boundary.
3. Inventory nodes by semantic role rather than display-layer name.
4. Refresh authoritative evidence for every node being described.
5. Add or update `onboardingHtml` according to
   `references/onboarding-guidelines.md`.
6. Update evidence references and claim statuses when content changes.
7. Validate, render, and inspect representative short/full/writeback/consumer
   nodes in a browser.
8. Run an independent factual validation pass before delivery.

## Upgrade Mode

1. Preserve the original report until acceptance.
2. Confirm compatibility: endpoint list, vis-network graph, and node inspector.
3. Extract report-level labels, groups, endpoints, layers, nodes, edges, help,
   colors, layout, onboarding, and evidence into a versioned config.
4. Replace styling-derived edge behavior with explicit `data_flow`, `grouping`,
   `derivation`, or `relationship` types.
5. Validate and render with the packaged shell.
6. Compare endpoint/node/edge counts and reader-facing content with the original.
7. Run browser checks and factual validation. Do not replace the original until
   accepted.

## Output Contract

Each generated report directory should normally contain:

```text
<slug>-provenance/
├── index.html
└── provenance.json
```

Keep the authored YAML/JSON source as well when it differs from normalized JSON.
The config, not hand-edited generated HTML, is the maintained source of truth.

## Completion Criteria

- Structural validator returns no errors.
- Live evidence supports every `verified` claim.
- Derived/off-DPE content respects the configured lineage boundary.
- Reader-facing content contains no local paths, memory syntax, tool names, or
  temporary ownership claims.
- Report renders without console errors.
- Manual smoke results state exactly what was and was not run.
- Existing accepted artifacts were not overwritten without approval.
