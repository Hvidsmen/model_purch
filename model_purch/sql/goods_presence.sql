;WITH parameters AS (
 SELECT CONVERT(date, ?, 23) AS DateFrom, CONVERT(date, ?, 23) AS DateTo,
        CAST(? AS nvarchar(255)) AS PlanningGroup
), work_days AS (
 SELECT FullDateAlternateKey, CalendarYear, MonthNumberOfYear,
  1.0 / COUNT(*) OVER (PARTITION BY CalendarYear, MonthNumberOfYear) AS Weight
 FROM DataWH.dbo.Date WHERE IsWorkDay = 1
), plan_erp AS (
 SELECT p.Количество * d.Weight AS Quantity,
        p.Количество * d.Weight * mce.FlagAccountingQtyBA AS AccountingQuantity,
        p.даичи_СуммаДДП * d.Weight AS Amount
 FROM DataWH.erp.[РегистрыНакопления.ПланыПродаж] p
 INNER JOIN DataWH.erp.[Справочники.Номенклатура] n ON p.НоменклатураСсылка = n.Ссылка
 INNER JOIN DataWH.dbo.ModelCodeERP mce ON n.Наименование = mce.ModelCode
 INNER JOIN DataWH.erp.[Справочники.СтруктураПредприятия] sub ON p.ПодразделениеСсылка = sub.Ссылка
 INNER JOIN work_days d ON YEAR(CAST(p.Период AS date)) = d.CalendarYear AND MONTH(CAST(p.Период AS date)) = d.MonthNumberOfYear
 CROSS JOIN parameters dates
 WHERE d.FullDateAlternateKey BETWEEN dates.DateFrom AND dates.DateTo
  AND LOWER(LTRIM(RTRIM(n.Даичи_ГруппаПланирования))) = LOWER(LTRIM(RTRIM(dates.PlanningGroup)))
  AND p.Сценарий IN (/*SCENARIOS*/)
), plan_initial AS (
 SELECT pe.Количество AS Quantity, pe.Количество * mce.FlagAccountingQtyBA AS AccountingQuantity, pe.СуммаДДП AS Amount
 FROM ModelPurch.dbo.PlanSalesERPAll pe
 INNER JOIN DataWH.dbo.ModelCodeERP mce ON pe.Номенклатура = mce.ModelCode
 CROSS JOIN parameters dates
 WHERE pe.Date_ BETWEEN dates.DateFrom AND dates.DateTo AND pe.Сценарий IN (/*SCENARIOS*/)
  AND LOWER(LTRIM(RTRIM(mce.PlanningGroupOZPErp))) = LOWER(LTRIM(RTRIM(dates.PlanningGroup)))
), stock_cost AS (
 SELECT s.Количество AS Quantity, s.Количество * mce.FlagAccountingQtyBA AS AccountingQuantity, s.DDP_USD AS Amount
 FROM parameters dates
 CROSS APPLY DataWH.erp.t_Даичи_ПервичнаяСебестоимостьOnDate(dates.DateFrom) s
 INNER JOIN DataWH.erp.[Справочники.Номенклатура] n ON s.Номенклатура = n.Наименование
 INNER JOIN DataWH.dbo.ModelCodeERP mce ON n.Наименование = mce.ModelCode
 WHERE LOWER(LTRIM(RTRIM(n.Даичи_ГруппаПланирования))) = LOWER(LTRIM(RTRIM(dates.PlanningGroup)))
), stock_initial AS (
 SELECT sg.НоменклатураСсылка,
        SUM(sg.ВНаличии + sg.ВПути) AS Quantity,
        SUM((sg.ВНаличии + sg.ВПути) * mce.FlagAccountingQtyBA) AS AccountingQuantity
 FROM DataWH.erp.StreamGoods sg
 INNER JOIN DataWH.erp.[Справочники.Номенклатура] n ON sg.НоменклатураСсылка = n.Ссылка
 INNER JOIN DataWH.dbo.ModelCodeERP mce ON mce.ModelCode = n.Наименование
 INNER JOIN DataWH.erp.[Справочник.Склады] store ON store.Ссылка = sg.СкладСсылка AND store.даичи_НазначениеСклада = N'Продажа'
 CROSS JOIN parameters dates
 WHERE sg.ReportDate = dates.DateFrom
  AND LOWER(LTRIM(RTRIM(n.Даичи_ГруппаПланирования))) = LOWER(LTRIM(RTRIM(dates.PlanningGroup)))
 GROUP BY sg.НоменклатураСсылка
 HAVING SUM(sg.ВНаличии + sg.ВПути) > 0
), income AS (
 SELECT sg.НоменклатураСсылка, sg.ТипПоступления, arrival.ExpectedDate,
        SUM(sg.ВПути + sg.КПоступлению) AS Quantity,
        SUM((sg.ВПути + sg.КПоступлению) * mce.FlagAccountingQtyBA) AS AccountingQuantity
 FROM DataWH.erp.StreamGoods sg
 INNER JOIN DataWH.erp.[Справочники.Номенклатура] n ON sg.НоменклатураСсылка = n.Ссылка
 INNER JOIN DataWH.dbo.ModelCodeERP mce ON mce.ModelCode = n.Наименование
 INNER JOIN DataWH.erp.[Справочник.Склады] store ON store.Ссылка = sg.СкладСсылка
 CROSS JOIN parameters dates
 CROSS APPLY (SELECT CASE WHEN sg.ТипПоступления = N'ЗаказПоставщику'
   THEN DATEADD(MONTH,2,IIF(sg.ДатаПоступления <= sg.ReportDate, DATEADD(DAY,14,sg.ReportDate), sg.ДатаПоступления))
   ELSE IIF(sg.ДатаПоступления <= sg.ReportDate, DATEADD(DAY,14,sg.ReportDate), sg.ДатаПоступления) END AS ExpectedDate) arrival
 WHERE sg.ReportDate = dates.DateFrom
  AND (sg.ТипПоступления IN (N'ЗаказПоставщику',N'ПриобретениеТоваровУслуг')
       OR (sg.ТипПоступления = N'ПеремещениеТоваров' AND store.даичи_НазначениеСклада != N'Продажа'))
  AND arrival.ExpectedDate BETWEEN dates.DateFrom AND dates.DateTo
  AND LOWER(LTRIM(RTRIM(n.Даичи_ГруппаПланирования))) = LOWER(LTRIM(RTRIM(dates.PlanningGroup)))
 GROUP BY sg.НоменклатураСсылка, sg.ТипПоступления, arrival.ExpectedDate
 HAVING SUM(sg.ВПути + sg.КПоступлению) > 0
)
SELECT 'plan_erp', COUNT_BIG(*), SUM(Quantity), SUM(AccountingQuantity), SUM(Amount) FROM plan_erp
UNION ALL SELECT 'plan_initial', COUNT_BIG(*), SUM(Quantity), SUM(AccountingQuantity), SUM(Amount) FROM plan_initial
UNION ALL SELECT 'stock_cost', COUNT_BIG(*), SUM(Quantity), SUM(AccountingQuantity), SUM(Amount) FROM stock_cost
UNION ALL SELECT 'stock_initial', COUNT_BIG(*), SUM(Quantity), SUM(AccountingQuantity), NULL FROM stock_initial
UNION ALL SELECT 'income', COUNT_BIG(*), SUM(Quantity), SUM(AccountingQuantity), NULL FROM income;
