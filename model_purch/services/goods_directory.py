"""Refresh the four source classifications without reimporting scenario inputs."""
from pathlib import Path
import unicodedata

from django.core.exceptions import ValidationError
from django.db import transaction

from ..conns import connect_database
from ..goods_identity import planning_group_key
from ..models import GoodsGroup, PGGoods, ScenarioModel
from .pricing import reprice_goods

FIELDS = ('planning_sales', 'group_goods', 'brand', 'purch')
REBUILD_SQL = (Path(__file__).resolve().parents[1] / 'sql' / 'refresh_goods_directory.sql').read_text(encoding='utf-8')
SQL = '''
SELECT DISTINCT [PlanningGroupOZP], [PlanningGroupSalesERP], [Group_1], [BrandName], [Purch], [PlanningKey]
FROM [ModelPurch].[dbo].[PlanningGroupOZP]
WHERE [PlanningGroupOZP] IS NOT NULL
'''


def refresh_goods_directory(scenario, connector=None):
    keys = set(PGGoods.objects.filter(scenario_plan=scenario).values_list('planning_group_key', flat=True))
    if not keys:
        return {'updated': 0, 'unchanged': 0, 'missing': 0}
    connection, cursor = (connector or connect_database)('vm-dwh', 'ModelPurch')
    try:
        connection.autocommit = False
        connection.timeout = 120
        cursor.execute(REBUILD_SQL)
        cursor.execute(SQL)
        source, ranks = {}, {}
        for group, sales, goods_group, brand, supplier, planning_key in cursor.fetchall():
            raw = (sales, goods_group, brand, supplier)
            key = planning_group_key(group)
            if key not in keys:
                continue
            values = tuple(str(value).strip() if value is not None else '' for value in raw)
            if len(values) != len(FIELDS) or any(len(value) > 255 for value in values):
                raise ValidationError(f'Некорректные поля справочника для группы «{group}». Обновление отменено.')
            values = (*values[:2], values[2] or None, values[3] or None)
            # Match the portal's exact Unicode identity as well as SQL's grouping.
            # Choose a whole representative row; never combine unrelated fields.
            code = unicodedata.normalize('NFC', str(planning_key or '').strip())
            rank = (0 if code.casefold().startswith('pg') else 1 if code else 2,
                    code.casefold(), code, str(group or ''), tuple(value or '' for value in values))
            if key not in ranks or rank < ranks[key]:
                ranks[key] = rank
                source[key] = values
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    counts = {'updated': 0, 'unchanged': 0, 'missing': 0}
    with transaction.atomic():
        locked = ScenarioModel.objects.select_for_update().get(pk=scenario.pk)
        goods = PGGoods.objects.select_for_update().filter(scenario_plan=locked)
        changed, supplier_ids, groups = [], [], {}
        for good in goods:
            values = source.get(good.planning_group_key)
            if values is None:
                counts['missing'] += 1
                continue
            if tuple(getattr(good, field) for field in FIELDS) == values:
                counts['unchanged'] += 1
                continue
            if good.purch != values[3]:
                supplier_ids.append(good.pk)
            for field, value in zip(FIELDS, values):
                setattr(good, field, value)
            if good.group_goods:
                groups[planning_group_key(good.group_goods)] = good.group_goods
            changed.append(good)
        for key, name in groups.items():
            GoodsGroup.objects.get_or_create(name_key=key, defaults={'name': name})
        PGGoods.objects.bulk_update(changed, FIELDS, batch_size=100)
        # Supplier supply type participates in DDP (e.g. a Russian supplier).
        for start in range(0, len(supplier_ids), 500):
            reprice_goods(goods=PGGoods.objects.filter(pk__in=supplier_ids[start:start + 500]))
        counts['updated'] = len(changed)
    return counts
