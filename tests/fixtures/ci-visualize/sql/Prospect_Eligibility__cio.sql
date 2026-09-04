SELECT
    Order_Totals__cio.CRMAccountId__c                    AS ProspectCRMAccountId__c,
    Order_Totals__cio.BusinessUnit__c                             AS BusinessUnit__c,
    SUM(Order_Totals__cio.NetShipmentQuantity__c)        AS Rolling12MoUnits__c,
    FIRST(spc_threshold.SalesPlayConfigKey__c)           AS SalesPlayConfigKey__c,
    FIRST(spc_threshold.SalesPlayName__c)                AS SalesPlayName__c
FROM Order_Totals__cio
INNER JOIN ssot__Account__dlm
    ON Order_Totals__cio.CRMAccountId__c = ssot__Account__dlm.ssot__Id__c
   AND ssot__Account__dlm.KQ_Id__c = 'CRM'
   AND ssot__Account__dlm.ssot__AccountTypeId__c = 'Associate Dealer'
LEFT OUTER JOIN ssot__Opportunity__dlm
    ON Order_Totals__cio.CRMAccountId__c = ssot__Opportunity__dlm.ssot__CustomerAccountId__c
   AND ssot__Opportunity__dlm.KQ_CustomerAccountId__c = 'CRM'
   AND ssot__Opportunity__dlm.Opportunity_Record_Type__c = '012000000000002AAA'
INNER JOIN (
    SELECT
        spc_inner.BusinessUnit_Prefixed__c                  AS BusinessUnit_Prefixed__c,
        FIRST(spc_inner.Threshold_Raw__c)          AS Threshold__c,
        FIRST(spc_inner.SalesPlayConfigKey_Raw__c) AS SalesPlayConfigKey__c,
        FIRST(spc_inner.SalesPlayName_Raw__c)      AS SalesPlayName__c
    FROM (
        SELECT
            CASE Play_Config__dlm.PBU_c__c
                 WHEN 'Consumer'   THEN '01 - Consumer'
                 WHEN 'Commercial' THEN '03 - Commercial'
            END AS BusinessUnit_Prefixed__c,
            Play_Config__dlm.Past_12_Months_Units_Threshold__c AS Threshold_Raw__c,
            Play_Config__dlm.Config_Key_c__c                   AS SalesPlayConfigKey_Raw__c,
            Play_Config__dlm.Name__c                           AS SalesPlayName_Raw__c
        FROM Play_Config__dlm
        WHERE Play_Config__dlm.Alert_Type_c__c = 'Associate Dealer Prospecting'
          AND Play_Config__dlm.Is_Active_c__c = true
    ) spc_inner
    GROUP BY spc_inner.BusinessUnit_Prefixed__c
) spc_threshold
    ON spc_threshold.BusinessUnit_Prefixed__c = Order_Totals__cio.BusinessUnit__c
WHERE ssot__Opportunity__dlm.ssot__CustomerAccountId__c IS NULL
  AND Order_Totals__cio.TxMonth__c >= DATE_ADD(CURRENT_DATE(), -365)
  AND Order_Totals__cio.TxMonth__c <= CURRENT_DATE()
GROUP BY
    Order_Totals__cio.CRMAccountId__c,
    Order_Totals__cio.BusinessUnit__c
HAVING SUM(Order_Totals__cio.NetShipmentQuantity__c) > FIRST(spc_threshold.Threshold__c)
