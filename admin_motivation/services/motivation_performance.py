"""De-duplicate repeated motivation windows before showing plan/fact totals."""
def grouped_sql(source):
    # These keys mirror the calculation's VariationCalculate PARTITION BY.
    # A global subdivision window repeats on every employee/product row.
    author = "CASE WHEN [VariationCalculate]=N'Подразделение(Глобально)' THEN N'' ELSE [АвторСделки] END"
    planning = "CASE WHEN [VariationCalculate] IN (N'Марка(Бренд)',N'Группа товаров',N'Группа планов продаж') THEN [ГруппаПлановПродаж] ELSE N'' END"
    goods = "CASE WHEN [VariationCalculate] IN (N'Марка(Бренд)',N'Группа товаров') THEN [ГруппаТоваров] ELSE N'' END"
    brand = "CASE WHEN [VariationCalculate]=N'Марка(Бренд)' THEN [Марка] ELSE N'' END"
    dimensions = '[Подразделение],[Год],[КварталМотивации],[VariationCalculate]'
    keys = ','.join((author, planning, goods, brand))
    return (
        'SELECT ' + dimensions + ',ScopeAuthor,SUM(Plan_) AS Plan_,SUM(Fact_) AS Fact_ FROM ('
        'SELECT ' + dimensions + ',' + author + ' AS ScopeAuthor,' + planning + ' AS ScopePlanning,'
        + goods + ' AS ScopeGoods,' + brand + ' AS ScopeBrand,'
        'MAX([СуммаПланUSD]) AS Plan_,MAX([СуммаОтгрузокUSD]) AS Fact_'
        ' FROM (' + source + ') AS input_rows GROUP BY ' + dimensions + ',' + keys +
        ') AS unique_windows GROUP BY ' + dimensions + ',ScopeAuthor')
