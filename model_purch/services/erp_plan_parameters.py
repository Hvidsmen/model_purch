from pathlib import Path
import math
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ..conns import connect_database
from ..models import PGGoods, ScenarioModel
from ..goods_identity import planning_group_key

SQL = (Path(__file__).resolve().parents[1] / 'sql' / 'erp_plan_parameters.sql').read_text(encoding='utf-8')


def source_versions(connector=None):
    connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
    try:
        connection.timeout = 30
        cursor.execute('SELECT DISTINCT Сценарий FROM DataWH.erp.[РегистрыНакопления.ПланыПродаж] WHERE Сценарий IS NOT NULL ORDER BY Сценарий')
        return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def update_parameters(scenario, sources, connector=None):
    sources = list(dict.fromkeys(str(value).strip() for value in sources if str(value).strip()))
    if not sources or len(sources) > 100 or any(len(value) > 255 for value in sources):
        raise ValidationError('Выберите от 1 до 100 сценариев плана продаж.')
    connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
    try:
        connection.timeout = 120
        cursor.execute(SQL.replace('/*SCENARIOS*/', ','.join('?' for _ in sources)), scenario.date_start_plan, scenario.date_end_plan, *sources)
        values = {}
        for group, ddp, volume in cursor.fetchall():
            key = planning_group_key(group or '')
            if key in values:
                raise ValidationError('Неоднозначная плановая группа в результате SQL.')
            numbers = [float(value) if value is not None else None for value in (ddp, volume)]
            if any(value is not None and not math.isfinite(value) for value in numbers):
                raise ValidationError('В плане есть некорректные DDP или объём.')
            values[key] = numbers
    finally:
        connection.close()
    if not values:
        raise ValidationError('За период сценария план не найден. Существующие значения сохранены.')
    with transaction.atomic():
        locked = ScenarioModel.objects.select_for_update().get(pk=scenario.pk)
        if (locked.date_start_plan, locked.date_end_plan) != (scenario.date_start_plan, scenario.date_end_plan):
            raise ValidationError('Период сценария изменился. Повторите загрузку.')
        goods = list(PGGoods.objects.filter(scenario_plan=locked))
        matched = 0
        for good in goods:
            pair = values.get(good.planning_group_key)
            good.plan_ddp_usd, good.plan_volume = pair if pair else (None, None)
            matched += pair is not None
        PGGoods.objects.bulk_update(goods, ['plan_ddp_usd', 'plan_volume'], batch_size=500)
        locked.erp_sales_plan_sources = sources
        locked.plan_parameters_loaded_at = timezone.now()
        locked.save(update_fields=['erp_sales_plan_sources', 'plan_parameters_loaded_at'])
    return matched, len(goods) - matched
