"""Validate only participating plans, with actionable private error context."""
from .heat_exclusion import predicate as heat

EXCLUDED=('ДПП','REtail','E-Com','СНГ','Д-Маврикий','Буфер','Брендинг')
ELIGIBLE=("COALESCE([Подразделение],N'') NOT IN ("+','.join("N'"+name+"'" for name in EXCLUDED)+")\n"
    " AND NOT (LTRIM(RTRIM(COALESCE([ТипКоэф],N'')))=N'Политики'\n"
    " AND (COALESCE([Канал],N'')=N'ДПП' OR "+heat('[ГруппаПлановПродаж]')+"))")
INVALID="""[СуммаПланUSD]<>0 AND ([АвторСделки]=N'Нет факта для распределения'
 OR K0 IS NULL OR K1 IS NULL OR K2 IS NULL OR K3 IS NULL OR K4 IS NULL)"""

PLAN_GUARD=f"""
IF EXISTS(SELECT 1 FROM DataWH.motivation.PlanFactTest2025 WHERE [Год]<>2025)
 THROW 50025,N'Выбранный план содержит данные другого года. Нужен план 2025',1;
IF EXISTS(SELECT 1 FROM DataWH.motivation.PlanFactTest2025 WHERE {INVALID} AND {ELIGIBLE})
BEGIN
 DECLARE @test2025PlanError nvarchar(2048),@test2025PlanRows bigint;
 SELECT @test2025PlanRows=COUNT_BIG(*) FROM DataWH.motivation.PlanFactTest2025
 WHERE {INVALID} AND {ELIGIBLE};
 SELECT TOP(1) @test2025PlanError=CONCAT(
 CASE WHEN [АвторСделки]=N'Нет факта для распределения'
 THEN N'Нет факта авторов для распределения плана' ELSE N'Нет полного набора K0–K4' END,
 N'. Подразделение: ',COALESCE([Подразделение],N''),N'; группа планов: ',COALESCE([ГруппаПлановПродаж],N''),
 N'; товары: ',COALESCE([ГруппаТоваров],N''),N'; бренд: ',COALESCE([Марка],N''),
 N'; вид: ',COALESCE([ТипКоэф],N''),N'; план USD: ',CONVERT(nvarchar(50),[СуммаПланUSD]),
 N'; строк с проблемой: ',CONVERT(nvarchar(30),@test2025PlanRows),N'.')
 FROM DataWH.motivation.PlanFactTest2025 WHERE {INVALID} AND {ELIGIBLE}
 ORDER BY [Подразделение],[ГруппаПлановПродаж],[ГруппаТоваров],[Марка],[Квартал];
 SET @test2025PlanError=REPLACE(@test2025PlanError,N'%',N'%%');
 THROW 50025,@test2025PlanError,1;
END;
"""
