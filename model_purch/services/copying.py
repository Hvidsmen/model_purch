"""Copy parameters onto existing target goods; never expand scenario membership."""
from django.db import transaction
from ..models import PGGoods, ScenarioModel
from ..goods_identity import planning_group_key


def copy_goods(source, target):
    source_goods = PGGoods.objects.filter(scenario_plan=source).select_related('kind_purch')
    skipped_count = 0
    updated_count = 0

    with transaction.atomic():
        ScenarioModel.objects.select_for_update().get(pk=target.pk)
        targets = {row.planning_group_key: row for row in PGGoods.objects.select_for_update().filter(scenario_plan=target)}
        for sg in source_goods:
            obj = targets.get(planning_group_key(sg.planning_group))
            if obj is None:
                skipped_count += 1
                continue
            values = {
                    'planning_group': sg.planning_group,
                    'planning_sales': sg.planning_sales,
                    'group_goods': sg.group_goods,
                    'brand': sg.brand,
                    'purch': sg.purch,
                    'kind_purch': sg.kind_purch,
                    'volume': sg.volume,
                    'container_volume': sg.container_volume,
                    'duty_rate': sg.duty_rate,
                    'exw_usd': sg.exw_usd,
                    **{name: getattr(sg, name) for name in (
                        'plan_ddp_usd', 'plan_volume', 'plan_quantity',
                        'fact_ddp_usd', 'fact_volume', 'fact_quantity',
                        'stock_ddp_usd', 'stock_volume', 'stock_quantity',
                    )},
                    'stock_cnt_day': sg.stock_cnt_day,
                    'percent_stock_end': sg.percent_stock_end,
                }
            for field, value in values.items():
                setattr(obj, field, value)
            obj.save(force_update=True)
            updated_count += 1

    return updated_count, skipped_count
