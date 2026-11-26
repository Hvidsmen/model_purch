"""Frozen regional policy rates and annual post-redistribution plan shares."""
from datetime import date,timedelta
from decimal import Decimal
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql

RULE='rostov-piter-policy-plans-2026-v1'
REGIONS=('Д-Ростов','Д-Питер')
BRANCHES=('Д-Москва','ТЕРМОТРЕЙД',*REGIONS)
REGION_SQL="N'Д-Ростов',N'Д-Питер'"
BRANCH_SQL="N'Д-Москва',N'ТЕРМОТРЕЙД',"+REGION_SQL
FIELDS=('DateFrom','DateTo','Subdivision','PGSales','Group_','Brand','TypeCoeff','K0','K1','K2','K3','K4','VariationCalculate')


def enabled(run):return getattr(run,'configuration',{}).get('regional_rule')==RULE


def approved_rates():
    """Freeze portal settings at creation; never substitute literal SQL defaults."""
    from ..models import GlobalCoeffVersion,SubdivisionCoeff
    versions=list(GlobalCoeffVersion.objects.filter(status='approved').order_by('effective_from','pk'))
    grouped={}
    supported={'Марка(Бренд)','Группа товаров','Группа планов продаж','Подразделение','Подразделение(Глобально)'}
    cells=SubdivisionCoeff.objects.filter(version__in=versions,subdivision__subdivision_key__in=REGIONS,type_coeff__type_coeff_name='Политики').select_related('version','subdivision','goods','segment','variation_calculate')
    for cell in cells:
        if cell.variation_calculate.variation_name not in supported:raise ValidationError('Неизвестная формула политиков: '+cell.variation_calculate.variation_name)
        key=(cell.version_id,cell.subdivision_id,cell.goods_id)
        item=grouped.setdefault(key,{'DateFrom':cell.version.effective_from.isoformat(),'DateTo':'9999-12-31',
            'Subdivision':cell.subdivision.subdivision_key,'PGSales':cell.goods.planning_group_sales,'Group_':cell.goods.group if cell.goods.planning_group_sales in ('1. RAC','2. VRF','3. PROF') else '*',
            'Brand':cell.goods.brand if cell.goods.group in ('Бытовые кондиционеры','Полупромышленные кондиционеры','Системы мульти-сплит','Системы VRF') else '','TypeCoeff':'Политики','VariationCalculate':cell.variation_calculate.variation_name})
        segment=cell.segment.segment_name
        if segment not in ('K0','K1','K2','K3','K4'):raise ValidationError('Неизвестный сегмент коэффициента '+segment)
        if item['VariationCalculate']!=cell.variation_calculate.variation_name:raise ValidationError('У сегментов товара разные формулы: '+cell.goods.goods_key)
        value=Decimal(str(cell.motivation_coeff))
        if not value.is_finite() or value<0:raise ValidationError('Некорректный коэффициент '+cell.goods.goods_key)
        item[segment]=str(value)
    result=[]
    for item in grouped.values():
        if any('K'+str(i) not in item for i in range(5)):raise ValidationError('Заполните K0–K4: '+item['Subdivision']+' · '+item['PGSales'])
        result.append(item)
    timelines={}
    for item in result:
        key=tuple(item[k] for k in ('Subdivision','PGSales','Group_','Brand'))
        timelines.setdefault(key,[]).append(item)
    for items in timelines.values():
        items.sort(key=lambda row:row['DateFrom'])
        for previous,current in zip(items,items[1:]):
            if previous['DateFrom']==current['DateFrom']:raise ValidationError('Повтор настройки региона для одной товарной группы и даты.')
            previous['DateTo']=(date.fromisoformat(current['DateFrom'])-timedelta(days=1)).isoformat()
    return {'versions':[{'id':v.pk,'from':v.effective_from.isoformat(),'revision':v.revision,'fallback_variation':v.fallback_variation or 'Подразделение(Глобально)'} for v in versions],
            'defaults':[{'Date_':v.effective_from.isoformat(),'DefaultFormula':v.fallback_variation or 'Подразделение(Глобально)'} for v in versions],
            'coefficients':sorted(result,key=lambda x:tuple(x[k] for k in ('DateFrom','Subdivision','PGSales','Group_','Brand')))}


