"""Capture reproducible inputs and reject invalid calculations before SQL writes."""
import hashlib
import json
import math
from collections import Counter
from decimal import Decimal
from django.core.serializers.json import DjangoJSONEncoder
from django.conf import settings
from ..models import Freight, PGGoods, Purch, PurchPay, ScenarioModel, ScenarioPlanSales, KindPurch, KindLagPay


def snapshot(scenario):
    purchases = Purch.objects.filter(scenario_plan=scenario).order_by('pk')
    data = {
        'scenario': ScenarioModel.objects.filter(pk=scenario.pk).values().get(),
        'plans': list(ScenarioPlanSales.objects.filter(scenario_model=scenario).order_by('pk').values()),
        'goods': list(PGGoods.objects.filter(scenario_plan=scenario).order_by('pk').values()),
        'purchases': list(purchases.values()),
        'payments': list(PurchPay.objects.filter(purch__in=purchases).order_by('pk').values()),
        'freight': list(Freight.objects.filter(scenario=scenario).values()),
        'purchase_kinds': list(KindPurch.objects.filter(pk__in=PGGoods.objects.filter(scenario_plan=scenario).values('kind_purch_id')).order_by('pk').values()),
        'payment_kinds': list(KindLagPay.objects.filter(pk__in=PurchPay.objects.filter(purch__in=purchases).values('kind_lag_pay_id')).order_by('pk').values()),
    }
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False, allow_nan=False))


def fingerprint(parameters):
    encoded = json.dumps(parameters, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def valid_number(value, minimum=0, maximum=None, strictly_positive=False):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(value) and value >= minimum and (not strictly_positive or value > 0) and (maximum is None or value <= maximum)


def validation_errors(scenario):
    errors = []
    if scenario.date_start_plan > scenario.date_end_plan:
        errors.append('Дата начала сценария позже даты окончания.')
    names = list(ScenarioModel.objects.exclude(pk=scenario.pk).values_list('name', flat=True))
    if not scenario.name.strip() or scenario.name.strip().casefold() in {name.strip().casefold() for name in names}:
        errors.append('Название сценария должно быть заполнено и уникально для экспорта в MS SQL.')
    goods = list(PGGoods.objects.filter(scenario_plan=scenario))
    if not goods:
        errors.append('В сценарии нет товаров для расчёта.')
    for good in goods:
        for name, label in [('volume', 'Объём'), ('exw_usd', 'EXW'), ('ddp_usd', 'DDP'), ('kddp', 'KDDP')]:
            if not valid_number(getattr(good, name), strictly_positive=(name == 'volume')):
                errors.append(f'{good.planning_group}: {label} должен быть конечным числом {"больше нуля" if name == "volume" else "не меньше нуля"}.')
        if not valid_number(good.percent_stock_end, maximum=100):
            errors.append(f'{good.planning_group}: процент конечного запаса должен быть от 0 до 100.')
        if not valid_number(good.stock_cnt_day):
            errors.append(f'{good.planning_group}: запас в днях должен быть неотрицательным.')
    purchases = list(Purch.objects.filter(scenario_plan=scenario).prefetch_related('purchpay_set'))
    duplicate_names = {name for name, count in Counter(p.name.strip().casefold() for p in purchases).items() if count > 1}
    if duplicate_names:
        errors.append('Повторяются названия закупок внутри сценария: ' + ', '.join(sorted(duplicate_names)))
    for purchase in purchases:
        if not valid_number(purchase.lag_income) or not valid_number(purchase.lage_make):
            errors.append(f'{purchase.name}: лаг доставки и производства должны быть неотрицательными.')
        payments = list(purchase.purchpay_set.all())
        if not payments:
            errors.append(f'{purchase.name}: не заполнен график платежей.')
        elif any(not valid_number(pay.percent_pay, maximum=100) for pay in payments):
            errors.append(f'{purchase.name}: каждый процент платежа должен быть от 0 до 100.')
        elif abs(sum(Decimal(str(pay.percent_pay)) for pay in payments) - Decimal('100')) > Decimal('0.01'):
            errors.append(f'{purchase.name}: сумма процентов платежей должна равняться 100%.')
    freight = Freight.objects.filter(scenario=scenario).first()
    if freight and (not valid_number(freight.price_per_container) or not valid_number(freight.volume_per_container, strictly_positive=True)):
        errors.append('Фрахт: цена должна быть неотрицательной, объём — больше нуля.')
    return errors


def calculation_readiness(scenario):
    errors = validation_errors(scenario)
    connection = getattr(settings, 'MS_SQL_CONN_STR', None)
    if not isinstance(connection, str) or not connection.strip():
        errors.append('Не настроено подключение MS_SQL_CONN_STR для расчёта в MS SQL.')
    export = scenario.exports.first()
    try:
        parameters = snapshot(scenario)
        current = fingerprint(parameters)
    except (ValueError, TypeError):
        parameters, current = {}, None
        errors.append('В параметрах есть некорректные числовые значения.')
    if export is None:
        errors.append('Сначала экспортируйте выбранный сценарий в MS SQL.')
    elif current != export.fingerprint:
        errors.append('Параметры изменились после экспорта. Повторите экспорт сценария в MS SQL.')
    return errors, export, parameters
