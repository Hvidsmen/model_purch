"""Keep calculation-specific enrichment off the shared warehouse Sales table."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql
from .calculation_performance import MARKER as PREPARATION_MARKER

MARKER = '-- Portal motivation: enrich a private sales working set.'
WORKING_TABLE = '#portal_motivation_sales'
SALES = r'(?:(?:\[?DataWH\]?)\s*\.\s*)?\[?dbo\]?\s*\.\s*\[?Sales\]?(?!\w)'


def patch(definition):
    if MARKER in definition:
        return definition
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
WHERE ClosedDealDate>=DATEFROMPARTS(@FactYear,1,1) AND YEAR(ClosedDealDate)=@FactYear;
'''
    edits = [(reference.start(), reference.end(), WORKING_TABLE) for reference in references]
    edits.append((point, point, setup))
    for start, stop, value in sorted(edits, reverse=True):
        definition = definition[:start] + value + definition[stop:]
    return definition