def install_rates(cursor,run):
    rows=run.configuration.get('regional_rates',{}).get('coefficients',[])
    defaults=run.configuration.get('regional_rates',{}).get('defaults',[])
    if not defaults:raise ValidationError('Нет утверждённой версии с формулой по умолчанию в запуске. Создайте новый расчёт после утверждения настроек.')
    cursor.execute('CREATE TABLE #share_region_defaults(Date_ date,DefaultFormula nvarchar(255) COLLATE DATABASE_DEFAULT);')
    cursor.executemany('INSERT INTO #share_region_defaults VALUES(?,?)',[(r['Date_'],r['DefaultFormula']) for r in defaults])
    cursor.execute('''CREATE TABLE #share_region_coefficients(
 DateFrom date,DateTo datetime2,Subdivision nvarchar(255) COLLATE DATABASE_DEFAULT,
 PGSales nvarchar(255) COLLATE DATABASE_DEFAULT,Group_ nvarchar(255) COLLATE DATABASE_DEFAULT,
 Brand nvarchar(255) COLLATE DATABASE_DEFAULT,TypeCoeff nvarchar(40) COLLATE DATABASE_DEFAULT,
 K0 float,K1 float,K2 float,K3 float,K4 float,VariationCalculate nvarchar(255) COLLATE DATABASE_DEFAULT);''')
    if rows:cursor.executemany('INSERT INTO #share_region_coefficients VALUES('+','.join('?' for _ in FIELDS)+')',
        [tuple(row[field]+'T23:59:59.9999999' if field=='DateTo' else row[field] for field in FIELDS) for row in rows])


# Shares use all valid 2026 transactions through the cutoff, after reassignment.
# The amount and the denominator are signed, matching the redistribution audit.
WEIGHTS_SQL='''
SELECT s.ShareSourceBranch AS Subdivision,LTRIM(RTRIM(s.[Даичи_АвторСделкиНаименование])) AS Author_,
 SUM(CAST(s.Price-s.AmountUSDIt_ITW+s.BallsOuterUSD AS decimal(28,8))) AS Turnover_
INTO #share_region_turnover FROM #share_transformed s
WHERE s.ShareSourceBranch IN ('''+REGION_SQL+''')
 AND EXISTS(SELECT 1 FROM dbo.Product p WHERE p.ProductCode=s.ProductCode AND p.[Тип номенклатуры]=N'Товар')
 AND EXISTS(SELECT 1 FROM dbo.Customer c WHERE c.CustomerKey=s.CustomerKey)
GROUP BY s.ShareSourceBranch,LTRIM(RTRIM(s.[Даичи_АвторСделкиНаименование]));
IF EXISTS(SELECT v.Subdivision FROM (VALUES(N'Д-Ростов'),(N'Д-Питер')) v(Subdivision)
 WHERE NOT EXISTS(SELECT 1 FROM #share_region_turnover t WHERE t.Subdivision=v.Subdivision))
 THROW 50082,N'Нет факта 2026 для определения долей Ростова/Питера.',1;
IF EXISTS(SELECT 1 FROM #share_region_turnover WHERE Turnover_<=0 OR NULLIF(Author_,N'') IS NULL)
 THROW 50082,N'Нет положительного факта автора после перераспределения Ростова/Питера.',1;
SELECT *,CAST(CAST(Turnover_ AS float)/NULLIF(CAST(SUM(Turnover_) OVER(PARTITION BY Subdivision) AS float),0) AS decimal(38,24)) AS Weight_,
 ROW_NUMBER() OVER(PARTITION BY Subdivision ORDER BY Turnover_ DESC,Author_) AS Rank_
INTO #share_plan_weights FROM #share_region_turnover;
UPDATE w SET Weight_=1-COALESCE((SELECT SUM(o.Weight_) FROM #share_plan_weights o WHERE o.Subdivision=w.Subdivision AND o.Rank_>1),0)
FROM #share_plan_weights w WHERE Rank_=1;
'''

