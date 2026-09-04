SELECT
  Account__dlm.Id__c AS account_id__c,
  FIRST(Account__dlm.Name__c) AS account_name__c
FROM Account__dlm
WHERE
  Account__dlm.CreatedDate__c = DATE_ADD(CURRENT_DATE, -1)
GROUP BY
  Account__dlm.Id__c