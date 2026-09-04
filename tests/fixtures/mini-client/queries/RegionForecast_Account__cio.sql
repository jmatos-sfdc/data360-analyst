SELECT
    Sales_Forecast_Period__dlm.BusinessUnit__c AS BusinessUnit__c,
    Sales_Forecast_Period__dlm.StartDate__c AS StartDate__c,
    Sales_Forecast_Period__dlm.EndDate__c AS EndDate__c,
    COUNT(Sales_Forecast_Period__dlm.AccountId__c) AS AccountCount__c
FROM Sales_Forecast_Period__dlm
GROUP BY
    Sales_Forecast_Period__dlm.BusinessUnit__c,
    Sales_Forecast_Period__dlm.StartDate__c,
    Sales_Forecast_Period__dlm.EndDate__c