# SQL is column-driven because native #plan includes additional fields on some
# installations. The first author receives the exact residual for every plan row.
SPLIT_SQL='''
ALTER TABLE #plan ALTER COLUMN [АвторСделки] nvarchar(255) COLLATE DATABASE_DEFAULT NULL;
UPDATE #plan SET TypeCoeff_=CASE WHEN [АвторСделки]=N'Продажи' THEN N'Продажи' ELSE N'Политики' END
WHERE [Подразделение] IN ('''+REGION_SQL+''') AND NULLIF(LTRIM(RTRIM(TypeCoeff_)),N'') IS NULL AND [АвторСделки] IN(N'Политики',N'Продажи');
IF EXISTS(SELECT 1 FROM #plan p WHERE p.[Подразделение] IN ('''+REGION_SQL+''') AND (LTRIM(RTRIM(p.TypeCoeff_))=N'Политики' OR p.[АвторСделки]=N'Политики')
 AND NOT EXISTS(SELECT 1 FROM #share_plan_weights w WHERE w.Subdivision=p.[Подразделение]))
 THROW 50082,N'Нет факта 2026 для распределения плана политиков Ростова/Питера.',1;
SELECT p.*,ROW_NUMBER() OVER(ORDER BY (SELECT NULL)) AS SharePlanRow
INTO #share_region_plan FROM #plan p WHERE p.[Подразделение] IN ('''+REGION_SQL+''') AND (LTRIM(RTRIM(p.TypeCoeff_))=N'Политики' OR p.[АвторСделки]=N'Политики');
IF EXISTS(SELECT 1 FROM #share_region_plan p WHERE
 (SELECT COUNT(*) FROM #share_region_coefficients c WHERE c.Subdivision=p.[Подразделение]
 AND p.[ДатаЗакрытияСделки] BETWEEN c.DateFrom AND c.DateTo AND c.PGSales=p.[ГруппаПлановПродаж]
 AND c.Group_=CASE WHEN p.[ГруппаПлановПродаж] IN(N'1. RAC',N'2. VRF',N'3. PROF') THEN p.[ГруппаТоваров] ELSE N'*' END
 AND c.Brand=CASE WHEN p.[ГруппаТоваров] IN(N'Бытовые кондиционеры',N'Полупромышленные кондиционеры',N'Системы мульти-сплит',N'Системы VRF') THEN p.[Марка] ELSE N'' END)>1)
 THROW 50083,N'Для плана Ростова/Питера найдены повторяющиеся утверждённые настройки коэффициентов.',1;
DELETE FROM #plan WHERE [Подразделение] IN ('''+REGION_SQL+''') AND (LTRIM(RTRIM(TypeCoeff_))=N'Политики' OR [АвторСделки]=N'Политики');
DECLARE @RegionColumns nvarchar(max),@RegionProjection nvarchar(max),@RegionSQL nvarchar(max);
SELECT @RegionColumns=STUFF((SELECT N','+QUOTENAME(c.name) FROM tempdb.sys.columns c
 WHERE c.object_id=OBJECT_ID('tempdb..#plan') ORDER BY c.column_id FOR XML PATH(''),TYPE).value('.','nvarchar(max)'),1,1,N'');
SELECT @RegionProjection=STUFF((SELECT N','+CASE
 WHEN c.name=N'АвторСделки' THEN N'w.Author_'
 WHEN c.name=N'СуммаПланUSD' THEN N'CASE WHEN w.Rank_=1 THEN p.[СуммаПланUSD]-SUM(CASE WHEN w.Rank_>1 THEN p.[СуммаПланUSD]*w.Weight_ ELSE 0 END) OVER(PARTITION BY p.SharePlanRow) ELSE p.[СуммаПланUSD]*w.Weight_ END'
 ELSE N'p.'+QUOTENAME(c.name) END FROM tempdb.sys.columns c
 WHERE c.object_id=OBJECT_ID('tempdb..#plan') ORDER BY c.column_id FOR XML PATH(''),TYPE).value('.','nvarchar(max)'),1,1,N'');
SET @RegionSQL=N'INSERT INTO #plan ('+@RegionColumns+N') SELECT '+@RegionProjection+
 N' FROM #share_region_plan p JOIN #share_plan_weights w ON w.Subdivision=p.[Подразделение]';
EXEC sys.sp_executesql @RegionSQL;
IF EXISTS(SELECT [Подразделение],[Год],[Квартал],[ГруппаПлановПродаж],[ГруппаТоваров],[Марка]
 FROM (SELECT [Подразделение],[Год],[Квартал],[ГруппаПлановПродаж],[ГруппаТоваров],[Марка],CAST([СуммаПланUSD] AS decimal(38,8)) Amount_ FROM #share_region_plan
 UNION ALL SELECT [Подразделение],[Год],[Квартал],[ГруппаПлановПродаж],[ГруппаТоваров],[Марка],-CAST([СуммаПланUSD] AS decimal(38,8))
 FROM #plan WHERE [Подразделение] IN ('''+REGION_SQL+''') AND LTRIM(RTRIM(TypeCoeff_))=N'Политики') a
 GROUP BY [Подразделение],[Год],[Квартал],[ГруппаПлановПродаж],[ГруппаТоваров],[Марка] HAVING ABS(SUM(Amount_))>0.01)
 THROW 50084,N'Не сохранена сумма плана Ростова/Питера. Результаты не опубликованы.',1;
'''


