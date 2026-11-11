"""Read-only column preflight for the fixed director-comparison SQL sources."""
from django.core.exceptions import ValidationError

COLUMNS={
 'motivation.FactTest2025':('Подразделение','ПодразделениеДокумент','Год','Квартал',
    'ГруппаПлановПродаж','ГруппаТоваров','Марка','СегментСкидки','СуммаОтгрузокUSD','ТипКоэф'),
 'motivation.PortalPreparedSales':('Subdivision','ProductCode','CustomerKey','ClosedDealDate','DocumentNumber',
    'DateInvoice','DiscountMotivation','ДаичиТипСкидкиНаДату','ВидЦены',
    'даичи_ВидЦеныВходнаяСтоимостьНаименование','Price','AmountUSDIt_ITW','BallsOuterUSD'),
 'dbo.Subdivisions':('SubdivisionKey','SubdivisionName','Chanel'),
 'dbo.Product':('ProductCode','PlanningGroupSalesErp','GroupERP','BrandName','ModelCode','Тип номенклатуры'),
 'dbo.Customer':('CustomerKey','CustomerKeyName'),
}


def require_columns(cursor,tables):
    # Table names come only from this allowlist, and are still bound values.
    if any(name not in COLUMNS for name in tables):raise ValueError('Unknown director source')
    cursor.execute("SELECT OBJECT_SCHEMA_NAME(c.object_id),OBJECT_NAME(c.object_id),c.name FROM sys.columns c WHERE c.object_id IN ("+
        ','.join('OBJECT_ID(?)' for _ in tables)+')',*tables)
    actual={(f'{schema}.{table}'.casefold(),column.casefold()) for schema,table,column in cursor.fetchall()}
    missing=[f'{name}.{column}' for name in tables for column in COLUMNS[name]
        if (name.casefold(),column.casefold()) not in actual]
    if missing:
        raise ValidationError('Не найдены поля источников сравнения: '+', '.join(missing)+'. Расчёт не выполнен.')
