"""Capture reproducible inputs and reject invalid calculations before SQL writes."""
import hashlib
import json
import math
from decimal import Decimal
from django.core.serializers.json import DjangoJSONEncoder
from django.conf import settings
from ..models import Freight, PGGoods, Purch, PurchPay, ScenarioModel, ScenarioPlanSales, KindPurch, KindLagPay


def snapshot(scenario):
    purchases = Purch.objects.all().order_by('pk')
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
        for name, label in [('container_volume', 'Объём контейнера'), ('volume', 'Объём'), ('exw_usd', 'EXW'), ('ddp_usd', 'DDP'), ('kddp', 'KDDP')]:
            if not valid_number(getattr(good, name), strictly_positive=(name in {'volume', 'container_volume'})):
                errors.append(f'{good.planning_group}: {label} должен быть конечным числом {"больше нуля" if name in {"volume", "container_volume"} else "не меньше нуля"}.')
        if not valid_number(good.percent_stock_end, maximum=100):
            errors.append(f'{good.planning_group}: процент конечного запаса должен быть от 0 до 100.')
        if not valid_number(good.stock_cnt_day):
            errors.append(f'{good.planning_group}: запас в днях должен быть неотрицательным.')
    from .purchases import coverage_errors
    errors.extend(coverage_errors(scenario))
    purchases = list(Purch.objects.all().prefetch_related('purchpay_set'))
    for purchase in purchases:
        if not purchase.name.strip():
            errors.append('Название закупки в общем справочнике должно быть заполнено.')
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


def batch_readiness():
    """Validate and record every local scenario before one shared SQL calculation."""
    scenarios = list(ScenarioModel.objects.order_by('pk'))
    checks, entries, errors = [], [], []
    for scenario in scenarios:
        scenario_errors, export, parameters = calculation_readiness(scenario)
        checks.append({'scenario': scenario, 'export': export, 'parameters': parameters, 'errors': scenario_errors})
        errors.extend(f'«{scenario.name}»: {error}' for error in scenario_errors)
        entries.append({'scenario_id': scenario.pk, 'export_id': export.pk if export else None,
                        'exported_at': export.exported_at.isoformat() if export else None, 'parameters': parameters})
    if not scenarios:
        errors.append('Сначала создайте сценарии для расчёта.')
    return errors, {'scope': 'all', 'scenarios': entries}, checks


def run_inputs_unchanged(run):
    if run.parameters.get('scope') == 'all':
        entries = run.parameters['scenarios']
        expected_ids = {entry['scenario_id'] for entry in entries}
        scenarios = list(ScenarioModel.objects.order_by('pk'))
        if {scenario.pk for scenario in scenarios} != expected_ids:
            return False
        current = {scenario.pk: snapshot(scenario) for scenario in scenarios}
        return all(fingerprint(current[entry['scenario_id']]) == fingerprint(entry['parameters']) for entry in entries)
    if run.scenario_id:
        return fingerprint(snapshot(run.scenario)) == fingerprint(run.parameters)
    return True
