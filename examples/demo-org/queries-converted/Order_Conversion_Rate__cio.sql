SELECT
  Account__dlm.Id__c AS account_id__c,
  SUM(
    CASE
      WHEN SalesOrder__dlm.Status__c = 'Shipped'
      THEN SalesOrder__dlm.Amount__c
      ELSE 0
    END
  ) / NULLIF(COUNT(CASE WHEN SalesOrder__dlm.Status__c = 'Shipped' THEN 1 END), 0) AS avg_shipped_amount__c,
  COUNT(SalesOrder__dlm.Id__c) AS order_count__c
FROM Account__dlm
INNER JOIN SalesOrder__dlm
  ON Account__dlm.Id__c = SalesOrder__dlm.AccountId__c
  AND SalesOrder__dlm.Status__c = 'Shipped'
WHERE
  SalesOrder__dlm.Status__c = 'Shipped'
GROUP BY
  Account__dlm.Id__c