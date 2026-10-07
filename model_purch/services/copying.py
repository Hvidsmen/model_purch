from django.db import transaction
from ..models import Purch, PurchPay, PGGoods
from ..goods_identity import planning_group_key


def copy_purchases(source, target):
    source_purchs = Purch.objects.filter(scenario_plan=source).prefetch_related('purchpay_set')
    created_count = updated_count = 0

    with transaction.atomic():
        for sp in source_purchs:
            # Ищем или создаем закупку с таким же именем в целевом сценарии
            p, created = Purch.objects.get_or_create(
                name=sp.name,
                scenario_plan=target,
                defaults={'lag_income': sp.lag_income, 'lage_make': sp.lage_make}
            )
            # Обновляем параметры закупки на случай изменений в источнике
            if p.lag_income != sp.lag_income or p.lage_make != sp.lage_make:
                p.lag_income = sp.lag_income
                p.lage_make = sp.lage_make
                p.save()

            # Полностью заменяем платежи на те, что в источнике
            p.purchpay_set.all().delete()
            new_pays = [
                PurchPay(purch=p, name=pay.name, percent_pay=pay.percent_pay, lag_day_pay=pay.lag_day_pay,
                         kind_lag_pay_id=pay.kind_lag_pay_id)
                for pay in sp.purchpay_set.all()
            ]
            if new_pays:
                PurchPay.objects.bulk_create(new_pays)

            if created:
                created_count += 1
            else:
                updated_count += 1

    return created_count, updated_count


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
