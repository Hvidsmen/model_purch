"""Read approved DataWH reference queries into the sales-planning directory."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from model_purch.conns import connect_database
from .models import SalesPlanningChannel, SalesPlanningDirectoryLoad, SalesPlanningGoods
from .sales_planning_scope import PRODUCT_GROUP


CHANNEL_SQL = '''SELECT DISTINCT
    Даичи_КаналПродаж
FROM DataWH.erp.[Справочники.СтруктураПредприятия]
WHERE Даичи_КаналПродаж IS NOT NULL'''

GOODS_SQL = '''SELECT
    Ссылка, Наименование, Родитель, Даичи_ГруппаПланирования, Марка,
    Даичи_ГруппаПлановПродаж, БИТ_КлассМощности, БИТ_ТипБлока, БИТ_ТипНаружногоБлока
FROM DataWH.erp.[Справочники.Номенклатура]
WHERE LTRIM(RTRIM(Родитель)) = N'Бытовые кондиционеры' '''


def reference_key(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return '0x' + bytes(value).hex()
    return str(value).strip() if value is not None else ''


def load_goods():
    connection, cursor = connect_database('vm-dwh', 'DataWH')
    try:
        connection.timeout = 60
        cursor.execute(GOODS_SQL)
        rows = cursor.fetchall()
    finally:
        try:
            cursor.close()
        finally:
            connection.close()
    goods = {}
    fields = ('planning_group', 'brand', 'sales_plan_group', 'power_class', 'block_type', 'outdoor_block_type')
    for row in rows:
        key = reference_key(row[0])
        product_group = str(row[2]).strip() if row[2] is not None else ''
        if product_group.casefold() != PRODUCT_GROUP.casefold():
            continue
        product_group = PRODUCT_GROUP
        name = str(row[1]).strip() if row[1] is not None else ''
        if not key or not name or len(key) > 255:
            raise ValidationError('В номенклатуре отсутствует ссылка или наименование либо ссылка длиннее 255 символов. Данные не изменены.')
        values = {'name': name, 'product_group': product_group, **{
            field: str(value).strip() if value is not None else '' for field, value in zip(fields, row[3:])}}
        if key in goods and goods[key] != values:
            raise ValidationError('В номенклатуре найдены разные записи с одной ссылкой. Данные не изменены.')
        goods[key] = values
    if not goods:
        raise ValidationError('Источник не вернул номенклатуру. Сохранённые данные не изменены.')
    with transaction.atomic():
        SalesPlanningGoods.objects.update(is_active=False)
        SalesPlanningGoods.objects.bulk_create(
            [SalesPlanningGoods(source_key=key, **values) for key, values in goods.items()],
            update_conflicts=True, unique_fields=['source_key'],
            update_fields=['name', 'product_group', *fields, 'is_active'], batch_size=80)
        SalesPlanningDirectoryLoad.objects.update_or_create(directory='goods', defaults={
            'loaded_at': timezone.now(), 'row_count': len(goods)})
        from .sales_planning_group_service import rebuild_groups
        rebuild_groups()
    return len(goods)


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




