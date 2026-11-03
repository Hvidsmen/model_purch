"""Show department eligibility from calculation inputs, not projected row maxima."""
GLOBAL_PLAN_FACT = 'DataWH.motivation.PlanFactAll2026'
DEPARTMENT = 'Всё подразделение'


def plan_fact_table(variant):
    if variant not in ('main', 'all_2026'):
        raise ValueError('Unknown calculation variant')
    return GLOBAL_PLAN_FACT if variant == 'all_2026' else 'DataWH.motivation.PlanFact'


def presentation_rows(rows, kind):
    from .reports import number, ratio
    return [{'kind': kind, 'subdivision': row['Подразделение'], 'year': row['Год'],
             'period': row['КварталМотивации'], 'formula': row.get('VariationCalculate') or 'Подразделение',
             'scope_author': row.get('ScopeAuthor') or 'Всё подразделение',
             'plan': number(row.get('Plan_')), 'fact': number(row.get('Fact_')),
             'ratio': ratio(number(row.get('Fact_')), number(row.get('Plan_')))} for row in rows]


def department_sql(source, calculation_variant='all_2026'):
    """Real department inputs, once per sales row, without motivation exclusions.

    The filtered report supplies only authorized subdivision/year/period contexts.
    Amounts come from physical sales/plan inputs, never employee projections.
    Q1 retains its real plan; all periods use the same coverage as eligibility.
    """
    table = plan_fact_table(calculation_variant)
    return (
        'WITH input_rows AS (' + source + '), contexts AS ('
        'SELECT DISTINCT [Подразделение],[Год],[КварталМотивации] FROM input_rows) '
        'SELECT c.[Подразделение],c.[Год],c.[КварталМотивации],'
        "N'Общий план-факт' AS VariationCalculate,N'' AS ScopeAuthor,"
        'SUM(pf.[СуммаПланUSD]) AS Plan_,SUM(pf.[СуммаОтгрузокUSD]) AS Fact_ '
        'FROM contexts c JOIN ' + table + ' pf ON pf.[Подразделение]=c.[Подразделение] '
        "AND pf.[Год]=c.[Год] AND LTRIM(RTRIM(pf.[ТипКоэф]))=N'Продажи' AND (" + period_coverage('c') + ') '
        'GROUP BY c.[Подразделение],c.[Год],c.[КварталМотивации] UNION ALL '
        "SELECT pf.[Подразделение],pf.[Год],N'Итого',N'Общий план-факт',N'',"
        'SUM(pf.[СуммаПланUSD]),SUM(pf.[СуммаОтгрузокUSD]) FROM ' + table + ' pf '
        "WHERE LTRIM(RTRIM(pf.[ТипКоэф]))=N'Продажи' AND EXISTS (SELECT 1 FROM contexts c "
        'WHERE c.[Подразделение]=pf.[Подразделение] AND c.[Год]=pf.[Год] AND (' + period_coverage('c') + ')) '
        'GROUP BY pf.[Подразделение],pf.[Год]')


def period_coverage(context):
    period = f'LOWER({context}.[КварталМотивации])'
    return (
        f"({period} LIKE N'1 кв%' AND pf.[Квартал]=1) OR "
        f"({period} LIKE N'2 кв%' AND pf.[Квартал] IN (1,2)) OR "
        f"({period} LIKE N'3 кв%' AND pf.[Квартал]=3) OR "
        f"(({period} LIKE N'4 кв%' OR {period}=N'год') AND pf.[Квартал] IN (1,2,3,4))")


