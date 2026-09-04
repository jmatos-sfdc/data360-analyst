SELECT
  Account__dlm.Id__c AS account_id__c,
  FIRST(Account__dlm.Name__c) AS account_name__c,
  FIRST(Account__dlm.Name__c) AS parent_name__c
FROM Account__dlm
LEFT JOIN Account__dlm
  ON Account__dlm.ParentAccountId__c = Account__dlm.Id__c
GROUP BY
  Account__dlm.Id__c