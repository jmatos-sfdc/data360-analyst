SELECT
  Account__dlm.Id__c AS account_id__c,
  FIRST(Account__dlm.Name__c) AS account_name__c,
  COUNT(SalesOrder__dlm.Id__c) AS order_count__c
FROM Account__dlm
LEFT JOIN SalesOrder__dlm
  ON Account__dlm.Id__c = SalesOrder__dlm.AccountId__c
WHERE Account__dlm.Status__c = 'Active'
  AND Account__dlm.RecordTypeId__c = '0123x000000ABCDAA2'
GROUP BY Account__dlm.Id__c
