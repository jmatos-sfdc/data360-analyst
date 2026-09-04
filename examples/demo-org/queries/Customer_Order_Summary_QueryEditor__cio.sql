SELECT
  COALESCE(a.Name__c, i.LastName__c, 'unknown') AS customer__c,
  COUNT(DISTINCT o.Id__c) AS order_count__c,
  EXTRACT(YEAR FROM o.OrderDate__c) AS order_year__c,
  SUM(o.Amount__c) AS total_amount__c
FROM Account__dlm AS a
LEFT JOIN ssot__Individual__dlm AS i
  ON a.Id__c = i.AccountId__c
JOIN SalesOrder__dlm AS o
  ON a.Id__c = o.AccountId__c
WHERE o.Status__c IN (SELECT Status__c FROM SalesOrder__dlm)
GROUP BY a.Id__c, EXTRACT(YEAR FROM o.OrderDate__c)
ORDER BY total_amount__c DESC
