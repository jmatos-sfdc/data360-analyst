SELECT
    Sales_Forecast_Period__dlm.BusinessUnit__c AS BusinessUnit__c,
    Sales_Forecast_Period__dlm.ForecastSetId__c AS ForecastSetId__c,
    Sales_Forecast_Period__dlm.StartDate__c AS StartDate__c,
    SUM(Sales_Forecast_Period__dlm.ActualUnits__c) AS ActualUnits__c
FROM Sales_Forecast_Period__dlm
INNER JOIN ssot__Account__dlm
    ON Sales_Forecast_Period__dlm.AccountId__c = ssot__Account__dlm.ssot__Id__c
GROUP BY
    Sales_Forecast_Period__dlm.BusinessUnit__c,
    Sales_Forecast_Period__dlm.ForecastSetId__c,
    Sales_Forecast_Period__dlm.StartDate__c
