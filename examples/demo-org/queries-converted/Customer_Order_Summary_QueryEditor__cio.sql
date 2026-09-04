SELECT
  IFNULL(Account__dlm.Name__c, IFNULL(ssot__Individual__dlm.LastName__c, 'unknown')) AS customer__c,
  APPROX_COUNT_DISTINCT(SalesOrder__dlm.Id__c) AS order_count__c,
  YEAR(SalesOrder__dlm.OrderDate__c) AS order_year__c,
  SUM(SalesOrder__dlm.Amount__c) AS total_amount__c
FROM Account__dlm
LEFT JOIN ssot__Individual__dlm
  ON Account__dlm.Id__c = ssot__Individual__dlm.AccountId__c
JOIN SalesOrder__dlm
  ON Account__dlm.Id__c = SalesOrder__dlm.AccountId__c
WHERE
  SalesOrder__dlm.Status__c IN (
    SELECT
      Status__c AS Status__c_alias
    FROM SalesOrder__dlm
  )
GROUP BY
  Account__dlm.Id__c,
  YEAR(SalesOrder__dlm.OrderDate__c)