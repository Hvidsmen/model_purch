;WITH parameters AS (
 SELECT CAST(? AS date) AS DateFrom, CAST(? AS date) AS DateTo
), work_days AS (
 SELECT FullDateAlternateKey, CalendarYear, MonthNumberOfYear,
  1.0 / COUNT(*) OVER (PARTITION BY CalendarYear, MonthNumberOfYear) AS P_
 FROM DataWH.dbo.Date WHERE IsWorkDay = 1
), plan_ AS (
 SELECT n.Даичи_ГруппаПланирования AS PlanningGroup,
  SUM(p.Количество * d.P_ * mce.FlagAccountingQtyBA) AS PlanQuantity,
  SUM(p.даичи_СуммаДДП * d.P_) AS PlanAmountDDP,
  SUM(p.Количество * d.P_ * n.ОбъемЧислитель) AS PlanVolume
 FROM DataWH.erp.[РегистрыНакопления.ПланыПродаж] p
 INNER JOIN DataWH.erp.[Справочники.Номенклатура] n ON p.НоменклатураСсылка = n.Ссылка
 INNER JOIN DataWH.dbo.ModelCodeERP mce ON n.Наименование = mce.ModelCode
 INNER JOIN DataWH.erp.[Справочники.СтруктураПредприятия] sub ON p.ПодразделениеСсылка = sub.Ссылка
 INNER JOIN work_days d ON YEAR(CAST(p.Период AS date)) = d.CalendarYear AND MONTH(CAST(p.Период AS date)) = d.MonthNumberOfYear
 WHERE d.FullDateAlternateKey BETWEEN (SELECT DateFrom FROM parameters) AND (SELECT DateTo FROM parameters)
  AND p.Сценарий IN (/*SCENARIOS*/)
  AND p.Количество + p.Сумма + p.даичи_СуммаДДП != 0
 GROUP BY n.Даичи_ГруппаПланирования
 HAVING SUM(p.Количество * d.P_ * mce.FlagAccountingQtyBA) > 0
), sales AS (
 SELECT p.PlanningGroupOZPErp AS PlanningGroup,
  SUM(s.AmountExwAEUSD) AS DDP_FACT,
  SUM(s.Quantity * p.Volume) AS FactVolume,
  SUM(s.Quantity * mce.FlagAccountingQtyBA) AS FactQuantity
 FROM DataWH.dbo.Sales s
 INNER JOIN DataWH.dbo.Product p ON s.ProductCode = p.ProductCode
 INNER JOIN DataWH.dbo.ModelCodeERP mce ON p.ModelCode = mce.ModelCode
 WHERE YEAR(s.DateInvoice) IN (
  YEAR((SELECT DateFrom FROM parameters)),
  YEAR((SELECT DateFrom FROM parameters)) - 1,
  YEAR((SELECT DateFrom FROM parameters)) - 2
 )
 GROUP BY p.PlanningGroupOZPErp
 HAVING SUM(s.Quantity * mce.FlagAccountingQtyBA) > 0
), stock AS (
 SELECT n.Даичи_ГруппаПланирования AS PlanningGroup,
  SUM(s.DDP_USD) AS DDPStock,
  SUM(n.ОбъемЧислитель * s.Количество) AS StockVolume,
  SUM(s.Количество * mce.FlagAccountingQtyBA) AS StockQty
 FROM parameters dates
 CROSS APPLY DataWH.erp.t_Даичи_ПервичнаяСебестоимостьOnDate(dates.DateFrom) s
 INNER JOIN DataWH.erp.[Справочники.Номенклатура] n ON s.Номенклатура = n.Наименование
 INNER JOIN DataWH.dbo.ModelCodeERP mce ON n.Наименование = mce.ModelCode
 GROUP BY n.Даичи_ГруппаПланирования
 HAVING SUM(s.Количество * mce.FlagAccountingQtyBA) > 0
)
SELECT COALESCE(plan_.PlanningGroup, stock.PlanningGroup) AS [План. группа],
 COALESCE(plan_.PlanAmountDDP / NULLIF(plan_.PlanQuantity, 0), stock.DDPStock / NULLIF(stock.StockQty, 0)) AS Plan_DDP_UNIT,
 COALESCE(plan_.PlanVolume / NULLIF(plan_.PlanQuantity, 0), stock.StockVolume / NULLIF(stock.StockQty, 0)) AS Plan_VOLUME_UNIT,
 plan_.PlanQuantity AS PLAN_QUANTITY,
 stock.DDPStock / NULLIF(stock.StockQty, 0) AS Stock_DDP_UNIT,
 stock.StockVolume / NULLIF(stock.StockQty, 0) AS Stock_Volume_UNIT,
 stock.StockQty,
 sales.DDP_FACT / NULLIF(sales.FactQuantity, 0) AS Sales_DDP_UNIT,
 sales.FactVolume / NULLIF(sales.FactQuantity, 0) AS Sales_Volume_UNIT,
 sales.FactQuantity AS SalesQty
FROM plan_
FULL JOIN stock ON plan_.PlanningGroup = stock.PlanningGroup
LEFT JOIN sales ON COALESCE(plan_.PlanningGroup, stock.PlanningGroup) = sales.PlanningGroup;