# Native procedures may cache this view once, then join the cache for fact and
# plan. Follow only explicit SELECT INTO / CTE dependencies on that view.
_SQL_IDENTIFIER = r'(?:\[[^\]]+\]|[\w#]+)(?:\s*\.\s*(?:\[[^\]]+\]|[\w#]+)){0,2}'


def _relation_key(name):
    return re.sub(r'[\[\]\s]', '', name).casefold()


def coefficient_joins(source):
    from .pilot_formula import closing
    code = mask_sql(source)
    known = {'vsubdivisionmotivecoeffto', 'motivation.vsubdivisionmotivecoeffto',
             'datawh.motivation.vsubdivisionmotivecoeffto'}
    dependencies = []
    for match in re.finditer(r'\bINTO\s+(?P<target>'+_SQL_IDENTIFIER+r')\s+FROM\s+(?P<input>'+_SQL_IDENTIFIER+r')', code, re.I):
        dependencies.append((_relation_key(match['target']), {_relation_key(match['input'])}))
    for match in re.finditer(r'(?P<target>'+_SQL_IDENTIFIER+r')\s+AS\s*\(', code, re.I):
        opening = match.end()-1
        end = closing(code, opening)
        body = code[opening+1:end]
        # Only a single-input coefficient CTE is supported; reject unions/joins
        # rather than replacing a mixed relation with the regional matrix.
        refs = re.findall(r'\bFROM\s+('+_SQL_IDENTIFIER+r')', body, re.I)
        if len(refs)==1 and not re.search(r'\b(?:JOIN|UNION)\b', body, re.I):
            dependencies.append((_relation_key(match['target']), {_relation_key(refs[0])}))
    while True:
        found = {target for target, inputs in dependencies if inputs <= known}
        if found <= known:break
        known.update(found)
    pattern = r'\bJOIN\s+(?P<relation>'+_SQL_IDENTIFIER+r')\s+(?:AS\s+)?\[?sc\]?\s+ON\b'
    return [match for match in re.finditer(pattern, code, re.I)
            if _relation_key(match['relation']) in known]


