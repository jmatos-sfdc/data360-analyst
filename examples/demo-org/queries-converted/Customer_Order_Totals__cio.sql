SELECT
  Account__dlm.Id__c AS account_id__c,
  FIRST(Account__dlm.Name__c) AS account_name__c,
  SUM(SalesOrder__dlm.Amount__c) AS total_amount__c,
  COUNT(SalesOrder__dlm.Id__c) AS order_count__c
FROM Account__dlm
LEFT JOIN SalesOrder__dlm
  ON Account__dlm.Id__c = SalesOrder__dlm.AccountId__c
LEFT JOIN ssot__Individual__dlm
  ON Account__dlm.Id__c = ssot__Individual__dlm.AccountId__c
LEFT JOIN Email_Unsubscribes__dlm
  ON ssot__Individual__dlm.Id__c = Email_Unsubscribes__dlm.IndividualId__c
WHERE
  Account__dlm.Status__c = 'Active'
  AND Email_Unsubscribes__dlm.IndividualId__c IS NULL
GROUP BY
  Account__dlm.Id__c