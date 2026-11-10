"""Build a private 2025 calculation from a versioned portal SQL template.

No coefficient export, current employee roster or subdivision merge is used.
The live warehouse procedure is never read or modified.
"""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql, closing

PROC = 'motivation.PortalCalcTest2025'
TABLES = ('Fact', 'PlanFact', 'ReportMotivationByDealAuthor', 'AnnualAccrual')

SETUP = """
IF @FactYear<>2025 THROW 50025,N'Этот вариант предназначен только для 2025 года',1;
DECLARE @Test2025Xml xml=TRY_CONVERT(xml,@Test2025Coefficients);
IF @Test2025Xml IS NULL
 THROW 50025,N'Не удалось прочитать снимок коэффициентов 2025. Повторите подготовку задания',1;
SELECT r.record.value('(planning/text())[1]','nvarchar(255)') AS PGSales,
 r.record.value('(goods/text())[1]','nvarchar(255)') AS Group_,
 COALESCE(r.record.value('(brand/text())[1]','nvarchar(255)'),N'') AS Brand,
 r.record.value('(kind/text())[1]','nvarchar(30)') AS TypeCoeff,
 CAST('20250101' AS date) AS DateFrom,CAST('20251231' AS date) AS DateTo,
 CAST(NULL AS nvarchar(64)) AS VariationCalculate,
 r.record.value('(k0/text())[1]','decimal(12,6)') AS K0,
 r.record.value('(k1/text())[1]','decimal(12,6)') AS K1,
 r.record.value('(k2/text())[1]','decimal(12,6)') AS K2,
 r.record.value('(k3/text())[1]','decimal(12,6)') AS K3,
 r.record.value('(k4/text())[1]','decimal(12,6)') AS K4
INTO #test2025_rates
FROM @Test2025Xml.nodes('/coefficients/coefficient') AS r(record);
IF NOT EXISTS(SELECT 1 FROM #test2025_rates)
 THROW 50025,N'Не загружены коэффициенты 2025 года',1;
IF EXISTS(SELECT 1 FROM #test2025_rates GROUP BY PGSales,Group_,Brand,TypeCoeff HAVING COUNT(*)>1)
 THROW 50025,N'Повторное товарное сочетание коэффициентов',1;
SELECT rates.*,sub.SubdivisionName AS Subdivision
INTO #test2025_coeff FROM #test2025_rates rates
CROSS JOIN (SELECT DISTINCT SubdivisionName FROM dbo.Subdivisions) sub;
CREATE INDEX IX_test2025_coeff ON #test2025_coeff(PGSales,TypeCoeff);
"""


# Read the historical formula view once, before joining millions of sales rows.
# Keep original intervals and scalar DISTINCT semantics: conflicting formulas
# still fail rather than silently picking MAX/TOP(1) and changing eligibility.
FORMULA_SNAPSHOT = """
SELECT DISTINCT old.DateFrom,old.DateTo,c.PGSales,c.Group_,c.Brand,c.TypeCoeff,
 c.Subdivision,old.VariationCalculate
INTO #test2025_formulas
FROM DataWH.motivation.vSubdivisionMotiveCoeffTo old
INNER JOIN #test2025_coeff c ON old.TypeCoeff=c.TypeCoeff
 AND old.Subdivision=c.Subdivision AND old.PGSales=c.PGSales AND old.Group_=c.Group_
 AND COALESCE(NULLIF(old.Brand,N'*'),N'')=COALESCE(NULLIF(c.Brand,N'*'),N'')
WHERE old.DateFrom<'20260101' AND old.DateTo>='20250101';
CREATE INDEX IX_test2025_formulas ON #test2025_formulas(PGSales,TypeCoeff,DateFrom,DateTo)
 INCLUDE(Subdivision,Group_,Brand,VariationCalculate);
"""

