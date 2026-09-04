SELECT
    ssot__ContactPointAddress__dlm.ssot__PostalCodeId__c AS ZipCode__c,
    COUNT(ssot__ContactPointAddress__dlm.ssot__Id__c) AS HouseholdCount__c
FROM ssot__ContactPointAddress__dlm
GROUP BY ssot__ContactPointAddress__dlm.ssot__PostalCodeId__c