def grouped_sql(source, coefficient_type=None, calculation_variant='all_2026'):
    # Policy inputs must not be substituted with sales inputs. Directors share
    # the unreplicated sales base; their channel/subdivision rules differ.
    input_type = 'Политики' if str(coefficient_type or '').strip().casefold() == 'политики' else 'Продажи'
    input_filter = "LTRIM(RTRIM(pf.[ТипКоэф]))=N'" + input_type + "'"
    author = "CASE WHEN [VariationCalculate]=N'Подразделение(Глобально)' THEN N'' ELSE [АвторСделки] END"
    planning = "CASE WHEN [VariationCalculate] IN (N'Марка(Бренд)',N'Группа товаров',N'Группа планов продаж') THEN [ГруппаПлановПродаж] ELSE N'' END"
    goods = "CASE WHEN [VariationCalculate] IN (N'Марка(Бренд)',N'Группа товаров') THEN [ГруппаТоваров] ELSE N'' END"
    brand = "CASE WHEN [VariationCalculate]=N'Марка(Бренд)' THEN [Марка] ELSE N'' END"
    dimensions = '[Подразделение],[Год],[КварталМотивации],[VariationCalculate]'
    keys = ','.join((author, planning, goods, brand))
    base_filter = '1=1'
    if str(coefficient_type or '').strip().casefold() != 'директора':
        from .director_participation import EXCLUDED
        excluded = ','.join("N'" + name.replace("'", "''") + "'" for name in EXCLUDED)
        base_filter = "(pf.[Канал] IS NULL OR pf.[Канал]<>N'ДПП') AND pf.[Подразделение] NOT IN (" + excluded + ')'
    return (
        'WITH input_rows AS (' + source + '), personal_windows AS ('
        'SELECT ' + dimensions + ',' + author + ' AS ScopeAuthor,' + planning + ' AS ScopePlanning,'
        + goods + ' AS ScopeGoods,' + brand + ' AS ScopeBrand,'
        'MAX([СуммаПланUSD]) AS Plan_,MAX([СуммаОтгрузокUSD]) AS Fact_'
        " FROM input_rows WHERE COALESCE([VariationCalculate],N'')<>N'Подразделение(Глобально)'"
        ' GROUP BY ' + dimensions + ',' + keys + '), global_contexts AS ('
        'SELECT DISTINCT ' + dimensions + ' FROM input_rows'
        " WHERE [VariationCalculate]=N'Подразделение(Глобально)'), global_totals AS ("
        'SELECT [Подразделение],[Год],[VariationCalculate],'
        "MIN(CASE WHEN LOWER([КварталМотивации]) LIKE N'1 кв%' THEN 1 ELSE 0 END) AS FirstOnly "
        'FROM global_contexts GROUP BY [Подразделение],[Год],[VariationCalculate]) '
        'SELECT ' + dimensions + ',ScopeAuthor,SUM(Plan_) AS Plan_,SUM(Fact_) AS Fact_'
        ' FROM personal_windows GROUP BY ' + dimensions + ',ScopeAuthor UNION ALL '
        'SELECT c.[Подразделение],c.[Год],c.[КварталМотивации],c.[VariationCalculate],'
        "N'' AS ScopeAuthor,"
        "SUM(CASE WHEN LOWER(c.[КварталМотивации]) LIKE N'1 кв%' THEN pf.[СуммаОтгрузокUSD] "
        'ELSE pf.[СуммаПланUSD] END) AS Plan_,SUM(pf.[СуммаОтгрузокUSD]) AS Fact_ '
        'FROM global_contexts c JOIN ' + plan_fact_table(calculation_variant) + ' pf ON '
        'pf.[Подразделение]=c.[Подразделение] AND pf.[Год]=c.[Год] '
        'AND ' + input_filter + ' AND (' + period_coverage('c') + ') '
        'WHERE ' + base_filter + ' '
        'GROUP BY c.[Подразделение],c.[Год],c.[КварталМотивации],c.[VariationCalculate] '
        'UNION ALL '
        # EXISTS selects the union of input quarters once, even for overlapping
        # Q2/Q4/year windows. Do not sum the already cumulative display values.
        "SELECT t.[Подразделение],t.[Год],N'Итого',t.[VariationCalculate],N'',"
        'SUM(CASE WHEN t.FirstOnly=1 THEN pf.[СуммаОтгрузокUSD] ELSE pf.[СуммаПланUSD] END),'
        'SUM(pf.[СуммаОтгрузокUSD]) '
        'FROM global_totals t JOIN ' + plan_fact_table(calculation_variant) + ' pf ON '
        'pf.[Подразделение]=t.[Подразделение] AND pf.[Год]=t.[Год] '
        'AND ' + input_filter + ' AND EXISTS ('
        'SELECT 1 FROM global_contexts coverage WHERE coverage.[Подразделение]=t.[Подразделение] '
        'AND coverage.[Год]=t.[Год] AND coverage.[VariationCalculate]=t.[VariationCalculate] '
        'AND (' + period_coverage('coverage') + ')) '
        'WHERE ' + base_filter + ' '
        'GROUP BY t.[Подразделение],t.[Год],t.[VariationCalculate]')