def patch(source):
    """Applied only to a new shareholder batch; Moscow's native expressions stay."""
    mask=mask_sql(source)
    fact=re.search(r';\s*WITH\s+fact\s+AS\s*\(',mask,re.I)
    if not fact:raise ValidationError('Не найдена база факта для региональных планов.')
    stop=re.search(r'\bUNION\s+ALL\b',mask[fact.end():],re.I)
    if not stop:raise ValidationError('Не найдена граница факта политиков.')
    end=fact.end()+stop.start()
    segment=source[fact.end():end]
    code=mask_sql(segment)
    select=re.search(r'\bSELECT\b',code,re.I)
    if not select:raise ValidationError('Не найдена проекция факта политиков.')
    depth=0;point=select.end();columns=[];author_edits=[]
    for token in re.finditer(r'\(|\)|,|\bFROM\b',code[point:],re.I):
        at=point+token.start();value=token[0].upper()
        if value=='(':depth+=1
        elif value==')':depth-=1
        elif depth==0 and value in (',','FROM'):
            columns.append((columns[-1][1]+1 if columns else point,at))
            if value=='FROM':break
    for start,stop in columns:
        column=code[start:stop]
        alias=re.search(r'(?:\bAS\s+)?\[?АвторСделки\]?\s*$',column,re.I)
        if not alias:continue
        expression=segment[start:start+alias.start()].strip()
        if not expression or expression.endswith('.'):raise ValidationError('Не распознано выражение автора политиков.')
        author_edits.append((start,stop," CASE WHEN sub.SubdivisionName IN ("+REGION_SQL+") THEN LTRIM(RTRIM(s.[Даичи_АвторСделкиНаименование])) ELSE ("+expression+") END AS АвторСделки\n"))
    if len(author_edits)!=1:raise ValidationError('Не распознано назначение автора политиков. Нужна проверка SQL перед региональным расчётом.')
    for start,stop,value in reversed(author_edits):segment=segment[:start]+value+segment[stop:]
    source=source[:fact.end()]+segment+source[end:]
    # Override the regional policy rows at the relation boundary. Every other
    # subdivision/type keeps its native rows, regardless of how a JOIN spells
    # or shares its TypeCoeff predicate (TRIM, LTRIM/RTRIM, brackets, etc.).
    columns=list(FIELDS)
    code=mask_sql(source)
    for field in ('EmployeeCode','EmployeeName'):
        if re.search(r'\[?sc\]?\s*\.\s*\[?'+field+r'\]?(?!\w)',code,re.I):columns.append(field)
    # A loader may SELECT sc.* into its cache; only payout projections need
    # explicit coefficient columns so the regional UNION has a known shape.
    if re.search(r'\[?sc\]?\s*\.\s*\*',code[fact.start():],re.I):
        raise ValidationError('Не поддерживается SELECT sc.* в региональном расчёте. Нужен явный список полей коэффициентов.')
    native=','.join(columns)
    frozen=','.join(field if field in FIELDS else 'CAST(NULL AS nvarchar(255)) AS ['+field+']' for field in columns)
    joins=coefficient_joins(source)
    fact_union=re.search(r'\bUNION\s+ALL\b',code[fact.end():],re.I)
    fact_end=fact.end()+fact_union.start() if fact_union else fact.end()
    plan_start=re.search(r'\bINTO\s+#plan\b',code,re.I)
    plan_union=re.search(r'\bUNION\s+ALL\b',code[plan_start.end():],re.I) if plan_start else None
    plan_end=plan_start.end()+plan_union.start() if plan_union else len(code)
    if (not plan_start
            or not any(fact.start()<match.start()<fact_end for match in joins)
            or not any(plan_start.end()<match.start()<plan_end for match in joins)):
        raise ValidationError('Не найдены соединения коэффициентов факта и плана для регионального расчёта. Найдено: '+str(len(joins)))
    for match in reversed(joins):
        original=source[match.start('relation'):match.end('relation')]
        relation='(SELECT '+native+' FROM '+original+' WHERE Subdivision NOT IN ('+REGION_SQL+") OR LTRIM(RTRIM(TypeCoeff))<>N'Политики' OR Subdivision IS NULL OR TypeCoeff IS NULL UNION ALL SELECT "+frozen+' FROM #share_region_coefficients)'
        source=source[:match.start('relation')]+relation+source[match.end('relation'):]
    # Native percent-plan rows must not replicate the unsplit regional plan.
    join=re.search(r'LEFT\s+JOIN\s+#percent_plan_menger\s+pm\s+ON',mask_sql(source),re.I)
    if not join:raise ValidationError('Не найдено распределение плана политиков.')
    source=source[:join.end()]+' s.SubdivisionName NOT IN ('+REGION_SQL+') AND '+source[join.end():]
    # Check every regional transaction against the approved matrix before defaults.
    at=re.search(r';\s*WITH\s+fact\s+AS\s*\(',mask_sql(source),re.I).start()
    validation='''
IF EXISTS(SELECT 1 FROM #share_transformed s JOIN dbo.Subdivisions b ON b.SubdivisionKey=s.Subdivision
 JOIN dbo.Product p ON p.ProductCode=s.ProductCode AND p.[Тип номенклатуры]=N'Товар'
 WHERE b.SubdivisionName IN ('''+REGION_SQL+''') AND EXISTS(SELECT 1 FROM dbo.Customer x WHERE x.CustomerKey=s.CustomerKey)
 AND (SELECT COUNT(*) FROM #share_region_coefficients c WHERE c.Subdivision=b.SubdivisionName
 AND s.ClosedDealDate BETWEEN c.DateFrom AND c.DateTo AND c.PGSales=p.PlanningGroupSalesErp
 AND c.Group_=CASE WHEN p.PlanningGroupSalesErp IN(N'1. RAC',N'2. VRF',N'3. PROF') THEN p.GroupERP ELSE N'*' END
 AND c.Brand=CASE WHEN p.GroupERP IN(N'Бытовые кондиционеры',N'Полупромышленные кондиционеры',N'Системы мульти-сплит',N'Системы VRF') THEN p.BrandName ELSE N'' END)>1)
 THROW 50083,N'Для факта Ростова/Питера найдены повторяющиеся утверждённые настройки коэффициентов.',1;
'''
    source=source[:at]+WEIGHTS_SQL+validation+source[at:]
    # Split after #plan is built, before either public-style temporary output.
    point=re.search(r"IF\s+OBJECT_ID\(N?'tempdb\.\.#share_plan_fact'\)",source,re.I)
    if not point:raise ValidationError('Не найдена граница сохранения план-факта.')
    source=source[:point.start()]+SPLIT_SQL+source[point.start():]
    return triple_gates(fallback_formulas(source))


