---
name: data360-dpe-review
description: Review Data Processing Engine (DPE) configuration — field mappings, upsert keys, source CIs, target objects, and writeback sequencing. Use when user asks to "review DPE", "check data processing engine", "review data action", or "DPE mapping".
triggers:
  - "review DPE"
  - "check data processor"
  - "review data action"
  - "DPE mapping"
  - "data processor"
---

# Data360 DPE Review

Review Data Processing Engine configurations — the layer that syncs CI output to
target objects.

## Context

DPEs are the final writeback layer in many Data Cloud pipelines:

```
DMOs → CIs → DPE writebacks → target objects
```

DPE definitions are `BatchCalcJobDefinition` records available through the Tooling
API. Fetch the full body with `get_dpe`; `Metadata.writebacks[]` and
`Metadata.datasources[]` are the primary evidence for this review. DPEs are not Data
Transforms and are not returned by `/ssot/data-transforms`.

Use the Setup UI, screenshots, or approved documentation only when Tooling API access
genuinely fails. Never infer a field mapping from similar field names when the DPE body
is available.

## Procedure

1. Resolve the target environment and exact DPE `DeveloperName`.
2. Fetch the DPE body with `get_dpe`.
3. Read `Metadata.datasources[]` for source CI names and exposed fields.
4. Read `Metadata.writebacks[]` for sequence, source, target, operation, external ID,
   relationship metadata, and every source-to-target field mapping.
5. Fetch source CI metadata and SQL when reviewing output type, grain, key derivation,
   or upstream behavior. Do not infer those from the CI name.
6. Describe each target object with `sf sobject describe` and verify field existence,
   data type, requiredness, external-ID properties, formulas, and relationships.
7. Report confirmed state separately from assumptions or items that could not be
   verified.

## What to review

### Source CI mapping

| Check | What to look for |
|---|---|
| Source CI name | Read `Metadata.datasources[].sourceName`; verify the CI exists and is active. |
| Field mapping | Read each `writebacks[].fields[]` source-to-target pair. Are all required fields mapped? |
| Unmapped fields | CI produces fields the DPE doesn't map — intentional or oversight? |

### Upsert key

| Check | What to look for |
|---|---|
| External ID field | Read `writebacks[].externalIdFieldName`; verify it exists on the target and is an external ID. |
| Source mapping | Confirm which source field maps to the target external ID. |
| Uniqueness | Verify the source CI grain and key derivation support one value per intended target row. |
| Composite key derivation | If the source key is composite, inspect the CI SQL and null behavior rather than guessing from the field label. |

### Target object

| Check | What to look for |
|---|---|
| Object type | Read `writebacks[].targetObjectName`; do not infer from labels. |
| Required fields | Does the DPE populate all required fields on the target? |
| Field types | Do CI output types match target field types? (Text → Text, Number → Number) |
| Lookup relationships | Inspect `parentName`, `relationshipName`, target describe metadata, and the mapped parent external ID. |

### Execution

| Check | What to look for |
|---|---|
| Trigger | Scheduled? Flow-triggered? Manual? |
| Order of operations | Read `writebackSequence`; identify proven dependencies rather than assuming every later write requires an earlier row. |
| Parent/child order | Every child-target sequence must exceed every parent-target sequence when the child binds to the parent. |
| Error handling | Report only behavior confirmed from run history or platform documentation; do not assume row-level failure behavior. |

## Common patterns

**Parent/child writeback DPE:**
- Parent writebacks run before child writebacks that bind to them.
- The child relationship may map a parent external ID rather than a Salesforce record
  ID; confirm the exact relationship metadata in the DPE body.
- The ordering rule is cross-cutting: every parent-target sequence precedes every
  child-target sequence, not only its apparent pair.

**Separate upserts to one target:**
- Multiple writebacks can upsert different field families on the same target external
  ID.
- Do not assume later writes require a row created by the first write. Check whether
  the target relationship is required and whether each writeback can independently
  upsert.
- A missing earlier write can produce an incomplete row rather than a failed later
  writeback.

**Typical execution flow:**
1. Wait for the upstream CI to complete publishing
2. Cascade to dependent CIs if applicable
3. Run the DPE

## Access fallback

DPE definitions are not exposed by `/ssot/data-transforms`; that endpoint returns Data
Transforms. The DPE body is retrieved separately through the Tooling API.

If `get_dpe` fails because the environment or user cannot access the Tooling API:

- Record the API failure and the unverified scope.
- Review the definition in Setup or obtain a current screenshot/export from someone
  with access.
- Cross-check target fields and source CI output independently where possible.
- Mark mappings as unverified if the exact DPE configuration is unavailable. Never
  reconstruct them from naming similarity.

## Output

Write review findings to
`~/Projects/clients/<Client>/Data360/reports/dpe-review.md`. Include:

- Environment, DPE developer name, status, modified date, and verification date
- Source CI inventory
- Writeback sequence and dependency table
- Exact source-to-target field mapping table
- Target object and relationship validation
- Upsert-key and source-grain analysis
- Confirmed findings, unverified claims, and recommended actions
