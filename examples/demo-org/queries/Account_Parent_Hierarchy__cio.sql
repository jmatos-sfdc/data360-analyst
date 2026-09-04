SELECT
  child.Id__c AS account_id__c,
  FIRST(child.Name__c) AS account_name__c,
  FIRST(parent.Name__c) AS parent_name__c
FROM Account__dlm AS child
LEFT JOIN Account__dlm AS parent
  ON child.ParentAccountId__c = parent.Id__c
GROUP BY child.Id__c
