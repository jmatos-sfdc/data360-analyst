SELECT
  ssot__Individual__dlm.Id__c AS individual_id__c,
  IFNULL(ssot__Individual__dlm.FirstName__c, 'Customer') AS first_name__c,
  CONCAT(
    IFNULL(ssot__Individual__dlm.FirstName__c, 'Customer'),
    ' ',
    IFNULL(ssot__Individual__dlm.LastName__c, '')
  ) AS full_name__c,
  CASE
    WHEN IFNULL(ssot__Individual__dlm.FirstName__c, 'Customer') = 'Customer'
    THEN 'guest'
    ELSE 'named'
  END AS profile_type__c
FROM ssot__Individual__dlm
GROUP BY
  ssot__Individual__dlm.Id__c,
  IFNULL(ssot__Individual__dlm.FirstName__c, 'Customer')