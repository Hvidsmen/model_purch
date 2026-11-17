"""Private raw RUB shipment base, independent of the sales coefficient."""
from django.core.exceptions import ValidationError
from .subdivision_scope import resolve_scope_sql
from .recalculation_2025_plan import ELIGIBLE

SHIPMENT='(s.Price - s.AmountUSDIt_ITW + s.BallsOuterUSD)'
RUB=SHIPMENT+'*s.PriceRUR/IIF(s.Price=0,1,s.Price)'


def add_rub_shipments(source):
    # Do not divide AmountRURMotive by a sales coefficient: that coefficient
    # can be zero, and director rates are independent of the sales rate.
    if source.count('s.AmountExwAEUSD')!=2 or source.count('SUM(AmountExwAEUSD) AmountExwAEUSD')!=1:
        raise ValidationError('Не распознана рублёвая база сделок в шаблоне расчёта 2025.')
    source=source.replace('s.AmountExwAEUSD',f'''s.AmountExwAEUSD,
 {RUB} AS PortalShipmentRUB,
 CASE WHEN {SHIPMENT} IS NULL OR ({SHIPMENT}<>0 AND (s.PriceRUR IS NULL OR s.PriceRUR=0))
 THEN 1 ELSE 0 END AS PortalMissingRUB''')
    source=source.replace('SUM(AmountExwAEUSD) AmountExwAEUSD',
        'SUM(AmountExwAEUSD) AmountExwAEUSD, SUM(PortalShipmentRUB) AS СуммаОтгрузокRUB, SUM(PortalMissingRUB) AS PortalMissingRUB')
    source+='''
IF EXISTS(SELECT 1 FROM #iteration_fact WHERE PortalMissingRUB>0 AND '''+resolve_scope_sql(ELIGIBLE)+''')
 THROW 50025,N'Для расчёта RUB в факте есть сделки с пустой суммой либо нулевым/пустым PriceRUR. Обновите подготовленный факт после загрузки Sales.',1;
'''
    return source
