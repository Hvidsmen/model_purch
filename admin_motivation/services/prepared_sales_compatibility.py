"""Allow only the reviewed legacy preparation on proven floating Discount."""
import re
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql

DOCUMENT_GUARD="""IF EXISTS (SELECT 1 FROM #document_sales GROUP BY DocumentKey, Номенклатура HAVING COUNT_BIG(*) > 1)
    THROW 50005, N'Неоднозначное соответствие документа и товара.', 1;
CREATE INDEX IX_document_sales_match ON #document_sales(DocumentKey, Номенклатура);"""

# The warehouse helper predates defensive NULLIF calls and the duplicate guard.
# Keep this whole CASE allowlisted: do not strip NULLIF or change 100 generically.
LEGACY_CASE="""CASE
 WHEN s.ВидЦены != '' and (1-s.Discount/100) =0 THEN s.Discount
 WHEN s.ВидЦены != '' and (s.Price - AmountUSDIt_ITW)/(1-s.Discount/100) =0 THEN s.Discount
 WHEN s.ВидЦены != '' and (s.Price - AmountUSDIt_ITW) =0 THEN s.Discount
 WHEN s.ВидЦены != '' AND s.Price - AmountUSDIt_ITW = 0 THEN s.Discount
 WHEN s.ВидЦены = '' AND (phe.Price*DataWH.dbo.getCurrancyRateUSD(DateInvoice, phe.CurrencyCode)*Quantity) = 0 THEN s.Discount
 WHEN s.ВидЦены != '' THEN (1-(s.Price - AmountUSDIt_ITW+ s.BallsOuterUSD)/((s.Price - AmountUSDIt_ITW)/(1-s.Discount/100)))*100
 WHEN s.ВидЦены = '' AND phe.Price IS NOT NULL THEN (1-(s.Price - AmountUSDIt_ITW+ s.BallsOuterUSD)/(phe.Price*DataWH.dbo.getCurrancyRateUSD(DateInvoice, phe.CurrencyCode)*Quantity))*100
 ELSE s.Discount END"""


def discount_case(sql):
    return re.search(r'\bSET\s+s\.\[?DiscountMotivation\]?\s*=\s*(?P<case>CASE\b[\s\S]*?\bEND\b)',mask_sql(sql),re.I)


def compatible(body,expected,cursor=None):
    from .prepared_sales import signature
    # Missing helper guard is safe: the published producer enforces its own
    # stronger deduplication/conflict validation before any consumer can run.
    candidates=[signature(expected)]
    if DOCUMENT_GUARD in expected:candidates.append(signature(expected.replace(DOCUMENT_GUARD,'')))
    if signature(body) in candidates:return True
    legacy,canonical=discount_case(body),discount_case(expected)
    if not legacy or not canonical:return False
    if signature(body[legacy.start('case'):legacy.end('case')])!=signature(LEGACY_CASE):return False
    rewritten=body[:legacy.start('case')]+expected[canonical.start('case'):canonical.end('case')]+body[legacy.end('case'):]
    if signature(rewritten) not in candidates:return False
    if cursor is None:
        raise ValidationError('Для совместимости прежней подготовки скидок нужно проверить тип dbo.Sales.Discount. Расчёт не изменён.')
    cursor.execute("SELECT system_type_id FROM sys.columns WHERE object_id=OBJECT_ID(N'dbo.Sales') AND name=N'Discount'")
    row=cursor.fetchone()
    # real/float outrank both integer and decimal literal operands, so 100 and
    # 100.0 become the same floating value. No integer/money/decimal assumption.
    if not row or row[0] not in (59,62):
        raise ValidationError('Прежняя формула скидок совместима с общим набором только для Discount типа real/float. Тип не подтверждён; расчёт не изменён.')
    return True
