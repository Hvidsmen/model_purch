"""Cache date/currency lookups without changing the discount CASE or SQL types."""
import re
from django.core.exceptions import ValidationError

MARKER='-- Portal prepared discount FX cache.'
RATE='DataWH.dbo.getCurrancyRateUSD(DateInvoice, phe.CurrencyCode)'


def patch(body):
    if MARKER in body:return body
    # The producer owns this canonical block. Fail before installing unknown SQL.
    pattern=r'UPDATE s\s+SET s\.DiscountMotivation\s*=[\s\S]+?WHERE s\.ClosedDealDate[^;]+;'
    matches=list(re.finditer(pattern,body))
    if len(matches)!=1:raise ValidationError('Не распознана формула скидки подготовки продаж.')
    match=matches[0];original=match.group()
    relation=re.search(r'FROM\s+(DataWH\.motivation\.PortalPreparedSalesStage) s\s+LEFT JOIN DataWH\.dbo\.vPriceHistoryERP phe\s+(ON[\s\S]+?)\s+WHERE ',original)
    if not relation or original.count(RATE)!=2:
        raise ValidationError('Не распознан подбор курса и исторической цены подготовки продаж.')
    table,condition=relation.groups()
    # Keep all original historical-price predicates, including BETWEEN's
    # inclusive boundaries. Materialize DISTINCT keys before calling the UDF.
    cache=f"""{MARKER}
IF OBJECT_ID('tempdb..#portal_prepared_fx_keys') IS NOT NULL DROP TABLE #portal_prepared_fx_keys;
IF OBJECT_ID('tempdb..#portal_prepared_fx') IS NOT NULL DROP TABLE #portal_prepared_fx;
SELECT DISTINCT s.DateInvoice,phe.CurrencyCode INTO #portal_prepared_fx_keys
FROM {table} s JOIN DataWH.dbo.vPriceHistoryERP phe {condition}
WHERE s.ВидЦены='';
SELECT k.DateInvoice,k.CurrencyCode,
 DataWH.dbo.getCurrancyRateUSD(k.DateInvoice,k.CurrencyCode) AS RateUSD
INTO #portal_prepared_fx FROM #portal_prepared_fx_keys k;
CREATE INDEX IX_portal_prepared_fx_date ON #portal_prepared_fx(DateInvoice);
"""
    updated=original.replace(RATE,'fx.RateUSD')
    updated=updated.replace(condition,condition+" AND s.ВидЦены=''\nLEFT JOIN #portal_prepared_fx fx ON fx.DateInvoice=s.DateInvoice\n AND (fx.CurrencyCode=phe.CurrencyCode OR (fx.CurrencyCode IS NULL AND phe.CurrencyCode IS NULL))",1)
    return body[:match.start()]+cache+updated+body[match.end():]
