SELECT
    Sales_Forecast_Period__dlm.BusinessUnit__c AS BusinessUnit__c,
    Sales_Forecast_Period__dlm.ForecastSetId__c AS ForecastSetId__c,
    SUM(Sales_Forecast_Period__dlm.Throughput__c) AS TotalThroughput__c
FROM Sales_Forecast_Period__dlm
INNER JOIN ssot__ProductCatalog__dlm
    ON Sales_Forecast_Period__dlm.ProductId__c = ssot__ProductCatalog__dlm.ssot__Id__c
GROUP BY
    Sales_Forecast_Period__dlm.BusinessUnit__c,
    Sales_Forecast_Period__dlm.ForecastSetId__c
