"""Refresh classifications and append missing goods from the scenario sales plans."""
from pathlib import Path
import unicodedata
import math

from django.core.exceptions import ValidationError
from django.db import transaction

from ..conns import connect_database
from ..goods_identity import planning_group_key
from .erp_plan_parameters import attached_sources
from ..models import GoodsGroup, KindPurch, PGGoods, ScenarioModel

FIELDS = ('planning_sales', 'group_goods', 'brand')
REBUILD_SQL = (Path(__file__).resolve().parents[1] / 'sql' / 'refresh_goods_directory.sql').read_text(encoding='utf-8')
NEW_GOODS_SQL = (Path(__file__).resolve().parents[1] / 'sql' / 'new_directory_goods.sql').read_text(encoding='utf-8')
SQL = '''
SELECT DISTINCT [PlanningGroupOZP], [PlanningGroupSalesERP], [Group_1], [BrandName], [PlanningKey]
FROM [ModelPurch].[dbo].[PlanningGroupOZP]
WHERE [PlanningGroupOZP] IS NOT NULL
'''


def refresh_goods_directory(scenario, connector=None):
    scenario = ScenarioModel.objects.get(pk=scenario.pk)
    keys = set(PGGoods.objects.filter(scenario_plan=scenario).values_list('planning_group_key', flat=True))
    sources = attached_sources(scenario)
    if len(sources) > 100 or any(len(value) > 255 for value in sources):
        raise ValidationError('В сценарии допускается не более 100 планов продаж с названиями до 255 символов.')
    if not keys and not sources:
        return {'added': 0, 'updated': 0, 'unchanged': 0, 'missing': 0}
    connection, cursor = (connector or connect_database)('vm-dwh', 'ModelPurch')
    try:
        connection.autocommit = False
        connection.timeout = 120
        cursor.execute(REBUILD_SQL)
        cursor.execute(SQL)
        source, ranks = {}, {}
        for group, sales, goods_group, brand, planning_key in cursor.fetchall():
            raw = (sales, goods_group, brand)
            key = planning_group_key(group)
            if key not in keys:
                continue
            values = tuple(str(value).strip() if value is not None else '' for value in raw)
            if len(values) != len(FIELDS) or any(len(value) > 255 for value in values):
                raise ValidationError(f'Некорректные поля справочника для группы «{group}». Обновление отменено.')
            values = (*values[:2], values[2] or None)
            # Match the portal's exact Unicode identity as well as SQL's grouping.
            # Choose a whole representative row; never combine unrelated fields.
            code = unicodedata.normalize('NFC', str(planning_key or '').strip())
            rank = (0 if code.casefold().startswith('pg') else 1 if code else 2,
                    code.casefold(), code, str(group or ''), tuple(value or '' for value in values))
            if key not in ranks or rank < ranks[key]:
                ranks[key] = rank
                source[key] = values
        new_goods, new_ranks = {}, {}
        if sources:
            cursor.execute(NEW_GOODS_SQL.replace('/*SCENARIOS*/', ','.join('?' for _ in sources)),
                           scenario.date_start_plan, scenario.date_end_plan, *sources)
            for group, sales, goods_group, brand, planning_key, supplier, volume in cursor.fetchall():
                group = str(group or '').strip()
                if not group or len(group) > 255:
                    raise ValidationError('Некорректное название новой плановой группы. Обновление отменено.')
                key = planning_group_key(group)
                if key in keys:
                    continue  # New-row defaults never affect existing scenario inputs.
                values = tuple(str(value).strip() if value is not None else '' for value in (sales, goods_group, brand, supplier))
                if any(len(value) > 255 for value in values):
                    raise ValidationError(f'Слишком длинные поля нового товара «{group}». Обновление отменено.')
                try:
                    volume = float(volume or 0)
                    if not math.isfinite(volume) or volume < 0:
                        raise ValueError
                except (TypeError, ValueError, OverflowError) as error:
                    raise ValidationError(f'Некорректный объём нового товара «{group}». Обновление отменено.') from error
                code = unicodedata.normalize('NFC', str(planning_key or '').strip())
                rank = (0 if code.casefold().startswith('pg') else 1 if code else 2,
                        code.casefold(), code, group, values, volume)
                if key not in new_ranks or rank < new_ranks[key]:
                    new_ranks[key] = rank
                    new_goods[key] = {'planning_group': group, 'planning_sales': values[0],
                                      'group_goods': values[1], 'brand': values[2] or None,
                                      'purch': values[3] or None, 'volume': volume}
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    counts = {'added': 0, 'updated': 0, 'unchanged': 0, 'missing': 0}
    with transaction.atomic():
        locked = ScenarioModel.objects.select_for_update().get(pk=scenario.pk)
        if attached_sources(locked) != sources or (locked.date_start_plan, locked.date_end_plan) != (scenario.date_start_plan, scenario.date_end_plan):
            raise ValidationError('Планы или период сценария изменились во время загрузки. Повторите обновление.')
        goods = PGGoods.objects.select_for_update().filter(scenario_plan=locked)
        changed, groups = [], {}
        existing = set()
        for good in goods:
            existing.add(good.planning_group_key)
            values = source.get(good.planning_group_key)
            if values is None:
                counts['missing'] += 1
                continue
            if tuple(getattr(good, field) for field in FIELDS) == values:
                counts['unchanged'] += 1
                continue
            for field, value in zip(FIELDS, values):
                setattr(good, field, value)
            if good.group_goods:
                groups[planning_group_key(good.group_goods)] = good.group_goods
            changed.append(good)
        for key, name in groups.items():
            GoodsGroup.objects.get_or_create(name_key=key, defaults={'name': name})
        PGGoods.objects.bulk_update(changed, FIELDS, batch_size=100)
        counts['updated'] = len(changed)
        kind = None
        for key, values in new_goods.items():
            if key in existing:
                continue
            if kind is None:
                kind, _ = KindPurch.objects.get_or_create(name='Закупается')
            group_name = values['group_goods']
            group = None
            if group_name:
                group, _ = GoodsGroup.objects.get_or_create(name_key=planning_group_key(group_name), defaults={'name': group_name})
            _, created = PGGoods.objects.get_or_create(scenario_plan=locked, planning_group_key=key,
                defaults={**values, 'kind_purch': kind, 'exw_usd': 0, 'stock_cnt_day': 0,
                          'percent_stock_end': 0, 'container_volume': group.container_volume if group else 65})
            counts['added'] += int(created)
            existing.add(key)
    return counts
