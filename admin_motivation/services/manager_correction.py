"""Apply the approved subdivision period correction once, before annual carry."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql

TABLE = r'(?:\[?DataWH\]?\s*\.\s*)?\[?motivation\]?\s*\.\s*\[?ReportMotivationByDealAuthor\]?'

# The period dimension is an integer in this private working set: 1..4, 5=year.
SQL = """
-- Portal subdivision amount correction, before annual carry is derived.
SELECT [Date_],[Subdivision],
 CASE LOWER(REPLACE(REPLACE(LTRIM(RTRIM([Kind_])),N' ',N''),N'.',N''))
 WHEN N'1кв' THEN 1 WHEN N'2кв' THEN 2 WHEN N'3кв' THEN 3 WHEN N'4кв' THEN 4
 WHEN N'год' THEN 5 WHEN N'year' THEN 5 END AS PeriodId,[Coeff_]
INTO #portal_manager_correction
FROM DataWH.motivation.SubdivisionManagerCoeff;
IF EXISTS (SELECT 1 FROM #portal_manager_correction WHERE PeriodId IS NULL)
 THROW 50020,N'Неизвестный период поправочного коэффициента подразделения',1;
IF EXISTS (SELECT 1 FROM #portal_manager_correction GROUP BY [Date_],[Subdivision],PeriodId HAVING COUNT(*)>1)
 THROW 50021,N'Дубли поправочных коэффициентов подразделения',1;

UPDATE r SET
 [СуммаМотивацииUSD]=r.[СуммаМотивацииUSD]*COALESCE(k.Coeff_,1),
 [СуммаМотивацииUSDУчетПроцента]=r.[СуммаМотивацииUSDУчетПроцента]*COALESCE(k.Coeff_,1),
 [СуммаМотивацииRUR]=r.[СуммаМотивацииRUR]*COALESCE(k.Coeff_,1),
 [СуммаМотивацииRURУчетПроцента]=r.[СуммаМотивацииRURУчетПроцента]*COALESCE(k.Coeff_,1)
FROM DataWH.motivation.ReportMotivationByDealAuthor r
CROSS APPLY (SELECT CASE
 WHEN LOWER(LTRIM(RTRIM(r.[КварталМотивации]))) LIKE N'1 кв%' THEN 1
 WHEN LOWER(LTRIM(RTRIM(r.[КварталМотивации]))) LIKE N'2 кв%' THEN 2
 WHEN LOWER(LTRIM(RTRIM(r.[КварталМотивации]))) LIKE N'3 кв%' THEN 3
 WHEN LOWER(LTRIM(RTRIM(r.[КварталМотивации]))) LIKE N'4 кв%' THEN 4
 WHEN LOWER(LTRIM(RTRIM(r.[КварталМотивации])))=N'год' THEN 5 END AS PeriodId) p
OUTER APPLY (SELECT TOP (1) c.Coeff_ FROM #portal_manager_correction c
 WHERE c.Subdivision=r.[Подразделение] AND c.PeriodId=p.PeriodId
 AND c.Date_<=EOMONTH(DATEFROMPARTS(r.[Год],CASE WHEN p.PeriodId=5 THEN 12 ELSE p.PeriodId*3 END,1))
 ORDER BY c.Date_ DESC) k;

UPDATE f SET
 [СуммаКвМотивацииUSD]=f.[СуммаКвМотивацииUSD]*COALESCE(q.Coeff_,1),
 [СуммаМотивацииUSDГод]=f.[СуммаМотивацииUSDГод]*COALESCE(y.Coeff_,1),
 [СуммаМотивацииUSD]=f.[СуммаКвМотивацииUSD]*COALESCE(q.Coeff_,1)+f.[СуммаМотивацииUSDГод]*COALESCE(y.Coeff_,1),
 [AmountRURMotive]=f.[AmountRURMotive]*(@QuarterShare*COALESCE(q.Coeff_,1)+@YearShare*COALESCE(y.Coeff_,1))
FROM DataWH.motivation.Fact f
OUTER APPLY (SELECT TOP (1) c.Coeff_ FROM #portal_manager_correction c
 WHERE c.Subdivision=f.[Подразделение] AND c.PeriodId=DATEPART(QUARTER,f.[ДатаЗакрытияСделки])
 AND c.Date_<=EOMONTH(DATEFROMPARTS(YEAR(f.[ДатаЗакрытияСделки]),DATEPART(QUARTER,f.[ДатаЗакрытияСделки])*3,1))
 ORDER BY c.Date_ DESC) q
OUTER APPLY (SELECT TOP (1) c.Coeff_ FROM #portal_manager_correction c
 WHERE c.Subdivision=f.[Подразделение] AND c.PeriodId=5
 AND c.Date_<=DATEFROMPARTS(YEAR(f.[ДатаЗакрытияСделки]),12,31) ORDER BY c.Date_ DESC) y;
DROP TABLE #portal_manager_correction;

"""


def patch(definition):
    mask = mask_sql(definition)
    if '#portal_manager_correction' in mask.casefold():
        raise ValidationError('Поправка подразделения уже присутствует в исходной процедуре. Требуется проверка совместимости.')
    inserts = list(re.finditer(r'\bINSERT\s+(?:INTO\s+)?' + TABLE + r'(?!\w)', mask, re.I))
    if len(inserts) != 1:
        raise ValidationError('Не найдена однозначная точка перед годовой доплатой для поправки подразделения.')
    point = inserts[0].start()
    prefix = mask[:point]
    required = ('СуммаМотивацииUSDУчетПроцента', 'СуммаМотивацииRURУчетПроцента', 'СуммаКвМотивацииUSD',
                'СуммаМотивацииUSDГод', 'AmountRURMotive', 'КварталМотивации', '@QuarterShare', '@YearShare')
    if not all(name.casefold() in prefix.casefold() for name in required) or not re.search(r'\bINTO\s+' + TABLE, prefix, re.I):
        raise ValidationError('Структура расчёта не поддерживает поправочные коэффициенты. Исходная процедура не изменена.')
    return definition[:point] + SQL + definition[point:]
