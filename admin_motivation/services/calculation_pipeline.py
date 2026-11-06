"""Reuse the 2026 combined result without running the full main calculation."""
import re
from decimal import Decimal
from django.core.exceptions import ValidationError
from .pilot_formula import mask_sql

CACHE = '#portal_shared_sales'
STATE = '#portal_shared_sales_state'


def validate_pair(main, combined):
    fields = ('quarter_share', 'year_share', 'payout_threshold')
    if (main.fact_year != 2026 or combined.fact_year != 2026
            or getattr(main, 'variant', 'main') != 'main' or combined.variant != 'all_2026'
            or main.plan_version.strip() != combined.plan_version.strip()
            or any(Decimal(str(getattr(main, key))) != Decimal(str(getattr(combined, key))) for key in fields)):
        raise ValidationError('Для общего расчёта нужны одинаковые параметры основного и объединённого вариантов 2026.')


def validate_equivalence(main_sql, combined_sql):
    """Prove recognized portal transformations differ only outside main periods.

    Annual IIF guards are redundant only because both annual/Q4 input SELECTs
    are restricted to Q4 for @FactYear=2026. Compare every remaining SQL token.
    """
    window = 'FROM #pf_base WHERE (Квартал=4 OR @FactYear<>2026)'
    if main_sql.count(window) != 2 or combined_sql.count(window) != 2:
        raise ValidationError('Не подтверждено одинаковое окно IV квартала для ускоренного расчёта.')
    # Early rows must not feed a later persisted-table transformation. Supported
    # references are DROP/INTO, the disabled carry INSERT/FROM, and the portal's
    # row-wise manager correction. Reject newly added consumers of the report.
    from .manager_correction import TABLE
    for definition in (main_sql, combined_sql):
        code = mask_sql(definition)
        references = list(re.finditer(TABLE+r'(?!\w)', code, re.I))
        drops = list(re.finditer(r'\bDROP\s+TABLE\s+'+TABLE+r'(?!\w)', code, re.I))
        expected = 3 + len(drops) + int('#portal_manager_correction' in code.casefold())
        if len(drops) > 1 or len(references) != expected:
            raise ValidationError('Обнаружена дополнительная обработка итогов. Требуется проверка ускоренного расчёта.')
    main_sql, count = re.subn(r'\bWHERE\s+1\s*=\s*0\s+AND\b', 'WHERE', main_sql, flags=re.I)
    if count != 3:
        raise ValidationError('Не подтверждены периоды ускоренного основного расчёта.')
    main_sql = re.sub(r'\bIIF\s*\(\s*Квартал\s*=\s*4\s*,\s*(\[?СуммаМотивации(?:USD|RUR)Год\]?)\s*,\s*0\s*\)',
                      r'\1', main_sql, flags=re.I)
    def normalize(sql):
        # Ignore comments/code formatting, but retain literal contents exactly:
        # department names, coefficient types and spaces inside strings matter.
        pieces, previous = [], 0
        for token in re.finditer(r"N?'(?:''|[^'])*'|--[^\n]*|/\*[\s\S]*?\*/", sql, re.I):
            pieces.append(re.sub(r'\s+', '', sql[previous:token.start()]).casefold())
            if not token[0].startswith(('--', '/*')):
                pieces.append(token[0])
            previous = token.end()
        pieces.append(re.sub(r'\s+', '', sql[previous:]).casefold())
        return ''.join(pieces)
    if normalize(main_sql) != normalize(combined_sql):
        raise ValidationError('Основной и объединённый расчёты различаются. Ускоренное задание не сохранено.')


def cache_setup(year):
    if year != 2026:
        raise ValueError('Shared pipeline supports 2026 only')
    # The join prevents SELECT INTO inheriting any Sales identity column.
    return f"""
IF OBJECT_ID('tempdb..{CACHE}') IS NOT NULL DROP TABLE {CACHE};
IF OBJECT_ID('tempdb..{STATE}') IS NOT NULL DROP TABLE {STATE};
SELECT TOP (0) s.* INTO {CACHE} FROM DataWH.dbo.Sales s
CROSS JOIN (SELECT 1 AS SchemaOnly) schema_only;
CREATE TABLE {STATE} (FactYear int NOT NULL, Ready bit NOT NULL);
INSERT INTO {STATE} VALUES (2026,0);
"""


def main_projection():
    """Copy values and types as calculated: no re-aggregation or rounding."""
    from .calculation_variants import TABLES
    statements = ["IF @@TRANCOUNT<>0 THROW 50030,N'Не закрыта транзакция объединённого расчёта',1;",
                  'DECLARE @portalProjectionStarted datetime2=SYSUTCDATETIME();', 'BEGIN TRANSACTION;']
    for table in TABLES:
        target = 'DataWH.motivation.' + table
        condition = (" WHERE SUBSTRING(LTRIM(RTRIM([КварталМотивации])),1,1)=N'4' "
                     "OR LOWER(LTRIM(RTRIM([КварталМотивации])))=N'год'") if table == 'ReportMotivationByDealAuthor' else ''
        statements.extend([f"IF OBJECT_ID(N'{target}',N'U') IS NOT NULL DROP TABLE {target};",
                           f'SELECT * INTO {target} FROM {target}All2026{condition};'])
    statements.extend(['COMMIT TRANSACTION;',
        "PRINT N'Формирование основного результата: '+CONVERT(nvarchar(30),DATEDIFF(MILLISECOND,@portalProjectionStarted,SYSUTCDATETIME()))+N' мс';"])
    return '\n'.join(statements)


def scoped(statement):
    return "EXEC sys.sp_executesql N'" + statement.replace("'", "''") + "';"
