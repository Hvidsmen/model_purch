from django.db import transaction
from ..models import PGGoods
from ..goods_identity import planning_group_key


def copy_goods(source, target):
    source_goods = PGGoods.objects.filter(scenario_plan=source).select_related('kind_purch')
    created_count = 0
    updated_count = 0

    with transaction.atomic():
        for sg in source_goods:
            # update_or_create предотвращает дубликаты: если товар с такими же плановыми группами уже есть, он обновится
            obj, created = PGGoods.objects.update_or_create(
                scenario_plan=target,
                planning_group_key=planning_group_key(sg.planning_group),
                defaults={
                    'planning_group': sg.planning_group,
                    'planning_sales': sg.planning_sales,
                    'group_goods': sg.group_goods,
                    'brand': sg.brand,
                    'purch': sg.purch,
                    'kind_purch': sg.kind_purch,
                    'volume': sg.volume,
                    'container_volume': sg.container_volume,
                    'exw_usd': sg.exw_usd,
                    'ddp_usd': sg.ddp_usd,
                    'kddp': sg.kddp,
                    'stock_cnt_day': sg.stock_cnt_day,
                    'percent_stock_end': sg.percent_stock_end,
                }
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

    return created_count, updated_count
