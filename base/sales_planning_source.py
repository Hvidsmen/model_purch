"""Read approved DataWH reference queries into the sales-planning directory."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from model_purch.conns import connect_database
from .models import SalesPlanningChannel, SalesPlanningDirectoryLoad


CHANNEL_SQL = '''SELECT DISTINCT
    Даичи_КаналПродаж
FROM DataWH.erp.[Справочники.СтруктураПредприятия]
WHERE Даичи_КаналПродаж IS NOT NULL'''


def load_channels():
    connection, cursor = connect_database('vm-dwh', 'DataWH')
    try:
        connection.timeout = 60
        cursor.execute(CHANNEL_SQL)
        rows = cursor.fetchall()
    finally:
        try:
            cursor.close()
        finally:
            connection.close()
    names = {}
    for row in rows:
        if row[0] is None:
            continue
        name = str(row[0]).strip()
        if not name:
            continue
        if len(name) > 255:
            raise ValidationError('Название канала продаж длиннее 255 символов. Данные не изменены.')
        names.setdefault(name.casefold(), name)
    if not names:
        raise ValidationError('Источник не вернул каналов продаж. Сохранённые данные не изменены.')
    with transaction.atomic():
        # Keep stable portal IDs when a channel disappears or is restored.
        existing = {channel.name.casefold(): channel for channel in SalesPlanningChannel.objects.all()}
        SalesPlanningChannel.objects.update(is_active=False)
        for key, name in names.items():
            channel = existing.get(key)
            if channel:
                channel.is_active = True
                channel.save(update_fields=['is_active'])
            else:
                SalesPlanningChannel.objects.create(name=name)
        SalesPlanningDirectoryLoad.objects.update_or_create(directory='channels', defaults={
            'loaded_at': timezone.now(), 'row_count': len(names)})
    return len(names)
