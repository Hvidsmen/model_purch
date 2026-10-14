from pathlib import Path
import math
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from ..conns import connect_database
from ..models import PGGoods, ScenarioModel, ScenarioPlanSales
from ..goods_identity import planning_group_key

SQL = (Path(__file__).resolve().parents[1] / 'sql' / 'erp_plan_parameters.sql').read_text(encoding='utf-8')


def attached_sources(scenario):
    return list(dict.fromkeys(name.strip() for name in ScenarioPlanSales.objects.filter(scenario_model=scenario).order_by('pk').values_list('name', flat=True) if name.strip()))


def update_parameters(scenario, sources=None, connector=None):
    use_attached = sources is None
    if use_attached:
        sources = attached_sources(scenario)
    sources = list(dict.fromkeys(str(value).strip() for value in sources if str(value).strip()))
    if not sources or len(sources) > 100 or any(len(value) > 255 for value in sources):
        raise ValidationError('В сценарии закупок должны быть указаны от 1 до 100 непустых планов продаж. Добавьте планы в настройках сценария.')
    connection, cursor = (connector or connect_database)('vm-dwh', 'DataWH')
    try:
        connection.timeout = 120
        cursor.execute(SQL.replace('/*SCENARIOS*/', ','.join('?' for _ in sources)), *sources)
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
        raise ValidationError('По выбранным планам нет групп с положительным количеством. Существующие значения сохранены.')
    with transaction.atomic():
        locked = ScenarioModel.objects.select_for_update().get(pk=scenario.pk)
        if use_attached and attached_sources(locked) != sources:
            raise ValidationError('Планы продаж сценария изменились во время загрузки. Повторите обновление.')
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