ALLOCATION = """
-- Annual 2025 shares, separately by document subdivision and coefficient goods key.
IF EXISTS(SELECT 1 FROM #fact WHERE КoэфMissing=1)
 THROW 50025,N'Есть факт без полного набора K0–K4 из файла',1;
SELECT t.*,AmountUSD/NULLIF(SUM(AmountUSD) OVER
 (PARTITION BY ПодразделениеДокумент,ГруппаПлановПродаж,ГруппаТоваров,Марка),0) AS Percent_
INTO #percent_plan_menger
FROM (SELECT ПодразделениеДокумент,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка,
 SUM(СуммаОтгрузокUSD) AS AmountUSD FROM #fact
 WHERE LTRIM(RTRIM(ТипКоэф))=N'Политики' AND Год=2025
 GROUP BY ПодразделениеДокумент,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка
 HAVING SUM(СуммаОтгрузокUSD)<>0) t;
IF EXISTS(SELECT 1 FROM #percent_plan_menger WHERE Percent_ IS NULL)
 THROW 50025,N'Нулевой общий факт товарного сочетания: план нельзя распределить',1;
"""
# No hidden fallback when a file does not cover a fact/plan classification.
ALLOCATION = ALLOCATION.replace('КoэфMissing=1', 'КоэфициентМотивации IS NULL OR K0 IS NULL OR K1 IS NULL OR K2 IS NULL OR K3 IS NULL OR K4 IS NULL')


