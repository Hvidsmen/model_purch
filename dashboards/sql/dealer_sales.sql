-- Monthly grain is required for a comparable rolling twelve-month period.
SELECT DATEFROMPARTS(YEAR(s.DateInvoice), MONTH(s.DateInvoice), 1) AS [month],
       CAST(c.CustomerKey AS nvarchar(100)) AS dealer_code,
       MAX(c.CustomerKeyName) AS dealer_name,
       g.Region AS region, g.City AS city,
       p.BrandName AS brand, p.Group_1 AS equipment,
       p.[Range] AS product_range, p.PlanningGroupOZPErp AS planning_group,
       sub.SubdivisionName AS branch,
       SUM(s.Quantity * mce.FlagAccountingQtyBA) AS quantity,
       SUM(s.Price - COALESCE(s.AmountUSDIt_ITW, 0)) AS amount
FROM DataWH.dbo.Sales s
INNER JOIN DataWH.dbo.Subdivisions sub ON s.Subdivision = sub.SubdivisionKey
INNER JOIN DataWH.dbo.Customer c ON s.CustomerKey = c.CustomerKey
INNER JOIN DataWH.dbo.Geography g ON c.GeographyKey = g.GeographyKey
INNER JOIN DataWH.dbo.Product p ON s.ProductCode = p.ProductCode
INNER JOIN DataWH.dbo.ModelCodeERP mce ON p.ModelCode = mce.ModelCode
WHERE s.DateInvoice >= ? AND s.DateInvoice < ?
  AND sub.Chanel = N'Дилерский'
  AND p.BrandName IN ('KENTATSU','MIDEA','AXIOMA','DAICHI','AURUM','PRIMERA','CHIGO')
  AND p.Group_1 IN ('Split','Split Cloud','Multi','LCAC')
GROUP BY DATEFROMPARTS(YEAR(s.DateInvoice), MONTH(s.DateInvoice), 1),
         c.CustomerKey, g.Region, g.City, p.BrandName, p.Group_1, p.[Range],
         p.PlanningGroupOZPErp, sub.SubdivisionName
