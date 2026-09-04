# Report Configuration Schema

The normalized runtime format is JSON schema version `1.0`. YAML is accepted as
authoring input and normalized by the renderer.

## Root

Required:

- `schemaVersion`
- `report`
- `layers[]`
- `groups[]`
- `endpoints[]`
- `nodes[]`
- `edges[]`

Optional:

- `filters[]`
- `help`
- `sources[]`

## Report

Required report fields:

- `id`: stable kebab-case ID and default storage namespace
- `title`
- `environment`: reader-facing label, not org alias
- `verifiedDate`: `YYYY-MM-DD`
- `groupSelectorLabel`
- `leftPaneTitle`
- `endpointHeader`
- `graphDescription`
- `lineageBoundary`: `strict_writeback` or `consumer_complete`
- `initialGroupId`

Optional: `documentTitle`, `subtitle`, `endpointInstruction`, `graphTitle`,
`graphSubtitle`, `filterLabel`, `initialFilterId`, `storageNamespace`, `outbound`,
and safe layout fields.

## Roles

- `source`
- `dlo`
- `dmo`
- `ci`
- `dpe`
- `writeback`
- `target_object`
- `target_field`
- `derived_field`
- `consumer`

Roles control semantics and defaults. `layerId` controls only display grouping.

## Groups And Endpoints

A group has `id`, `label`, and non-empty `endpointIds`. An endpoint has `id`,
`groupId`, `nodeId`, and `label`. Optional endpoint fields include
`technicalLabel`, `summaryHtml`, `noteHtml`, `order`, and `evidenceIds`.

Version 1 endpoints have one primary group. Multiple endpoints in different groups
may link to the same node.

## Nodes

Required node fields:

- `id`
- `label`
- `role`
- `layerId`
- non-empty `groups`
- `description`

Optional: `onboardingHtml`, machine-readable `properties`, ordered
`displayProperties`, `evidenceIds`, `claimStatus`, `level`, and limited `style`
overrides. `level` is a non-negative integer used for hierarchical layout; when
absent, the shell falls back to the display layer's `order`.

`displayProperties` contains reader-facing rows:

```json
[{"label":"Source CI","value":"Example__cio"},
 {"label":"Fields","value":"A → B\nC → D","multiline":true}]
```

Keep full validation metadata in `properties`; use `displayProperties` to curate
what appears in the inspector. Values must be display-ready scalars. Empty rows are
omitted.

Writeback nodes require these properties:

- `sequence`
- `sourceName`
- `targetObject`
- `operation`
- `upsertKey`

## Edge Types

| Type | Traversal |
|---|---|
| `data_flow` | Always traversed. |
| `grouping` | Never traversed. |
| `relationship` | Never traversed. |
| `derivation` | Traversed only under `consumer_complete`. |

Each edge requires `id`, `from`, `to`, `type`, and `groups`. Optional fields are
`label`, `explanationHtml`, `evidenceIds`, `filterTags`, and limited styling.

Do not add a `traceable` override. Traversal derives from type and lineage boundary.

## Layers And Filters

Layers require `id`, `label`, six-digit hex `color`, and integer `order`.
Display-layer names are report-specific and do not change node semantics.

Filters require `id`, `label`, and non-empty `tags`. Nodes, edges, and groups may
reference tags through `filterTags`. The shell provides the implicit All option.

## Safe HTML

Only fields named `*Html` and `help.html` accept trusted reader markup. The
validator allowlist permits paragraphs, emphasis, code, lists, headings, tables,
line breaks, and `div.anchor`. It rejects scripts, styles, embeds, form controls,
links/URI attributes, event handlers, inline styles, comments, declarations,
unknown classes, malformed nesting, and closing script sequences.

## Evidence

The optional `sources[]` registry uses:

- `id`
- `kind`
- `artifact`
- `verifiedAt`
- `status`

Optional: `environment`, `locator`, and internal non-rendered `notes`.

Nodes, edges, endpoints, and help reference evidence through `evidenceIds`.

## Commands

```bash
data360 provenance-validate <config.json|yaml>
```

```bash
data360 provenance-render \
  --config <config.json|yaml> \
  --output <report>/index.html \
  --normalized-config <report>/provenance.json
```

The renderer refuses invalid config before creating or overwriting output.