def patch_source(source, existing=False):
    code = mask_sql(source)
    if not all(re.search(r'@' + name + r'\b', code, re.I) for name in ('FactYear', 'PlanVersion', 'QuarterShare', 'YearShare')):
        raise ValidationError('В SQL-шаблоне пересчёта 2025 отсутствуют обязательные параметры: год, версия плана и доли.')
    if any(len(re.findall(r'@' + name + r'\b', code, re.I)) < 2 for name in ('QuarterShare', 'YearShare')):
        raise ValidationError('В исходной процедуре доли должны использоваться в формулах начисления.')
    if re.search(r'#director_employees|PortalSubdivisionMerge|Portal pilot premiums', source, re.I):
        raise ValidationError('В SQL-шаблоне пересчёта 2025 обнаружена зависимость от переходных правил или текущего состава директоров.')
    header = re.search(r'\b(?:CREATE\s+(?:OR\s+ALTER\s+)?|ALTER\s+)PROC(?:EDURE)?\s+(?:\[?motivation\]?\s*\.\s*)?\[?sp_CalcBySub\]?\b', code, re.I)
    # mask retains bracketed names; the trailing word boundary is before a closing ].
    if not header:
        raise ValidationError('Не распознан заголовок sp_CalcBySub.')
    # Include an optional closing bracket, which a word boundary can leave behind.
    end = header.end() + (1 if source[header.end():].startswith(']') else 0)
    source = source[:header.start()] + ('ALTER' if existing else 'CREATE') + ' PROCEDURE ' + PROC + source[end:]
    code = mask_sql(source)
    body = re.search(r'\bAS\b', code[header.start():], re.I)
    if not body:
        raise ValidationError('Не найдена граница параметров процедуры.')
    at = header.start() + body.start()
    source = source[:at] + '\n, @Test2025Coefficients nvarchar(max)\n' + source[at:]
    # Insert setup after the procedure BEGIN, or after AS for nonwrapped sources.
    code = mask_sql(source)
    as_match = re.search(r'\bAS\b', code[header.start():], re.I)
    at = header.start() + as_match.end()
    begin = re.match(r'\s*BEGIN\b(?!\s+(?:TRY|TRAN|CATCH))', code[at:], re.I)
    at += begin.end() if begin else 0
    source = source[:at] + '\n' + SETUP + source[at:]
    source, references = re.subn(r'(?:(?:\[?DataWH\]?)\s*\.\s*)?\[?motivation\]?\s*\.\s*\[?vSubdivisionMotiveCoeffTo\]?(?!\w)', '#test2025_coeff', source, flags=re.I)
    if references not in (4, 6, 8):
        raise ValidationError('Не распознаны соединения ставок факта и плана 2025 года.')
    # Match the file's goods scopes, including wildcard rows, without multiplying sales.
    joins = list(re.finditer(r"LEFT\s+JOIN\s+#test2025_coeff\s+sc\s+ON[\s\S]*?AND\s+(?P<sub>sub|s)\.SubdivisionName\s*=\s*sc\.Subdivision", source, re.I))
    if len(joins) != references:
        raise ValidationError('Не распознаны границы товарных соединений коэффициентов.')
    for join in reversed(joins):
        text = join[0]
        kind = re.search(r"TRIM\(sc\.TypeCoeff\)\s*=\s*N?'(Политики|Продажи)'", text, re.I)
        alias = 'p' if re.search(r'\bp\.PlanningGroupSalesErp', text, re.I) else 't'
        date = 's.ClosedDealDate' if alias == 'p' else 't.Date_'
        brand = 'p.BrandName' if alias == 'p' else 't.[Марка(Бренд)]'
        if not kind:
            raise ValidationError('Не распознан вид коэффициента в соединении.')
        replacement = f"""OUTER APPLY (SELECT TOP(1) c.PGSales,c.Group_,c.Brand,c.TypeCoeff,c.DateFrom,c.DateTo,c.Subdivision,
 c.K0,c.K1,c.K2,c.K3,c.K4,
 (SELECT DISTINCT old.VariationCalculate FROM #test2025_formulas old
 WHERE {date} BETWEEN old.DateFrom AND old.DateTo AND old.TypeCoeff=c.TypeCoeff
 AND old.Subdivision=c.Subdivision AND old.PGSales=c.PGSales AND old.Group_=c.Group_
 AND COALESCE(NULLIF(old.Brand,N'*'),N'')=COALESCE(NULLIF(c.Brand,N'*'),N'')) AS VariationCalculate
 FROM #test2025_coeff c
 WHERE {date} BETWEEN c.DateFrom AND c.DateTo AND c.TypeCoeff=N'{kind[1]}'
 AND c.Subdivision={join['sub']}.SubdivisionName AND c.PGSales={alias}.PlanningGroupSalesERP
 AND (c.Group_=N'*' OR c.Group_={alias}.GroupERP)
 AND (c.Brand IN (N'*',N'') OR c.Brand={brand})
 ORDER BY CASE WHEN c.Group_=N'*' THEN 0 ELSE 1 END DESC,
 CASE WHEN c.Brand IN (N'*',N'') THEN 0 ELSE 1 END DESC) sc"""
        source = source[:join.start()] + replacement + source[join.end():]
    # Use the matched coefficient keys in both fact and plan allocation dimensions.
    source, fact_groups = re.subn(r"CASE\s+WHEN\s+p\.PlanningGroupSalesErp\s+IN\s*\([^)]*\)\s+THEN\s+p\.GroupERP\s+ELSE\s+'[^']*'\s+END\s+GroupERP", 'sc.Group_ GroupERP', source, flags=re.I)
    source, fact_brands = re.subn(r"CASE\s+WHEN\s+p\.GroupERP\s+IN\s*\([^)]*\)\s+THEN\s+BrandName\s+ELSE\s+'[^']*'\s+END\s+\[Марка\(Бренд\)\]", 'sc.Brand [Марка(Бренд)]', source, flags=re.I)
    if fact_groups != 2 or fact_brands != 2:
        raise ValidationError('Не распознаны товарные ключи двух видов факта.')
    source = re.sub(r't\.GroupERP\s*\[ГруппаТоваров\]', 'sc.Group_ [ГруппаТоваров]', source, flags=re.I)
    source = re.sub(r't\.\[Марка\(Бренд\)\]\s*\[Марка\]', 'sc.Brand [Марка]', source, flags=re.I)
    # Match the same wildcard classification when assigning policy plan shares.
    share_joins = list(re.finditer(r'LEFT\s+JOIN\s+#percent_plan_menger\s+pm\s+ON[\s\S]*?AND\s+t\.\[Марка\(Бренд\)\]\s*=\s*pm\.Марка', source, re.I))
    for join in reversed(share_joins):
        # Move the share lookup after sc has been bound by OUTER APPLY.
        following = re.search(r'OUTER APPLY \(SELECT TOP\(1\) c\.PGSales[\s\S]*?\) sc', source[join.end():], re.I)
        if not following:
            raise ValidationError('Не найдена ставка перед распределением плана.')
        end = join.end() + following.end()
        coeff_join = source[join.end():end]
        shares = " LEFT JOIN #percent_plan_menger pm ON s.SubdivisionKey=pm.ПодразделениеДокумент AND sc.PGSales=pm.ГруппаПлановПродаж AND sc.Group_=pm.ГруппаТоваров AND sc.Brand=pm.Марка "
        source = source[:join.start()] + coeff_join + shares + source[end:]
    # The historical deal author comes from the sales document, never the current roster.
    source = re.sub(r"N?'Политики'\s+(?:AS\s+)?АвторСделки", "COALESCE(NULLIF(LTRIM(RTRIM(s.Даичи_АвторСделкиНаименование)),N''),N'Не заполнено') АвторСделки", source, count=1, flags=re.I)
    source = re.sub(r's\.Даичи_АвторСделкиНаименование\s*=\s*ds\.Даичи_АвторСделкиНаименование\b',
                    "s.Даичи_АвторСделкиНаименование=COALESCE(NULLIF(ds.Даичи_АвторСделкиНаименование,N''),s.Даичи_АвторСделкиНаименование)", source, flags=re.I)
    # All rate fallback expressions start with sc.Kn; parse balanced COALESCE calls.
    code = mask_sql(source); edits = []
    for match in re.finditer(r'\bCOALESCE\s*\(\s*sc\.\[?(K[0-4])\]?\s*,', code, re.I):
        stop = closing(code, code.index('(', match.start())) + 1
        edits.append((match.start(), stop, 'sc.' + match[1]))
    for start, stop, value in reversed(edits):
        source = source[:start] + value + source[stop:]
    # Replace only the known annual share SELECT, including its inner GROUP BY.
    code = mask_sql(source)
    into = list(re.finditer(r'\bINTO\s+#percent_plan_menger\b', code, re.I))
    if len(into) != 1:
        raise ValidationError('Не найдена однозначная таблица долей плана политиков.')
    select = list(re.finditer(r'\bSELECT\b', code[:into[0].start()], re.I))[-1]
    outer_from = re.search(r'\bFROM\s*\(', code[into[0].end():], re.I)
    if not outer_from:
        raise ValidationError('Не распознана исходная выборка долей.')
    opening = code.index('(', into[0].end() + outer_from.start())
    end = closing(code, opening) + 1
    alias = re.match(r'\s*(?:AS\s+)?t\b\s*;?', code[end:], re.I)
    if not alias:
        raise ValidationError('Не распознана граница выборки долей.')
    source = source[:select.start()] + ALLOCATION + source[end + alias.end():]
    # Missing plan authors must not silently receive the full plan.
    source = re.sub(r'COALESCE\s*\(\s*\[?АвторСделки\]?\s*,\s*N?\'[^\']*\'\s*\)',
                    "COALESCE([АвторСделки],N'Нет факта для распределения')", source, flags=re.I)
    source = isolate(source)
    code = mask_sql(source)
    boundary = re.search(r'\bSELECT\b[\s\S]*?\bINTO\s+#pf_base\b', code, re.I)
    if not boundary:
        raise ValidationError('Не найдена база расчёта 2025 года.')
    # Locate the SELECT immediately preceding INTO, rather than matching all earlier SELECTs.
    into_pf = re.search(r'\bINTO\s+#pf_base\b', code, re.I)
    point = list(re.finditer(r'\bSELECT\b', code[:into_pf.start()], re.I))[-1].start()
    guard = """
IF EXISTS(SELECT 1 FROM DataWH.motivation.PlanFactTest2025 WHERE [Год]<>2025)
 THROW 50025,N'Выбранный план содержит данные другого года. Нужен план 2025',1;
IF EXISTS(SELECT 1 FROM DataWH.motivation.PlanFactTest2025
 WHERE [СуммаПланUSD]<>0 AND ([АвторСделки]=N'Нет факта для распределения'
 OR K0 IS NULL OR K1 IS NULL OR K2 IS NULL OR K3 IS NULL OR K4 IS NULL))
 THROW 50025,N'Есть план без факта для распределения или без коэффициентов файла',1;
"""
    source = source[:point] + guard + source[point:]
    from .sales_participation import patch as participation
    source = participation(source, policy_heat_all_years=True)
    from .pilot_calculation import normalize_q4_rur
    source = normalize_q4_rur(source)
    # Ensure there is no pilot-period restriction and all output periods exist.
    labels = re.findall(r"N?'([1-4]\s+Квартал[^']*|Год)'\s+(?:AS\s+)?\[?КварталМотивации\]?", source, re.I)
    if ['5' if x.casefold() == 'год' else x[0] for x in labels] != list('123455'):
        raise ValidationError('Не распознаны четыре квартала и годовая часть 2025 года.')
    if source.count(SETUP)!=1:
        raise ValidationError('Не найдена точка однократной загрузки формул 2025.')
    return source.replace(SETUP,SETUP+FORMULA_SNAPSHOT,1)