DEPARTMENT_GATES_SQL='''
SELECT p.[Подразделение],p.[Год],w.Period_,
 COALESCE(SUM(p.[СуммаОтгрузокUSD])/NULLIF(SUM(p.[СуммаПланUSD]),0),0) AS Execution_
INTO #share_region_department FROM #share_plan_fact p
JOIN (VALUES(2,1),(2,2),(3,3),(4,1),(4,2),(4,3),(4,4),(5,1),(5,2),(5,3),(5,4)) w(Period_,Quarter_)
 ON p.[Квартал]=w.Quarter_
WHERE p.[Подразделение] IN ('''+REGION_SQL+''') AND LTRIM(RTRIM(p.[ТипКоэф]))=N'Продажи'
GROUP BY p.[Подразделение],p.[Год],w.Period_;
'''


def triple_failure(period):
    """Whole branch AND employee total AND configured formula must all pass."""
    if str(period) not in ('2','3','4','5'):raise ValueError('Q1 is unconditional')
    department="COALESCE((SELECT MAX(d.Execution_) FROM #share_region_department d WHERE d.[Подразделение]=#pf_base.[Подразделение] AND d.[Год]=#pf_base.[Год] AND d.Period_="+str(period)+'),0)'
    def ratio(extra=()):
        window='PARTITION BY '+','.join(('Подразделение','АвторСделки','Год','ТипКоэф',*extra))
        return 'COALESCE(SUM(СуммаОтгрузокUSD) OVER('+window+')/NULLIF(SUM(СуммаПланUSD) OVER('+window+'),0),0)'
    personal=ratio()
    formula="CASE WHEN VariationCalculate=N'Марка(Бренд)' THEN "+ratio(('ГруппаПлановПродаж','ГруппаТоваров','Марка'))
    formula+=" WHEN VariationCalculate=N'Группа товаров' THEN "+ratio(('ГруппаПлановПродаж','ГруппаТоваров'))
    formula+=" WHEN VariationCalculate=N'Группа планов продаж' THEN "+ratio(('ГруппаПлановПродаж',))
    formula+=" WHEN VariationCalculate=N'Подразделение' THEN "+personal
    formula+=" WHEN VariationCalculate=N'Подразделение(Глобально)' THEN "+department+' ELSE 0 END'
    return '('+department+'<@PayoutThreshold OR '+personal+'<@PayoutThreshold OR ('+formula+')<@PayoutThreshold)'


