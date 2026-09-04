SELECT IFNULL(IFNULL(wd.CommonOwnerCRMAccountId__c, d.CommonOwnerCRMAccountId__c), ad.CommonOwnerCRMAccountId__c) AS CommonOwnerCRMAccountId__c,
       IFNULL(IFNULL(wd.CRMAccountId__c, d.CRMAccountId__c), ad.CRMAccountId__c) AS CRMAccountId__c,
       IFNULL(IFNULL(wd.TxMonth__c, d.TxMonth__c), ad.TxMonth__c) AS TxMonth__c,
       IFNULL(IFNULL(wd.MaterialId__c, d.MaterialId__c), ad.MaterialId__c) AS MaterialId__c,
       FIRST(IFNULL(IFNULL(wd.ProductId__c, d.ProductId__c), ad.ProductId__c)) AS ProductId__c,
       FIRST(IFNULL(IFNULL(wd.CommonOwnerName__c, d.CommonOwnerName__c), ad.CommonOwnerName__c)) AS CommonOwnerName__c,
       SUM(IFNULL(wd.NetShipmentQuantity__c, 0) + IFNULL(d.NetShipmentQuantity__c, 0) + IFNULL(ad.NetShipmentQuantity__c, 0)) AS NetShipmentQuantity__c
FROM (
SELECT Account_Hierarchy__cio.COAccountId__c   AS CommonOwnerCRMAccountId__c,
       Account_Hierarchy__cio.AccountId__c     AS CRMAccountId__c,
       Sales_Ledger__dlm.matl_id__c            AS MaterialId__c,
       ssot__Product__dlm.ssot__Id__c          AS ProductId__c,
       co.AccountName__c                       AS CommonOwnerName__c,
       sap.Channel__c                          AS ChannelCode__c,
       DATE_TRUNC('MONTH', Sales_Ledger__dlm.sls_trans_dt__c) AS TxMonth__c,
       SUM(Sales_Ledger__dlm.net_ship_qty__c)  AS NetShipmentQuantity__c
FROM Sales_Ledger__dlm
INNER JOIN ssot__GoodsProduct__dlm
    ON Sales_Ledger__dlm.matl_id__c = ssot__GoodsProduct__dlm.ssot__Id__c
LEFT OUTER JOIN ssot__Product__dlm
    ON ssot__GoodsProduct__dlm.ProductCode__c = ssot__Product__dlm.ssot__ProductCode__c
INNER JOIN Account_Hierarchy__cio
    ON Sales_Ledger__dlm.cust_id__c = Account_Hierarchy__cio.AccountNumber__c
INNER JOIN ssot__Account__dlm
    ON Account_Hierarchy__cio.AccountId__c = ssot__Account__dlm.ssot__Id__c
   AND ssot__Account__dlm.KQ_Id__c = 'CRM'
   AND ssot__Account__dlm.RecordTypeId__c = '012000000000001AAA'
LEFT OUTER JOIN Region_Assignment__dlm
    ON Account_Hierarchy__cio.COAccountNumber__c = Region_Assignment__dlm.Common_Owner__c
   AND ssot__GoodsProduct__dlm.BusinessUnit_Number__c = Region_Assignment__dlm.BusinessUnit__c
LEFT OUTER JOIN (
    SELECT ssot__Account__dlm.ssot__Id__c   AS AccountId__c,
           ssot__Account__dlm.ssot__Name__c AS AccountName__c
    FROM ssot__Account__dlm
    WHERE ssot__Account__dlm.KQ_Id__c = 'CRM'
) co ON Account_Hierarchy__cio.COAccountId__c = co.AccountId__c
LEFT OUTER JOIN (
    SELECT ssot__Account__dlm.ssot__Id__c     AS ShipToId__c,
           ssot__Account__dlm.Channel_Code__c AS Channel__c
    FROM ssot__Account__dlm
    WHERE ssot__Account__dlm.KQ_Id__c = 'SAP'
) sap ON Account_Hierarchy__cio.AccountNumber__c = sap.ShipToId__c
WHERE Sales_Ledger__dlm.sales_org__c IN ('S01', 'S02')
  AND Region_Assignment__dlm.Common_Owner__c IS NULL
GROUP BY Account_Hierarchy__cio.COAccountId__c,
         Account_Hierarchy__cio.AccountId__c,
         Sales_Ledger__dlm.matl_id__c,
         ssot__Product__dlm.ssot__Id__c,
         co.AccountName__c,
         sap.Channel__c,
         DATE_TRUNC('MONTH', Sales_Ledger__dlm.sls_trans_dt__c)
) wd
FULL JOIN (
SELECT Account_Hierarchy__cio.COAccountId__c   AS CommonOwnerCRMAccountId__c,
       Account_Hierarchy__cio.AccountId__c     AS CRMAccountId__c,
       Sales_Ledger__dlm.matl_id__c            AS MaterialId__c,
       ssot__Product__dlm.ssot__Id__c          AS ProductId__c,
       co.AccountName__c                       AS CommonOwnerName__c,
       sap.Channel__c                          AS ChannelCode__c,
       DATE_TRUNC('MONTH', Sales_Ledger__dlm.sls_trans_dt__c) AS TxMonth__c,
       SUM(Sales_Ledger__dlm.pos_net_ship_qty__c) AS NetShipmentQuantity__c
FROM Sales_Ledger__dlm
INNER JOIN ssot__GoodsProduct__dlm
    ON Sales_Ledger__dlm.matl_id__c = ssot__GoodsProduct__dlm.ssot__Id__c
LEFT OUTER JOIN ssot__Product__dlm
    ON ssot__GoodsProduct__dlm.ProductCode__c = ssot__Product__dlm.ssot__ProductCode__c
INNER JOIN Account_Hierarchy__cio
    ON Sales_Ledger__dlm.g3x_cust_id__c = Account_Hierarchy__cio.AccountNumber__c
INNER JOIN ssot__Account__dlm
    ON Account_Hierarchy__cio.AccountId__c = ssot__Account__dlm.ssot__Id__c
   AND ssot__Account__dlm.KQ_Id__c = 'CRM'
   AND ssot__Account__dlm.RecordTypeId__c = '012000000000001AAA'
INNER JOIN Region_Assignment__dlm
    ON Account_Hierarchy__cio.COAccountNumber__c = Region_Assignment__dlm.Common_Owner__c
   AND ssot__GoodsProduct__dlm.BusinessUnit_Number__c = Region_Assignment__dlm.BusinessUnit__c
LEFT OUTER JOIN (
    SELECT ssot__Account__dlm.ssot__Id__c   AS AccountId__c,
           ssot__Account__dlm.ssot__Name__c AS AccountName__c
    FROM ssot__Account__dlm
    WHERE ssot__Account__dlm.KQ_Id__c = 'CRM'
) co ON Account_Hierarchy__cio.COAccountId__c = co.AccountId__c
LEFT OUTER JOIN (
    SELECT ssot__Account__dlm.ssot__Id__c     AS ShipToId__c,
           ssot__Account__dlm.Channel_Code__c AS Channel__c
    FROM ssot__Account__dlm
    WHERE ssot__Account__dlm.KQ_Id__c = 'SAP'
) sap ON Account_Hierarchy__cio.AccountNumber__c = sap.ShipToId__c
WHERE Sales_Ledger__dlm.sales_org__c IN ('S01', 'S02')
  AND Region_Assignment__dlm.G3X_Indicator__c = 'Y'
GROUP BY Account_Hierarchy__cio.COAccountId__c,
         Account_Hierarchy__cio.AccountId__c,
         Sales_Ledger__dlm.matl_id__c,
         ssot__Product__dlm.ssot__Id__c,
         co.AccountName__c,
         sap.Channel__c,
         DATE_TRUNC('MONTH', Sales_Ledger__dlm.sls_trans_dt__c)
) d
ON wd.CRMAccountId__c = d.CRMAccountId__c
   AND wd.ProductId__c = d.ProductId__c
   AND wd.TxMonth__c = d.TxMonth__c
