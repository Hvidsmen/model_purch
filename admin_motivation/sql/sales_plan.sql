;WITH fact AS (
 SELECT sub.SubdivisionName, p.PlanningGroupSalesErp,
 CASE WHEN p.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN p.GroupERP ELSE '*' END GroupERP,
 CASE WHEN p.GroupERP IN (N'Бытовые кондиционеры',N'Полупромышленные кондиционеры',N'Системы мульти-сплит',N'Системы VRF') THEN p.BrandName ELSE '*' END Brand,
 CASE WHEN s.DiscountMotivation >= COALESCE(sm.O4,10000) THEN 'O4'
 WHEN s.DiscountMotivation >= COALESCE(sm.O3,10000) AND s.DiscountMotivation < COALESCE(sm.O4,10000) THEN 'O3'
 WHEN s.DiscountMotivation >= COALESCE(sm.O2,10000) AND s.DiscountMotivation < COALESCE(sm.O3,10000) THEN 'O2'
 WHEN s.DiscountMotivation >= COALESCE(sm.O1,10000) AND s.DiscountMotivation < COALESCE(sm.O2,10000) THEN 'O1' ELSE 'O0' END Segment,
 (s.Price - s.AmountUSDIt_ITW + s.BallsOuterUSD) USD
 FROM DataWH.dbo.Sales s
 INNER JOIN DataWH.dbo.Customer c ON s.CustomerKey = c.CustomerKey
 INNER JOIN DataWH.dbo.Subdivisions sub ON s.Subdivision = sub.SubdivisionKey
 INNER JOIN DataWH.dbo.Product p ON s.ProductCode = p.ProductCode AND p.[Тип номенклатуры] = N'Товар'
 LEFT JOIN DataWH.motivation.vSegmentMotivationFromTO sm ON s.ClosedDealDate BETWEEN sm.DateFrom AND sm.DateTo
 AND s.ДаичиТипСкидкиНаДату LIKE CONCAT('%',sm.TypeDiscount,'%')
 WHERE YEAR(s.ClosedDealDate) BETWEEN YEAR(GETDATE())-4 AND YEAR(GETDATE())-1
 AND sub.Chanel NOT IN (N'ДПП')
 AND sub.SubdivisionName NOT IN (N'ДПП',N'REtail',N'E-Com',N'СНГ',N'Д-Маврикий',N'Буфер',N'Брендинг')
), fact_per AS (
 SELECT SubdivisionName, PlanningGroupSalesErp, GroupERP, Brand,
 CASE WHEN SUM(USD)=0 THEN 1 ELSE SUM(IIF(Segment='O0',USD,0))/SUM(USD) END O0,
 CASE WHEN SUM(USD)=0 THEN 0 ELSE SUM(IIF(Segment='O1',USD,0))/SUM(USD) END O1,
 CASE WHEN SUM(USD)=0 THEN 0 ELSE SUM(IIF(Segment='O2',USD,0))/SUM(USD) END O2,
 CASE WHEN SUM(USD)=0 THEN 0 ELSE SUM(IIF(Segment='O3',USD,0))/SUM(USD) END O3,
 CASE WHEN SUM(USD)=0 THEN 0 ELSE SUM(IIF(Segment='O4',USD,0))/SUM(USD) END O4
 FROM fact GROUP BY SubdivisionName, PlanningGroupSalesErp, GroupERP, Brand
), plan_ AS (
 SELECT s.SubdivisionName Subdivision, g.PlanningGroupSalesERP, CAST(ps.Date_ AS date) Date_,
 CASE WHEN g.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN g.GroupERP ELSE '*' END GroupERP,
 CASE WHEN g.GroupERP IN (N'Бытовые кондиционеры',N'Полупромышленные кондиционеры',N'Системы мульти-сплит',N'Системы VRF') THEN g.BrandName ELSE '*' END Brand,
 SUM(ps.AmountUSD) AmountUSD
 FROM DataWH.planning.PlanSales ps
 INNER JOIN DataWH.dbo.Subdivisions s ON s.SubdivisionKey = ps.Subdivision
 INNER JOIN DataWH.planning.Goods g ON ps.GoodsKey = g.PlanningKey
 WHERE CONVERT(nvarchar(255), ps.Version_) = ?
 AND s.Chanel NOT IN (N'ДПП')
 AND s.SubdivisionName NOT IN (N'ДПП',N'REtail',N'E-Com',N'СНГ',N'Д-Маврикий',N'Буфер',N'Брендинг')
 GROUP BY s.SubdivisionName, g.PlanningGroupSalesERP, CAST(ps.Date_ AS date),
 CASE WHEN g.PlanningGroupSalesErp IN ('1. RAC','2. VRF','3. PROF') THEN g.GroupERP ELSE '*' END,
 CASE WHEN g.GroupERP IN (N'Бытовые кондиционеры',N'Полупромышленные кондиционеры',N'Системы мульти-сплит',N'Системы VRF') THEN g.BrandName ELSE '*' END
)
SELECT plan_.Subdivision, plan_.PlanningGroupSalesERP, plan_.Date_, plan_.GroupERP, plan_.Brand, plan_.AmountUSD,
 plan_.AmountUSD*COALESCE(f.O0,1) USD_O0, plan_.AmountUSD*COALESCE(f.O1,0) USD_O1,
 plan_.AmountUSD*COALESCE(f.O2,0) USD_O2, plan_.AmountUSD*COALESCE(f.O3,0) USD_O3,
 plan_.AmountUSD*COALESCE(f.O4,0) USD_O4
FROM plan_ LEFT JOIN fact_per f ON plan_.Subdivision=f.SubdivisionName
 AND plan_.PlanningGroupSalesERP=f.PlanningGroupSalesErp AND plan_.GroupERP=f.GroupERP AND plan_.Brand=f.Brand
