---
name: data360-compare
description: Compare Data Cloud artifacts — diff CIs side-by-side, compare DMO schemas across orgs (dev vs UAT), or compare CI SQL across similar CIs. Use when user asks to "compare CIs", "diff these CIs", "compare dev and UAT", "compare orgs", "compare DMOs", or "diff DMOs across orgs".
triggers:
  - "compare CIs"
  - "compare two CIs"
  - "diff these CIs"
  - "compare dev and UAT"
  - "compare orgs"
  - "data 360 org comparison"
  - "compare DMOs"
  - "diff DMOs across orgs"
  - "what's different between these CIs"
---

# Data360 Compare

Compare Data Cloud artifacts — CIs against each other, DMO schemas across orgs, or any two artifacts side-by-side.

## CI-to-CI Comparison

Compare two or more CIs that appear to do similar things (e.g., same naming prefix, same source DMOs).

### What to compare

| Aspect | What to look for |
|---|---|
| **FROM / JOIN chain** | Same tables? Same join order? Missing tables in one? |
| **JOIN conditions** | Same keys? Additional conditions on one but not the other? |
| **WHERE filters** | Same filters? Different date ranges? Different literal values? |
| **GROUP BY** | Same grain? One more granular than the other? |
| **SELECT / measures** | Same output columns? Different aggregations? |
| **Hard-coded values** | RecordTypeIds, status strings, date offsets |

### Workflow

1. Pull SQL for both CIs (from `queries/*.sql` on disk or via MCP `list_cis`)
2. Lay them side-by-side
3. Walk through FROM → JOIN → WHERE → GROUP BY → SELECT
4. For each section, note: identical, similar (minor diff), or divergent
5. Report findings as a table

### Output format

```markdown
## CI Comparison: <CI_A> vs <CI_B>

| Aspect | <CI_A> | <CI_B> | Verdict |
|---|---|---|---|
| Source tables | Account, Order | Account, Order, Product | CI_B adds Product |
| Account filter | `Type = 'Customer'` | `Type = 'Customer' AND Status = 'Active'` | CI_B is stricter |
| Date range | Last 365 days | Last 180 days | Different lookback |
| Grain | Campaign × Account | Campaign × Account × Product | CI_B is finer |
```

## Org-to-Org Comparison (dev vs UAT)

Compare the same object type across two orgs to find schema differences.

### DMO schema comparison

1. Get DMO metadata from both orgs:
   - Dev: `sf sobject describe --sobject <dmo> --target-org <dev-alias> --json`
   - UAT: `sf sobject describe --sobject <dmo> --target-org <uat-alias> --json`
2. Compare field lists — look for:
   - Fields in dev but not UAT (not yet deployed)
   - Fields in UAT but not dev (deployed ahead, or UAT-specific config)
   - Same field, different type or label

### CI inventory comparison

1. Run intake on both orgs (or use MCP `list_cis` on each)
2. Compare CI lists:
   - CIs in dev but not UAT
   - CIs in UAT but not dev
   - Same CI name, different SQL (check `expression` field)

### Output format

```markdown
## Org Comparison: <dev> vs <UAT>

### DMO: <dmo_name>
| Field | Dev | UAT | Status |
|---|---|---|---|
| New_Field__c | Present (Text) | Missing | Not deployed |
| Old_Field__c | Missing | Present (Number) | UAT-only |

### CI Inventory
| Status | Count | Examples |
|---|---|---|
| Both orgs | 45 | Customer_Lifetime_Value, Monthly_Revenue_* |
| Dev only | 3 | Campaign_ROI, Churn_Risk_v2, Test_Promo |
| UAT only | 1 | Legacy_Margin_Calc |
```

## Cross-CI Pattern Analysis

For a set of CIs sharing a naming prefix (e.g., `Monthly_Revenue_*`, `Churn_Risk_*`):

1. Pull all SQL files matching the prefix
2. Extract the FROM/JOIN chain from each
3. Build a matrix: which CIs join which tables
4. Flag inconsistencies:
   - One CI joins a table the others skip
   - Different join keys for the same table pair
   - Different WHERE filters for the same column

This is a deeper version of what `cluster_cis_by_dmo.py` does, but driven by the AI reading the actual SQL rather than the script's heuristics.

## Output

Write comparison results to `~/Projects/clients/<Client>/Data360/reports/`. Use descriptive filenames: `ci-comparison-clv-v1-vs-v2.md`, `org-comparison-dev-uat.md`, etc.
