"""Keep calculation-specific enrichment off the shared warehouse Sales table."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql
from .calculation_performance import MARKER as PREPARATION_MARKER
from .calculation_scope import VIEW as SUBDIVISION_VIEW

MARKER = '-- Portal motivation: enrich a private sales working set.'
WORKING_TABLE = '#portal_motivation_sales'
SALES = r'(?:(?:\[?DataWH\]?)\s*\.\s*)?\[?dbo\]?\s*\.\s*\[?Sales\]?(?!\w)'


def expand_preparation(definition, cursor):
    """Inline the supported parameterless helper before isolating its writes."""
    mask = mask_sql(definition)
    name = r'(?:(?:\[?DataWH\]?)\s*\.\s*)?\[?motivation\]?\s*\.\s*\[?sp_PrepareSales\]?'
    calls = list(re.finditer(r'\bEXEC(?:UTE)?\s+' + name + r'\s*;', mask, re.I))
    if not calls:
        return definition
    if len(calls) != 1 or cursor is None:
        raise ValidationError('Не удалось прочитать подготовку motivation.sp_PrepareSales для изолированного расчёта.')
    cursor.execute("SELECT OBJECT_DEFINITION(OBJECT_ID(N'motivation.sp_PrepareSales'))")
    row = cursor.fetchone()
    source = row[0] if row else None
    if not isinstance(source, str) or not source.strip():
        raise ValidationError('Нет определения motivation.sp_PrepareSales или права VIEW DEFINITION. Расчёт отменён.')
    header = re.match(r'\s*(?:CREATE\s+(?:OR\s+ALTER\s+)?|ALTER\s+)PROC(?:EDURE)?\s+' + name + r'\s+AS\b',
                      mask_sql(source), re.I)
    if header is None:
        raise ValidationError('Не распознана процедура sp_PrepareSales без параметров. Расчёт отменён.')
    body = source[header.end():]
    body_mask = mask_sql(body)
    wrapper = re.match(r'\s*BEGIN\b(?!\s+(?:TRY|CATCH|TRAN))', body_mask, re.I)
    wrapper_end = re.search(r'\bEND\s*;?\s*$', body_mask, re.I)
    if wrapper and wrapper_end:
        body = body[wrapper.end():wrapper_end.start()]
    if re.search(r'\b(?:EXEC(?:UTE)?|RETURN|BEGIN\s+TRAN(?:SACTION)?|COMMIT|ROLLBACK)\b', mask_sql(body), re.I):
        raise ValidationError('Неподдерживаемое управление выполнением в sp_PrepareSales. Расчёт отменён.')
    call = calls[0]
    expanded = definition[:call.start()] + '\n' + body + '\n' + definition[call.end():]
    from .calculation_performance import patch as optimize
    return optimize(expanded)


def patch(definition, cursor=None):
    if MARKER in definition:
        return definition
    definition = expand_preparation(definition, cursor)
    mask = mask_sql(definition)
    if re.search(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+\[?Sales\]?(?!\w)', mask, re.I):
        raise ValidationError('Не распознана схема таблицы Sales. Изолированный расчёт не запущен.')
    references = list(re.finditer(SALES, mask, re.I))
    if not references:
        return definition
    fact = re.search(r';\s*WITH\s+fact\s+AS\s*\(', mask, re.I)
    updates = list(re.finditer(r'\bUPDATE\s+s\s+SET\b', mask[:fact.start()] if fact else '', re.I))
    materialize = re.search(r'\bINTO\s+#document_sales\b', mask, re.I)
    if not re.search(r'@FactYear\b', mask, re.I) or fact is None or materialize is None or len(updates) != 2:
        raise ValidationError('Не распознана подготовка продаж для изолированного расчёта. Общая таблица Sales не изменена; запуск отменён.')
    # Recognize the two original enrichment statements, rather than silently
    # redirecting arbitrary warehouse writes into a temporary table.
    spans = [(updates[0].start(), updates[1].start()), (updates[1].start(), fact.start())]
    for (start, stop), expected in zip(spans, ('#document_sales', 'vPriceHistoryERP')):
        segment = mask[start:stop]
        if expected.casefold() not in segment.casefold() or not re.search(r'\bFROM\s+' + SALES + r'\s+s\b', segment, re.I):
            raise ValidationError('Не распознаны обновления атрибутов Sales. Изолированный расчёт не запущен.')
    for reference in references:
        if not re.search(r'\b(?:FROM|JOIN)\s*$', mask[:reference.start()], re.I):
            raise ValidationError('Неподдерживаемое обращение к Sales в расчёте. Требуется проверка совместимости.')
    selects = list(re.finditer(r'\bSELECT\b', mask[:materialize.start()], re.I))
    if not selects:
        raise ValidationError('Не найдена граница подготовки документов расчёта.')
    point = definition.index(PREPARATION_MARKER) if PREPARATION_MARKER in definition else selects[-1].start()
    if any(reference.start() < point for reference in references):
        raise ValidationError('Продажи читаются до подготовки рабочей копии. Изолированный расчёт не запущен.')
    setup = f'''{MARKER}
IF OBJECT_ID('tempdb..{WORKING_TABLE}') IS NOT NULL DROP TABLE {WORKING_TABLE};
SELECT * INTO {WORKING_TABLE}
FROM DataWH.dbo.Sales
WHERE ClosedDealDate>=DATEFROMPARTS(@FactYear,1,1) AND YEAR(ClosedDealDate)=@FactYear
AND Subdivision IN (SELECT SubdivisionKey FROM {SUBDIVISION_VIEW});
'''
    edits = [(reference.start(), reference.end(), WORKING_TABLE) for reference in references]
    edits.append((point, point, setup))
    for start, stop, value in sorted(edits, reverse=True):
        definition = definition[:start] + value + definition[stop:]
    return definition