FULL JOIN (
SELECT Account_Hierarchy__cio.COAccountId__c   AS CommonOwnerCRMAccountId__c,
       Account_Hierarchy__cio.AccountId__c     AS CRMAccountId__c,
       Sales_Ledger__dlm.matl_id__c            AS MaterialId__c,
       ssot__Product__dlm.ssot__Id__c          AS ProductId__c,
       co.AccountName__c                       AS CommonOwnerName__c,
       DATE_TRUNC('MONTH', Sales_Ledger__dlm.sls_trans_dt__c) AS TxMonth__c,
       SUM(Sales_Ledger__dlm.assoc_qty__c)     AS NetShipmentQuantity__c
FROM Sales_Ledger__dlm
INNER JOIN ssot__GoodsProduct__dlm
    ON Sales_Ledger__dlm.matl_id__c = ssot__GoodsProduct__dlm.ssot__Id__c
LEFT OUTER JOIN ssot__Product__dlm
    ON ssot__GoodsProduct__dlm.ProductCode__c = ssot__Product__dlm.ssot__ProductCode__c
INNER JOIN Account_Hierarchy__cio
    ON Sales_Ledger__dlm.assoc_cust_id__c = Account_Hierarchy__cio.AccountNumber__c
INNER JOIN ssot__Account__dlm
    ON Account_Hierarchy__cio.AccountId__c = ssot__Account__dlm.ssot__Id__c
   AND ssot__Account__dlm.KQ_Id__c = 'CRM'
   AND ssot__Account__dlm.RecordTypeId__c = '012000000000001AAA'
LEFT OUTER JOIN (
    SELECT ssot__Account__dlm.ssot__Id__c   AS AccountId__c,
           ssot__Account__dlm.ssot__Name__c AS AccountName__c
    FROM ssot__Account__dlm
    WHERE ssot__Account__dlm.KQ_Id__c = 'CRM'
) co ON Account_Hierarchy__cio.COAccountId__c = co.AccountId__c
WHERE Sales_Ledger__dlm.sales_org__c IN ('S01', 'S02')
GROUP BY Account_Hierarchy__cio.COAccountId__c,
         Account_Hierarchy__cio.AccountId__c,
         Sales_Ledger__dlm.matl_id__c,
         ssot__Product__dlm.ssot__Id__c,
         co.AccountName__c,
         DATE_TRUNC('MONTH', Sales_Ledger__dlm.sls_trans_dt__c)
HAVING SUM(Sales_Ledger__dlm.assoc_qty__c) <> 0
) ad
ON wd.CRMAccountId__c = ad.CRMAccountId__c
   AND wd.ProductId__c = ad.ProductId__c
   AND wd.TxMonth__c = ad.TxMonth__c
GROUP BY IFNULL(IFNULL(wd.CommonOwnerCRMAccountId__c, d.CommonOwnerCRMAccountId__c), ad.CommonOwnerCRMAccountId__c),
         IFNULL(IFNULL(wd.CRMAccountId__c, d.CRMAccountId__c), ad.CRMAccountId__c),
         IFNULL(IFNULL(wd.TxMonth__c, d.TxMonth__c), ad.TxMonth__c),
         IFNULL(IFNULL(wd.MaterialId__c, d.MaterialId__c), ad.MaterialId__c)
