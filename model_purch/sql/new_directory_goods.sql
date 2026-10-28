;WITH work_days AS (
 SELECT FullDateAlternateKey, CalendarYear, MonthNumberOfYear,
  1.0 / COUNT(*) OVER (PARTITION BY CalendarYear, MonthNumberOfYear) AS Weight
 FROM DataWH.dbo.Date WHERE IsWorkDay = 1
), plan_groups AS (
 SELECT n.Даичи_ГруппаПланирования AS PlanningGroup
 FROM DataWH.erp.[РегистрыНакопления.ПланыПродаж] p
 INNER JOIN DataWH.erp.[Справочники.Номенклатура] n ON p.НоменклатураСсылка = n.Ссылка
 INNER JOIN DataWH.dbo.ModelCodeERP mce ON n.Наименование = mce.ModelCode
 INNER JOIN DataWH.erp.[Справочники.СтруктураПредприятия] sub ON p.ПодразделениеСсылка = sub.Ссылка
 INNER JOIN work_days d ON YEAR(CAST(p.Период AS date)) = d.CalendarYear
  AND MONTH(CAST(p.Период AS date)) = d.MonthNumberOfYear
 WHERE d.FullDateAlternateKey BETWEEN ? AND ? AND p.Сценарий IN (/*SCENARIOS*/)
 GROUP BY n.Даичи_ГруппаПланирования
 HAVING SUM(p.Количество * d.Weight * mce.FlagAccountingQtyBA) > 0
)
SELECT DISTINCT COALESCE(g.PlanningGroupOZP, p.PlanningGroup) AS PlanningGroupOZP, g.PlanningGroupSalesERP, g.Group_1,
 g.BrandName, g.PlanningKey, g.Purch, g.Volume
FROM plan_groups p
LEFT JOIN ModelPurch.dbo.PlanningGroupOZP g ON LOWER(LTRIM(RTRIM(p.PlanningGroup))) = LOWER(LTRIM(RTRIM(g.PlanningGroupOZP)))
WHERE NULLIF(LTRIM(RTRIM(p.PlanningGroup)), '') IS NOT NULL;
