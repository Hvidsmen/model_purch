from pathlib import Path
import math
from decimal import Decimal
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
        cursor.execute(SQL.replace('/*SCENARIOS*/', ','.join('?' for _ in sources)), str(scenario.date_start_plan), str(scenario.date_end_plan), *sources)
        totals = {}
        fields = ['plan_ddp_usd', 'plan_volume', 'plan_quantity',
                  'stock_ddp_usd', 'stock_volume', 'stock_quantity',
                  'fact_ddp_usd', 'fact_volume', 'fact_quantity']
        for group, *raw in cursor.fetchall():
            if len(raw) != 9:
                raise ValidationError('Запрос должен вернуть план, сток и факт с количествами.')
            key = planning_group_key(group or '')
            numbers = [Decimal(str(value)) if value is not None else None for value in raw]
            if any(value is not None and not value.is_finite() for value in numbers):
                raise ValidationError('В плане, стоке или факте есть некорректные значения.')
            if any(numbers[index] is not None and numbers[index] <= 0 for index in (2, 5, 8)):
                raise ValidationError('Количество плана, стока и факта должно быть положительным.')
            aggregate = totals.setdefault(key, [[None, Decimal(0)] for _ in fields])
            for index, value in enumerate(numbers):
                if value is None:
                    continue
                total = aggregate[index]
                if index in (2, 5, 8):
                    total[0] = (total[0] or Decimal(0)) + value
                else:
                    quantity_index = (index // 3) * 3 + 2
                    quantity = numbers[quantity_index]
                    if index < 2 and quantity is None:
                        quantity = numbers[5]  # Plan parameters fall back to stock.
                    if quantity is None:
                        raise ValidationError('Для расчёта DDP и объёма отсутствует количество.')
                    total[0] = (total[0] or Decimal(0)) + value * quantity
                    total[1] += quantity
        values = {}
        for key, aggregate in totals.items():
            values[key] = [None if total is None else float(total if index in (2, 5, 8) else total / weight)
                           for index, (total, weight) in enumerate(aggregate)]
        if any(value is not None and not math.isfinite(value) for row in values.values() for value in row):
            raise ValidationError('В плане, стоке или факте есть слишком большие значения.')
    finally:
        connection.close()
    if not values:
        raise ValidationError('В выбранных планах, остатках и фактических продажах нет групп с положительным количеством. Существующие значения сохранены.')
    with transaction.atomic():
        locked = ScenarioModel.objects.select_for_update().get(pk=scenario.pk)
        if (locked.date_start_plan, locked.date_end_plan) != (scenario.date_start_plan, scenario.date_end_plan):
            raise ValidationError('Период сценария изменился во время загрузки. Повторите обновление.')
        if use_attached and attached_sources(locked) != sources:
            raise ValidationError('Планы продаж сценария изменились во время загрузки. Повторите обновление.')
        goods = list(PGGoods.objects.filter(scenario_plan=locked))
        matched = 0
        for good in goods:
            pair = values.get(good.planning_group_key)
            for field, value in zip(fields, pair if pair else [None] * len(fields)):
                setattr(good, field, value)
            matched += pair is not None
        PGGoods.objects.bulk_update(goods, fields, batch_size=500)
        locked.erp_sales_plan_sources = sources
        locked.plan_parameters_loaded_at = timezone.now()
        locked.save(update_fields=['erp_sales_plan_sources', 'plan_parameters_loaded_at'])
    return matched, len(goods) - matched