def triple_gates(source):
    from .pilot_formula import closing
    labels=list(re.finditer(r"N?'(?P<period>[1-4]\s+Квартал[^']*|Год)'\s+(?:AS\s+)?\[?КварталМотивации\]?",source,re.I))
    if len(labels)!=6:raise ValidationError('Не распознаны периоды тройной проверки.')
    mask=mask_sql(source);starts=[]
    for label in labels:
        selects=list(re.finditer(r'\bSELECT\b',mask[:label.start()],re.I))
        if not selects:raise ValidationError('Не найдена проекция периода тройной проверки.')
        starts.append(selects[-1].start())
    edits=[]
    region="([Подразделение] IN ("+REGION_SQL+") AND LTRIM(RTRIM([ТипКоэф]))=N'Политики')"
    for index in (1,2,3,4):
        start,end=starts[index],starts[index+1]
        found=0
        for function in re.finditer(r'\bIIF\s*\(',mask[start:end],re.I):
            opening=start+function.end()-1;stop=closing(mask,opening)
            if not re.match(r'\s+(?:AS\s+)?\[?СуммаМотивации(?:USD|RUR)УчетПроцента\]?',source[stop+1:],re.I):continue
            depth=0;comma=None
            for at in range(opening+1,stop):
                if mask[at]=='(':depth+=1
                elif mask[at]==')':depth-=1
                elif mask[at]==',' and depth==0:comma=at;break
            if comma is None:raise ValidationError('Не найдена граница условия выплаты.')
            original=source[opening+1:comma]
            replacement='(('+region+' AND '+triple_failure(index+1)+') OR (NOT '+region+' AND ('+original+')))'
            edits.append((opening+1,comma,replacement));found+=1
        if found!=2:raise ValidationError('Не найдены две валюты выплаты для тройной проверки периода '+str(index+1))
    for start,end,value in sorted(edits,reverse=True):source=source[:start]+value+source[end:]
    # Insert after all native base exclusions, before any payout projection.
    source=source[:starts[0]]+DEPARTMENT_GATES_SQL+source[starts[0]:]
    return source


def fallback_formulas(source):
    """Exact formula first, then the approved version's frozen fallback."""
    from .pilot_formula import closing
    code=mask_sql(source)
    pattern=r'\bCOALESCE\s*\(\s*\[?sc\]?\s*\.\s*\[?VariationCalculate\]?\s*,'
    matches=list(re.finditer(pattern,code,re.I));edits=[]
    for index,match in enumerate(matches):
        end=closing(code,code.index('(',match.start()))
        stop=matches[index+1].start() if index+1<len(matches) else len(source)
        date_match=re.search(r'(\[?\w+\]?\s*\.\s*\[?\w+\]?)\s*(?:BETWEEN|>=)\s*\[?sc\]?\s*\.\s*\[?DateFrom\]?' ,code[end:stop],re.I)
        if not date_match:raise ValidationError('Не найдена дата формулы по умолчанию региона.')
        date_=date_match[1];branch='sub.SubdivisionName' if 'closeddealdate' in date_.casefold() else 's.SubdivisionName'
        fallback='CASE WHEN '+branch+' IN ('+REGION_SQL+') THEN (SELECT TOP(1) DefaultFormula FROM #share_region_defaults WHERE Date_<='+date_+' ORDER BY Date_ DESC) END,'
        edits.append((match.end(),fallback))
    if len(edits)<4:raise ValidationError('Не найдены формулы факта и плана для регионов.')
    for point,value in reversed(edits):source=source[:point]+value+source[point:]
    return source


def performance_sql(source):
    """A regional global formula uses the same whole-branch base as its gate."""
    source=source.replace("LTRIM(RTRIM(pf.[ТипКоэф]))=N'Политики'", "LTRIM(RTRIM(pf.[ТипКоэф]))=CASE WHEN pf.[Подразделение] IN ("+REGION_SQL+") THEN N'Продажи' ELSE N'Политики' END")
    from .motivation_exclusions import sql_allowed
    condition=sql_allowed('Политики',group_expression='pf.[ГруппаПлановПродаж]',channel_expression='pf.[Канал]')
    return source.replace(condition,'(pf.[Подразделение] IN ('+REGION_SQL+') OR ('+condition+'))')
