---
name: data360-dpe-review
description: Review Data Processor Engine (DPE) / Data Action configuration — field mappings, upsert keys, source CIs, target objects. Use when user asks to "review DPE", "check data processor", "review data action", or "DPE mapping".
triggers:
  - "review DPE"
  - "check data processor"
  - "review data action"
  - "DPE mapping"
  - "data processor"
---

# Data360 DPE Review

Review Data Processor Engine configurations — the layer that syncs CI output to CRM objects.

## Context

DPEs (Data Processor Engine / Data Actions) are the final layer in the Data Cloud pipeline:

```
DMOs → CIs → DPE → CRM Objects (Record Alert, Record Alert Item, etc.)
```

DPEs are configured in Setup (not via the transforms API) and may not be accessible via REST endpoints. If API access is unavailable, review must be done through the Setup UI or by reading the canvas/documentation that describes the configuration.

## What to review

### Source CI mapping

| Check | What to look for |
|---|---|
| Source CI name | Which CI feeds this DPE? Verify it exists and is ACTIVE. |
| Field mapping | Each CI output field → CRM object field. Are all required fields mapped? |
| Unmapped fields | CI produces fields the DPE doesn't map — intentional or oversight? |

### Upsert key

| Check | What to look for |
|---|---|
| External ID field | Which field does the DPE use for upsert? (e.g., `RecordAlertExternalId`) |
| Uniqueness | Is the External ID guaranteed unique per grain? Duplicate values cause silent overwrites. |
| Composite key derivation | If the External ID is a concat (e.g., `Account~Campaign`), verify all components are non-null. |

### Target object

| Check | What to look for |
|---|---|
| Object type | Record Alert, Record Alert Item, custom object, etc. |
| Required fields | Does the DPE populate all required fields on the target? |
| Field types | Do CI output types match target field types? (Text → Text, Number → Number) |
| Lookup relationships | If the target has lookup fields (e.g., Record Alert Item → Record Alert), how is the relationship established? |

### Execution

| Check | What to look for |
|---|---|
| Trigger | Scheduled? Flow-triggered? Manual? |
| Order of operations | Does the Alert DPE run before the Item DPE? (Items may need the Alert record to exist first for lookup) |
| Error handling | What happens on upsert failure? (Usually: row is skipped, logged in Data Cloud error log) |

## Common patterns

**Record Alert DPE:**
- Upsert to `Record Alert` from an Alert-level CI (e.g., rollup by account/owner)
- Upsert to `Record Alert Item` from an Item-level CI (e.g., per-product/per-location detail)
- Multiple source CIs may feed the same target object via separate DPEs

**Typical execution flow:**
1. Wait for the upstream CI to complete publishing
2. Cascade to dependent CIs if applicable
3. Run the DPE

## Access limitations

DPE configurations are in **Setup → Data Cloud → Data Actions** (or Data Processor Engine). They are NOT exposed via the `/ssot/data-transforms` API (which only returns Data Transforms, not Data Actions).

If the user's profile lacks Setup access (e.g., not System Administrator), DPE review requires:
- Reading the canvas/documentation that describes the DPE
- Asking someone with access to screenshot or export the configuration
- Reviewing the CI output shape and inferring the mapping from field names

## Output

Write review findings to `~/Projects/clients/<Client>/Data360/reports/dpe-review.md`. Include: source CIs, target objects, field mapping table, upsert key analysis, execution order, and any concerns.
