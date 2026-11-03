"""Show department eligibility from calculation inputs, not projected row maxima."""
GLOBAL_PLAN_FACT = 'DataWH.motivation.PlanFactAll2026'


def grouped_sql(source):
    # These keys mirror the calculation's VariationCalculate PARTITION BY.
    # Personal formula windows remain independent. Global projections can be
    # raw rows or repeated windows depending on the installed source procedure.
    author = "CASE WHEN [VariationCalculate]=N'Подразделение(Глобально)' THEN N'' ELSE [АвторСделки] END"
    planning = "CASE WHEN [VariationCalculate] IN (N'Марка(Бренд)',N'Группа товаров',N'Группа планов продаж') THEN [ГруппаПлановПродаж] ELSE N'' END"
    goods = "CASE WHEN [VariationCalculate] IN (N'Марка(Бренд)',N'Группа товаров') THEN [ГруппаТоваров] ELSE N'' END"
    brand = "CASE WHEN [VariationCalculate]=N'Марка(Бренд)' THEN [Марка] ELSE N'' END"
    dimensions = '[Подразделение],[Год],[КварталМотивации],[VariationCalculate]'
    keys = ','.join((author, planning, goods, brand))
    return (
        'WITH input_rows AS (' + source + '), personal_windows AS ('
        'SELECT ' + dimensions + ',' + author + ' AS ScopeAuthor,' + planning + ' AS ScopePlanning,'
        + goods + ' AS ScopeGoods,' + brand + ' AS ScopeBrand,'
        'MAX([СуммаПланUSD]) AS Plan_,MAX([СуммаОтгрузокUSD]) AS Fact_'
        " FROM input_rows WHERE COALESCE([VariationCalculate],N'')<>N'Подразделение(Глобально)'"
        ' GROUP BY ' + dimensions + ',' + keys + '), global_contexts AS ('
        'SELECT DISTINCT ' + dimensions + ',[ТипКоэф] FROM input_rows'
        " WHERE [VariationCalculate]=N'Подразделение(Глобально)') "
        'SELECT ' + dimensions + ',ScopeAuthor,SUM(Plan_) AS Plan_,SUM(Fact_) AS Fact_'
        ' FROM personal_windows GROUP BY ' + dimensions + ',ScopeAuthor UNION ALL '
        'SELECT c.[Подразделение],c.[Год],c.[КварталМотивации],c.[VariationCalculate],'
        "N'' AS ScopeAuthor,"
        # Q1 intentionally has no motivation target: display shipments on both sides.
        "SUM(CASE WHEN LOWER(c.[КварталМотивации]) LIKE N'1 кв%' THEN pf.[СуммаОтгрузокUSD] "
        'ELSE pf.[СуммаПланUSD] END) AS Plan_,SUM(pf.[СуммаОтгрузокUSD]) AS Fact_ '
        'FROM global_contexts c JOIN ' + GLOBAL_PLAN_FACT + ' pf ON '
        'pf.[Подразделение]=c.[Подразделение] AND pf.[Год]=c.[Год] '
        'AND LTRIM(RTRIM(pf.[ТипКоэф]))=c.[ТипКоэф] AND ('
        "(LOWER(c.[КварталМотивации]) LIKE N'1 кв%' AND pf.[Квартал]=1) OR "
        "(LOWER(c.[КварталМотивации]) LIKE N'2 кв%' AND pf.[Квартал] IN (1,2)) OR "
        "(LOWER(c.[КварталМотивации]) LIKE N'3 кв%' AND pf.[Квартал]=3) OR "
        "((LOWER(c.[КварталМотивации]) LIKE N'4 кв%' OR LOWER(c.[КварталМотивации])=N'год') "
        'AND pf.[Квартал] IN (1,2,3,4))) '
        "WHERE (pf.[Канал] IS NULL OR pf.[Канал]<>N'ДПП') "
        "AND pf.[Подразделение] NOT IN (N'ДПП',N'REtail',N'E-Com',N'СНГ',N'Д-Маврикий',N'Буфер',N'Брендинг') "
        'GROUP BY c.[Подразделение],c.[Год],c.[КварталМотивации],c.[VariationCalculate]')

