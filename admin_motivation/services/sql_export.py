"""Export the approved timeline using each version's original effective date."""
from django.core.exceptions import ValidationError
from ..models import GlobalCoeffVersion, SubdivisionCoeff, SubdivisionManagerCoeff, Goods


def export_motivation(connector):
    versions = list(GlobalCoeffVersion.objects.filter(status='approved').order_by('effective_from', 'pk'))
    if not versions:
        raise ValidationError('Нет утверждённых версий для выгрузки. Сначала утвердите версию коэффициентов.')
    start_date = versions[0].effective_from
    connection = None
    try:
        connection, cursor = connector('vm-dwh', 'DataWH')
        # Parameters never become SQL text. The replacement and the full timeline are atomic.
        for table in ['SubdivisionManagerCoeff', 'SubdivisionMotiveCoeff', 'GoodsMotivation']:
            cursor.execute(f'DELETE FROM DataWH.motivation.{table} WHERE Date_ >= ?', start_date)
        for version in versions:
            date_from = version.effective_from
            managers = SubdivisionManagerCoeff.objects.filter(version=version).select_related('subdivision', 'kind')
            manager_rows = [(date_from, row.subdivision.subdivision_key, row.kind.name if row.kind else None, row.coeff) for row in managers]
            if manager_rows:
                cursor.executemany('INSERT INTO DataWH.motivation.SubdivisionManagerCoeff (Date_, Subdivision, Kind_, Coeff_) VALUES (?, ?, ?, ?)', manager_rows)
            coeffs = list(SubdivisionCoeff.objects.filter(version=version).select_related('subdivision', 'goods', 'type_coeff', 'segment', 'variation_calculate'))
            coefficient_rows = [(date_from, row.subdivision.subdivision_key, row.goods.goods_key, row.type_coeff.type_coeff_name,
                                 row.segment.segment_name, row.variation_calculate.variation_name, row.motivation_coeff) for row in coeffs]
            if coefficient_rows:
                cursor.executemany('INSERT INTO DataWH.motivation.SubdivisionMotiveCoeff (Date_, Subdivision, GoodsKey, TypeCoeff, Segment, VariationCalculate, Coeff_) VALUES (?, ?, ?, ?, ?, ?, ?)', coefficient_rows)
            good_ids = {row.goods_id for row in coeffs} | set(version.coefficients.values_list('goods_id', flat=True))
            good_rows = [(date_from, good.goods_key, good.planning_group_sales, good.group, good.brand) for good in Goods.objects.filter(pk__in=good_ids)]
            if good_rows:
                cursor.executemany('INSERT INTO DataWH.motivation.GoodsMotivation (Date_, GoodsKey, PGSales, Group_, Brand) VALUES (?, ?, ?, ?, ?)', good_rows)
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()
    return versions
