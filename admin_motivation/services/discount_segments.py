"""Read existing SQL thresholds once, then maintain them with coefficient versions."""
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.db import models, transaction
from ..models import DiscountSegment, GlobalCoeffVersion, touch_version

FIELDS = ('o0', 'o1', 'o2', 'o3', 'o4')
SELECT_SQL = 'SELECT Date_, TypeDiscount, [O0], [O1], [O2], [O3], [O4] FROM DataWH.motivation.SegmentMotivation ORDER BY Date_, TypeDiscount'
INSERT_SQL = 'INSERT INTO DataWH.motivation.SegmentMotivation (Date_, TypeDiscount, [O0], [O1], [O2], [O3], [O4]) VALUES (?, ?, ?, ?, ?, ?, ?)'


def import_missing_versions(connector):
    connection = None
    try:
        connection, cursor = connector('vm-dwh', 'DataWH')
        rows = list(cursor.execute(SELECT_SQL).fetchall())
    finally:
        if connection is not None:
            connection.close()
    if not rows:
        raise ValidationError('Таблица SegmentMotivation пуста. Введите сегменты в последнем черновике.')
    timeline = {}
    for raw in rows:
        day, name, *values = raw
        day = day.date() if hasattr(day, 'date') else day
        if not day or not name or not str(name).strip():
            raise ValidationError('В MS SQL есть строка без даты или типа скидки. Импорт отменён.')
        try:
            values = [None if value is None else Decimal(str(value)) for value in values]
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError('Некорректный порог скидки в MS SQL. Импорт отменён.')
        name = str(name).strip()
        entries = timeline.setdefault(day, {})
        if name.casefold() in entries:
            raise ValidationError('Дубли типа скидки на одну дату в MS SQL. Импорт отменён.')
        entries[name.casefold()] = (name, values)
    with transaction.atomic():
        versions = list(GlobalCoeffVersion.objects.select_for_update().order_by('pk'))
        created, filled = [], []
        for version in versions:
            if version.discount_segments.exists():
                continue
            source_day = max((day for day in timeline if day <= version.effective_from), default=None)
            if source_day is None:
                raise ValidationError(f'В MS SQL нет порогов на дату {version.effective_from:%d.%m.%Y}. Импорт отменён.')
            for name, values in timeline[source_day].values():
                row = DiscountSegment(version=version, type_discount=name, **dict(zip(FIELDS, values)))
                row.full_clean()
                created.append(row)
            filled.append(version.pk)
        # One-time historical bootstrap only: never overwrite a configured version.
        models.QuerySet.bulk_create(DiscountSegment.objects.all(), created)
        for pk in filled:
            touch_version(pk)
    return len(filled), len(created)


def export_enabled(versions):
    if not DiscountSegment.objects.filter(version__in=versions).exists():
        return False  # Upgrade compatibility: existing SQL thresholds remain untouched.
    if any(not version.discount_segments.exists() for version in versions):
        raise ValidationError('Не во всех утверждённых версиях заполнены сегменты скидок. Загрузите недостающие версии из MS SQL перед выгрузкой.')
    return True
