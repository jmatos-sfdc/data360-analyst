# Evidence Model

## Claim Status

| Status | Meaning | Usage |
|---|---|---|
| `verified` | Confirmed from the authoritative live source in the target environment. | State directly. |
| `documented` | Supported by approved UI, design, or domain documentation but not live metadata. | State directly with evidence retained. |
| `inferred` | Reasoned from metadata but not explicit in an authority. | Qualify in reader content. |
| `unverified` | No current authoritative support. | Mark explicitly or omit from outbound output. |

## Authority Matrix

| Claim | Primary evidence | Not sufficient |
|---|---|---|
| DPE identity/status/modified date | DPE body | Report prose |
| DPE source CIs | `Metadata.datasources[]` | CI naming convention |
| Writeback sequence, operation, target, key | `Metadata.writebacks[]` | UI label |
| Field mapping | `writebacks[].fields[]` | Similar source/target names |
| Parent mapping | Writeback relationship metadata + target describe | Sequence alone |
| CI inputs, joins, filters, calculations | CI SQL | CI display name |
| CI output fields/types | CI metadata | Remembered SELECT list |
| CI grain | SQL grouping plus CI dimensions | `_Cat`/`_DPSect` suffix |
| DLO-to-DMO link | Mapping metadata | Similar names |
| DMO fields/relationships | DMO metadata/relationships | CI join alone |
| CRM type/formula/external ID | sObject describe | Report text |
| UI group/label | UI metadata/capture or approved design | DPE field name |
| Trigger/orchestration | Automation metadata or approved design | Lineage graph |
| Business meaning | Approved design/domain reference | API name expansion |
| Off-DPE writer | Writer metadata | Absence from selected DPE |

## Evidence Kinds

- `dpe_body`
- `ci_sql`
- `ci_metadata`
- `dlo_metadata`
- `dlo_dmo_mapping`
- `dmo_metadata`
- `dmo_relationships`
- `sobject_describe`
- `ui_capture`
- `automation_metadata`
- `approved_design`
- `approved_domain_reference`
- `platform_documentation`

## Edge Evidence

- `data_flow`: prove each boundary with its native authority.
- `grouping`: prove ownership or structural dependency; explain why it is not
  traversed.
- `derivation`: prove formula/external writer/UI derivation. Consumer-complete
  derivation edges require evidence.
- `relationship`: prove lookup/master-detail/model relationship. Relationship does
  not automatically mean traceable data flow.

## Validation Findings

- **Blocker:** wrong mapping, missing artifact/reference, unsupported claim,
  executable reader content, or customer-data exposure.
- **Important:** wrong grain, filter, sequence implication, relationship, formula,
  or stale state that changes meaning.
- **Cosmetic:** wording, ordering, or presentation without lineage impact.
- **Unverifiable:** no available authoritative source.

## Evidence Records

Keep evidence in the config registry and reference it by ID. Include artifact,
kind, environment when applicable, verification timestamp, stable metadata
locator, and claim status. Do not include tokens, secrets, local paths, customer
IDs, row values, or customer examples.
