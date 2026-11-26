"""Session-local, compact inputs for the ordinary premium overview."""
import logging
from time import perf_counter
from .manager_detail import DIMENSIONS,MONEY_FIELDS
from .motivation_performance import plan_fact_table,period_coverage
from .period_authors import TABLES,quarters

logger=logging.getLogger(__name__)
REPORT='#portal_overview_input'
PLAN='#portal_overview_plan_fact'
AUTHORS='#portal_overview_authors'


def reset(cursor,name):
    cursor.execute("IF OBJECT_ID('tempdb.."+name+"') IS NOT NULL DROP TABLE "+name)


def author_sql(periods,variant):
    selected=quarters(periods,variant)
    condition='[Квартал] IN ('+','.join(map(str,selected))+')' if selected else '1=0'
    return ("SELECT DISTINCT [Подразделение],[Год],[ТипКоэф],LTRIM(RTRIM(COALESCE([АвторСделки],N''))) AS [АвторСделки] INTO "+AUTHORS+
        ' FROM '+TABLES[variant]+' WHERE '+condition+
        ' AND [Подразделение] IN (@Subdivision) AND [ТипКоэф]=@TypeCoeff_ AND [Год] IN (@Years)'
        ' AND [ГруппаПлановПродаж] IN (@gp_sales) AND [ГруппаТоваров] IN (@GPGoods) AND [Марка] IN (@Brend)')


def restrict_authors(source):
    return ('SELECT r.* FROM ('+source+') r WHERE EXISTS (SELECT 1 FROM '+AUTHORS+' a '
        'WHERE a.[Подразделение]=r.[Подразделение] AND a.[Год]=r.[Год] AND a.[ТипКоэф]=r.[ТипКоэф] '
        "AND a.[АвторСделки]=LTRIM(RTRIM(COALESCE(r.[АвторСделки],N''))))")


def report_sql(source):
    dimensions=[*DIMENSIONS,'Год','КварталМотивации','VariationCalculate','ТипКоэф']
    keys=','.join('['+key+']' for key in dimensions)
    # Money is additive. Native execution windows are repeated on product rows:
    # take MAX at the same product/formula/author grain before personal_windows.
    sums=','.join('SUM(['+key+']) AS ['+key+']' for key in MONEY_FIELDS)
    maxima=','.join('MAX(['+key+']) AS ['+key+']' for key in ('СуммаПланUSD','СуммаОтгрузокUSD'))
    return 'SELECT '+keys+','+sums+','+maxima+' INTO '+REPORT+' FROM ('+source+') s GROUP BY '+keys


def plan_sql(variant):
    dimensions=('Подразделение','Год','Квартал','ТипКоэф','ГруппаПлановПродаж','ГруппаТоваров','Марка','Канал')
    keys=','.join('pf.['+key+']' for key in dimensions)
    return ('SELECT '+keys+',SUM(pf.[СуммаПланUSD]) AS [СуммаПланUSD],SUM(pf.[СуммаОтгрузокUSD]) AS [СуммаОтгрузокUSD] '
        'INTO '+PLAN+' FROM '+plan_fact_table(variant)+" pf WHERE LTRIM(RTRIM(pf.[ТипКоэф])) IN (?,N'Продажи') "
        'AND EXISTS(SELECT 1 FROM '+REPORT+' c WHERE c.[Подразделение]=pf.[Подразделение] AND c.[Год]=pf.[Год] '
        'AND ('+period_coverage('c')+')) GROUP BY '+keys)


def prepare(cursor,source,args,kind,variant):
    started=perf_counter()
    reset(cursor,REPORT);reset(cursor,PLAN)
    cursor.execute(report_sql(source),*args)
    report_done=perf_counter()
    cursor.execute(plan_sql(variant),kind)
    logger.info('Overview SQL inputs: report_ms=%d plan_ms=%d',
        int((report_done-started)*1000),int((perf_counter()-report_done)*1000))
    return 'SELECT * FROM '+REPORT