def isolate(source):
    for table in TABLES:
        pattern = r'(?:(?:\[?DataWH\]?)\s*\.\s*)?\[?motivation\]?\s*\.\s*\[?' + table + r'\]?(?!\w)'
        source = re.sub(pattern, 'DataWH.motivation.' + table + 'Test2025', source, flags=re.I)
    return source


def template_source():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'sql' / 'templates' / 'recalculation_2025.sql.in'
    source = path.read_text(encoding='utf-8')
    if source.count('{{PROCEDURE}}') != 1 or re.search(r'^\s*GO\s*$', source, re.I | re.M):
        raise ValidationError('Повреждён SQL-шаблон пересчёта 2025.')
    return source.replace('{{PROCEDURE}}', '[motivation].[sp_CalcBySub]')


def prepare(cursor):
    # The source can include current directors or 2026-only windows. Its shape
    # must not prevent an independent fact-2025 / plan-2025 calculation.
    cursor.execute("SELECT OBJECT_ID(N'motivation.PortalCalcTest2025')")
    row = cursor.fetchone()
    existing = bool(row and row[0])
    from .formula_defaults import TABLE_SQL, patch_defaults
    cursor.execute(TABLE_SQL)
    original, _ = patch_defaults(template_source(), required=False)
    source = patch_source(original, existing)
    from .payout_threshold import parameterize
    source = parameterize(source)
    from .first_quarter_inputs import patch as first_quarter
    source = first_quarter(source)
    cursor.execute("""SELECT CASE WHEN EXISTS(SELECT 1 FROM motivation.PortalFormulaDefaults
 WHERE DefaultFormula=N'Подразделение(Глобально)' AND Date_<'20260101')
 OR EXISTS(SELECT 1 FROM motivation.vSubdivisionMotiveCoeffTo
 WHERE VariationCalculate=N'Подразделение(Глобально)' AND DateFrom<'20260101' AND DateTo>='20250101')
 THEN 1 ELSE 0 END""")
    if cursor.fetchone()[0]:
        from .pilot_formula import patch_calculation
        source = patch_calculation(source)
    from .calculation_performance import patch as performance
    source = performance(source)
    # This adapter uses the full historical subdivision set, without the Moscow-only view.
    from .calculation_sales_isolation import patch as private_sales
    source = private_sales(source, cursor=cursor).replace('DataWH.motivation.PortalCalculationSubdivisions', 'DataWH.dbo.Subdivisions')
    from .prepared_sales import install as install_prepared_sales, patch as use_prepared_sales
    install_prepared_sales(cursor)
    source = use_prepared_sales(source, historical_author=True)
    # XML methods require this option at procedure creation, in a separate batch.
    cursor.execute('SET QUOTED_IDENTIFIER ON; SET ANSI_NULLS ON;')
    cursor.execute(source)
