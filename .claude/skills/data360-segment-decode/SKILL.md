---
name: data360-segment-decode
description: Decode and analyze Data Cloud segments — parse HTML-encoded criteria trees, summarize inclusion/exclusion logic, validate membership CSVs. Use when user asks to "decode a segment", "what does this segment do", "segment criteria", "validate segment membership", or "analyze segment".
triggers:
  - "decode a segment"
  - "what does this segment do"
  - "segment criteria"
  - "validate segment"
  - "analyze segment"
  - "segment membership"
---

# Data360 Segment Decode

Parse and analyze Data Cloud segment definitions. Segment criteria are stored as HTML-encoded JSON trees — this skill decodes them into human-readable logic.

## Where segment data lives

After intake:
```
~/Projects/clients/<Client>/Data360/
├── object-model/segments/<name>.yaml    — metadata + criteriaObjects (referenced DMO/CI names)
└── segments/<SegmentName>/              — per-segment working folder
    ├── <export>.csv                     — membership export (if pulled)
    ├── validate.py                      — validation script (if created)
    └── notes.md                         — findings
```

## Decoding criteria

### From YAML sidecar

Read `object-model/segments/<name>.yaml`. Intake persists only `criteriaObjects` — the sorted list of DMO/CI api-names referenced anywhere in the segment's include/exclude criteria. This is enough to resolve segment → upstream-object lineage offline, but **the sidecar does not carry the raw criteria tree** (operators, field names, values). To decode the full criteria logic, use the live MCP path below.

### From the MCP server (live)

```
list_segments → find the segment → read includeCriteria / excludeCriteria from the response
```

The `includeCriteria` and `excludeCriteria` fields are HTML-encoded JSON trees:

1. HTML-decode the criteria string (`html.unescape()`)
2. Parse the JSON
3. Walk the tree — each node is either:
   - A **logical operator** (`AND`, `OR`, `NOT`) with child nodes
   - A **filter condition** with `fieldName`, `operator`, `value`, and optional `dataModelObject`

### Output format

Present decoded criteria as nested bullet points:

```
Include (AND):
  • Individual.ssot__FirstName__c IS NOT NULL
  • ContactPointEmail.ssot__EmailAddress__c IS NOT NULL
  • (OR):
    • Individual.ssot__BirthDate__c >= 1990-01-01
    • Individual.CustomField__c = 'VIP'
Exclude:
  • Unsubscribes.Channel__c = 'Email'
```

## Membership validation

When the user has a membership CSV export:

1. Read the CSV — note row count, column headers
2. Cross-reference against the segment criteria:
   - Do all rows satisfy the inclusion logic?
   - Are any excluded rows present? (false positives)
   - Are expected rows missing? (false negatives)
3. For large CSVs, sample and spot-check rather than exhaustive validation
4. Write findings to `segments/<SegmentName>/notes.md`

## Common segment patterns

- **Journey entry segments** — typically `JRNY_*` prefix, filter on a specific event or date threshold
- **Marketing suppression** — exclude segments joined via `LEFT JOIN *_Unsubscribes__dlm`
- **Test segments** — `ZTEST_*`, `TEST_*` prefixes — flag for cleanup review
- **Activation-linked** — check `list_activations` to see if the segment is actively syncing to MCE or another target

## Activation context

When analyzing a segment, always check:
```
list_activations → filter by segmentId → show target, status, last publish date
```

A segment with no activation may be orphaned. A segment with a failed activation needs investigation.

## Output

Write findings to `~/Projects/clients/<Client>/Data360/segments/<SegmentName>/notes.md`. Include: decoded criteria, membership stats, validation results, activation status, and any concerns.
