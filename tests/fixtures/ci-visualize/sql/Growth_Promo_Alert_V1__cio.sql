SELECT
    q.RecordAlertExternalId__c    AS RecordAlertExternalId__c,
    q.CommonOwnerCRMAccountId__c  AS CommonOwnerCRMAccountId__c,
    q.AlertType__c                AS AlertType__c
FROM (
    SELECT
        CONCAT(sel.CommonOwnerCRMAccountId__c,
               sel.AlertType__c,
               sel.CampaignReference__c,
               sel.ProgramReferenceNumber__c,
               sel.FiscalYear__c,
               sel.QuarterText__c) AS RecordAlertExternalId__c,
        sel.CommonOwnerCRMAccountId__c AS CommonOwnerCRMAccountId__c,
        sel.AlertType__c               AS AlertType__c
    FROM (
        SELECT
            ssot__Account__dlm.ssot__Id__c                 AS CommonOwnerCRMAccountId__c,
            cfg.AlertType__c                               AS AlertType__c,
            camp.CampaignReference__c                      AS CampaignReference__c,
            Loyalty_Program__dlm.ProgramReferenceNumber__c AS ProgramReferenceNumber__c,
            Incentive_Ledger__dlm.fiscal_yr__c             AS FiscalYear__c,
            CASE Incentive_Ledger__dlm.qtr_no__c
                 WHEN 1 THEN 'Q1'
                 WHEN 2 THEN 'Q2'
                 WHEN 3 THEN 'Q3'
                 WHEN 4 THEN 'Q4'
            END                                            AS QuarterText__c,
            Incentive_Ledger__dlm.tier_level__c            AS TierLevel__c
        FROM Incentive_Ledger__dlm
        INNER JOIN ssot__Account__dlm
            ON Incentive_Ledger__dlm.owner_cust_id__c = ssot__Account__dlm.OwnerGroupId__c
           AND ssot__Account__dlm.RecordTypeId__c = '012000000000001AAA'
        INNER JOIN Loyalty_Member__dlm
            ON Loyalty_Member__dlm.AccountId__c = ssot__Account__dlm.ssot__Id__c
        INNER JOIN Loyalty_Program__dlm
            ON Loyalty_Program__dlm.Id__c = Loyalty_Member__dlm.RebateProgramId__c
        INNER JOIN (
            SELECT Region_Assignment__dlm.Common_Owner__c AS Common_Owner__c,
                   Region_Assignment__dlm.BusinessUnit__c          AS BusinessUnit__c
            FROM Region_Assignment__dlm
        ) dpa
            ON dpa.Common_Owner__c = ssot__Account__dlm.OwnerGroupId__c
        INNER JOIN (
            SELECT Play_Config__dlm.Alert_Type_c__c AS AlertType__c,
                   'PROMO_CONFIG_KEY'               AS cfg_key
            FROM Play_Config__dlm
            WHERE Play_Config__dlm.Is_Active_c__c = true
        ) cfg
            ON cfg.cfg_key = 'PROMO_CONFIG_KEY'
        INNER JOIN (
            SELECT ssot__Campaign__dlm.ssot__Id__c AS CampaignReference__c,
                   'PROMO'                          AS join_key
            FROM ssot__Campaign__dlm
            INNER JOIN Product_Link__dlm
                ON ssot__Campaign__dlm.ssot__Id__c = Product_Link__dlm.Campaign_c__c
            INNER JOIN Play_Config__dlm
                ON ssot__Campaign__dlm.Sales_Play_Lookup__c = Play_Config__dlm.Id__c
        ) camp
            ON camp.join_key = 'PROMO'
        WHERE Incentive_Ledger__dlm.tier_level__c >= 1
    ) sel
) q
GROUP BY q.RecordAlertExternalId__c, q.CommonOwnerCRMAccountId__c, q.AlertType__c
