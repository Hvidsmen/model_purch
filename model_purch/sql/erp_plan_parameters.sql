;WITH work_days AS (
 SELECT FullDateAlternateKey, CalendarYear, MonthNumberOfYear,
  1.0 / COUNT(*) OVER (PARTITION BY CalendarYear, MonthNumberOfYear) AS P_
 FROM DataWH.dbo.Date WHERE IsWorkDay = 1
), totals AS (
 SELECT n.Даичи_ГруппаПланирования AS PlanningGroup,
  SUM(p.Количество * d.P_) AS Quantity,
  SUM(p.даичи_СуммаДДП * d.P_) AS AmountDDP,
  SUM(p.Количество * d.P_ * n.ОбъемЧислитель) AS Volume
 FROM DataWH.erp.[РегистрыНакопления.ПланыПродаж] p
 INNER JOIN DataWH.erp.[Справочники.Номенклатура] n ON p.НоменклатураСсылка = n.Ссылка
 INNER JOIN DataWH.dbo.ModelCodeERP mce ON n.Наименование = mce.ModelCode
 INNER JOIN DataWH.erp.[Справочники.СтруктураПредприятия] sub ON p.ПодразделениеСсылка = sub.Ссылка
 INNER JOIN work_days d ON YEAR(CAST(p.Период AS date)) = d.CalendarYear AND MONTH(CAST(p.Период AS date)) = d.MonthNumberOfYear
 WHERE d.FullDateAlternateKey BETWEEN ? AND ?
  AND p.Сценарий IN (/*SCENARIOS*/)
  AND p.Количество + p.Сумма + p.даичи_СуммаДДП != 0
 GROUP BY n.Даичи_ГруппаПланирования
 HAVING SUM(p.Количество * d.P_) > 0
)
SELECT PlanningGroup AS [План. группа], AmountDDP / NULLIF(Quantity, 0) AS DDP_UNIT,
 Volume / NULLIF(Quantity, 0) AS VOLUME_UNIT, Quantity AS PLAN_QUANTITY FROM totals;
