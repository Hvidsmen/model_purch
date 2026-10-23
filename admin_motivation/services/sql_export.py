"""Export the approved timeline using each version's original effective date."""
from django.core.exceptions import ValidationError
from ..models import GlobalCoeffVersion, SubdivisionCoeff, SubdivisionManagerCoeff, SubdivisionDirectorCoeff, Goods


DIRECTOR_TYPE = 'Директора'
EMPLOYEE_COLUMNS_SQL = """
IF COL_LENGTH(N'DataWH.motivation.SubdivisionMotiveCoeff', N'EmployeeCode') IS NULL
    ALTER TABLE DataWH.motivation.SubdivisionMotiveCoeff ADD EmployeeCode nvarchar(64) NULL;
IF COL_LENGTH(N'DataWH.motivation.SubdivisionMotiveCoeff', N'EmployeeName') IS NULL
    ALTER TABLE DataWH.motivation.SubdivisionMotiveCoeff ADD EmployeeName nvarchar(255) NULL;
"""
COEFFICIENT_INSERT = ('INSERT INTO DataWH.motivation.SubdivisionMotiveCoeff '
    '(Date_, Subdivision, GoodsKey, TypeCoeff, Segment, VariationCalculate, Coeff_, EmployeeCode, EmployeeName) '
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')


def director_variation(good):
    # Directors have no separate editable variation. Match the calculation's
    # default when vSubdivisionMotiveCoeffTo has no VariationCalculate override.
    return 'Группа планов продаж' if good.planning_group_sales in ('1. RAC', '2. VRF', '3. PROF') else 'Подразделение'


def export_motivation(connector, manage_connection=True):
    versions = list(GlobalCoeffVersion.objects.filter(status='approved').order_by('effective_from', 'pk'))
    if not versions:
        raise ValidationError('Нет утверждённых версий для выгрузки. Сначала утвердите версию коэффициентов.')
    from .discount_segments import export_enabled, INSERT_SQL, FIELDS
    include_segments = export_enabled(versions)
    from .workflow import export_snapshot
    snapshot = export_snapshot()
    start_date = versions[0].effective_from
    connection = None
    try:
        connection, cursor = connector('vm-dwh', 'DataWH')
        cursor.execute("""DECLARE @portalLock int; EXEC @portalLock=sys.sp_getapplock
 @Resource=N'PortalMotivationCalculation',@LockMode=N'Exclusive',@LockOwner=N'Session',@LockTimeout=0;
 IF @portalLock < 0 THROW 50001,N'Дождитесь завершения расчёта перед выгрузкой',1;""")
        cursor.execute(EMPLOYEE_COLUMNS_SQL)
        # Parameters never become SQL text. The replacement and the full timeline are atomic.
        tables = ['SubdivisionManagerCoeff', 'SubdivisionMotiveCoeff', 'GoodsMotivation']
        if include_segments:
            tables.append('SegmentMotivation')
        for table in tables:
            cursor.execute(f'DELETE FROM DataWH.motivation.{table} WHERE Date_ >= ?', start_date)
        for version in versions:
            date_from = version.effective_from
            if include_segments:
                cursor.executemany(INSERT_SQL, [(date_from, row.type_discount, *[getattr(row, field) for field in FIELDS]) for row in version.discount_segments.order_by('type_discount')])
            managers = SubdivisionManagerCoeff.objects.filter(version=version).select_related('subdivision', 'kind')
            manager_rows = [(date_from, row.subdivision.subdivision_key, row.kind.name if row.kind else None, row.coeff) for row in managers]
            from .subdivision_aliases import coefficient_rows as merged_rows
            manager_rows = list(merged_rows(manager_rows, 1, range(3)))
            if manager_rows:
                cursor.executemany('INSERT INTO DataWH.motivation.SubdivisionManagerCoeff (Date_, Subdivision, Kind_, Coeff_) VALUES (?, ?, ?, ?)', manager_rows)
            coeffs = list(SubdivisionCoeff.objects.filter(version=version).select_related('subdivision', 'goods', 'type_coeff', 'segment', 'variation_calculate'))
            coefficient_rows = [(date_from, row.subdivision.subdivision_key, row.goods.goods_key, row.type_coeff.type_coeff_name,
                                 row.segment.segment_name, row.variation_calculate.variation_name, row.motivation_coeff, None, None) for row in coeffs]
            directors = list(SubdivisionDirectorCoeff.objects.filter(version=version).select_related('subdivision', 'goods', 'employee', 'variation_calculate').order_by('subdivision_id', 'employee_id', 'goods_id'))
            coefficient_rows.extend(
                (date_from, row.subdivision.subdivision_key, row.goods.goods_key, DIRECTOR_TYPE,
                 f'K{i}', (row.variation_calculate.variation_name if row.variation_calculate_id else director_variation(row.goods)), getattr(row, f'k{i}'),
                 row.employee.employee_code, row.employee.full_name)
                for row in directors for i in range(5))
            coefficient_rows = list(merged_rows(coefficient_rows, 1, [0, 1, 2, 3, 4, 7]))
            if coefficient_rows:
                cursor.executemany(COEFFICIENT_INSERT, coefficient_rows)
            good_ids = {row.goods_id for row in coeffs} | {row.goods_id for row in directors} | set(version.coefficients.values_list('goods_id', flat=True))
            good_rows = [(date_from, good.goods_key, good.planning_group_sales, good.group, good.brand) for good in Goods.objects.filter(pk__in=good_ids)]
            if good_rows:
                cursor.executemany('INSERT INTO DataWH.motivation.GoodsMotivation (Date_, GoodsKey, PGSales, Group_, Brand) VALUES (?, ?, ?, ?, ?)', good_rows)
        if manage_connection:
            from .pilot_formula import GLOBAL_SUBDIVISION
            if (SubdivisionCoeff.objects.filter(version__in=versions, variation_calculate__variation_name=GLOBAL_SUBDIVISION).exists()
                    or SubdivisionDirectorCoeff.objects.filter(version__in=versions, variation_calculate__variation_name=GLOBAL_SUBDIVISION).exists()
                    or SubdivisionManagerCoeff.objects.filter(version__in=versions).exists()):
                from .calculation_jobs import prepare
                prepare(cursor)
            # Changes during a remote export must not be labelled as a current export.
            if export_snapshot()['fingerprint'] != snapshot['fingerprint']:
                raise ValidationError('Настройки изменились во время выгрузки. Выгрузка отменена; повторите её.')
            from .execution_tracking import record_export
            record_export(cursor, snapshot)
            connection.commit()
            from ..models import MotivationExportLog
            MotivationExportLog.objects.create(fingerprint=snapshot['fingerprint'],snapshot=snapshot)
        else:
            if export_snapshot()['fingerprint'] != snapshot['fingerprint']:
                raise ValidationError('Настройки изменились во время выгрузки. Повторите синхронизацию.')
            from .execution_tracking import record_export
            record_export(cursor,snapshot)
    except Exception:
        if connection is not None and manage_connection:
            connection.rollback()
        raise
    finally:
        if connection is not None and manage_connection:
            connection.close()
    return versions
