SELECT
  ssot__Individual__dlm.Id__c AS individual_id__c,
  FIRST(ssot__Individual__dlm.Email__c) AS email__c,
  FIRST(ssot__Individual__dlm.FirstName__c) AS first_name__c
FROM ssot__Individual__dlm
WHERE
  MONTH(ssot__Individual__dlm.BirthDate__c) = MONTH(CURRENT_DATE)
  AND DAY(ssot__Individual__dlm.BirthDate__c) = DAY(CURRENT_DATE)
GROUP BY
  ssot__Individual__dlm.Id__c