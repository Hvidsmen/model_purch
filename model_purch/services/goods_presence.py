"""On-demand source checks, SELECT only; never synchronize or delete scenario goods."""
import hashlib
import re
from decimal import Decimal
from pathlib import Path
from django.core.exceptions import ValidationError
from django.utils import timezone
from ..conns import connect_database
from .erp_plan_parameters import attached_sources

SQL = (Path(__file__).resolve().parents[1] / 'sql' / 'goods_presence.sql').read_text(encoding='utf-8')
FAST_SQL = re.sub(r'\), stock_cost AS \(.*?\n\), stock_initial AS \(',
    '), stock_cost AS (\n SELECT CAST(NULL AS float) AS Quantity, CAST(NULL AS float) AS AccountingQuantity, CAST(NULL AS float) AS Amount WHERE 1=0\n), stock_initial AS (',
    SQL, count=1, flags=re.S)


def presence_cache_key(good, include_cost, sources=None):
    scope = (str(good.scenario_plan.date_start_plan), str(good.scenario_plan.date_end_plan),
             good.planning_group, attached_sources(good.scenario_plan) if sources is None else sources, include_cost)
    return 'goods-presence:' + hashlib.sha256((SQL + repr(scope)).encode('utf-8')).hexdigest()

SOURCES = {
    'plan_erp': ('План продаж ERP', 'План и период сценария; источник новых товаров и плановых параметров.', 'accounting'),
    'plan_initial': ('План первоначальной загрузки', 'План из источника первоначальной загрузки и синхронизации сценария.', 'raw'),
    'stock_cost': ('Сток для расчёта DDP', 'Остатки по себестоимости на дату начала сценария.', 'accounting'),
    'stock_initial': ('Сток первоначальной загрузки', 'Наличие и товар в пути на складах продажи на дату начала сценария.', 'raw'),
    'income': ('Ожидаемые поступления', 'Ожидаемая дата с учётом правил поступления должна входить в период сценария.', 'raw'),
}


def check_goods_presence(good, connector=None, include_cost=True):
    scenario = good.scenario_plan
    sources = attached_sources(scenario)
    if len(sources) > 100 or any(len(name) > 255 for name in sources):
        raise ValidationError('Некорректный список планов сценария.')
    statement = (SQL if include_cost else FAST_SQL).replace('/*SCENARIOS*/', ','.join('?' for _ in sources) or 'NULL')
    connection, cursor = (connector or connect_database)('vm-dwh', 'ModelPurch')
    try:
        connection.timeout = 60
        cursor.execute(statement, str(scenario.date_start_plan), str(scenario.date_end_plan), good.planning_group, *sources, *sources)
        values = {key: (count, quantity, accounting, amount) for key, count, quantity, accounting, amount in cursor.fetchall()}
        if set(values) != set(SOURCES):
            raise ValidationError('Источник вернул неполный результат проверки. Отсутствие товара не подтверждено.')
        rows = []
        for key, (label, note, basis) in SOURCES.items():
            count, raw, accounting, amount = values[key]
            raw, accounting, amount = [None if value is None else Decimal(str(value)) for value in (raw, accounting, amount)]
            if any(value is not None and not value.is_finite() for value in (raw, accounting, amount)):
                raise ValidationError('Источник вернул некорректные числовые значения.')
            checked = key != 'stock_cost' or include_cost
            qualifying = accounting if basis == 'accounting' else raw
            rows.append({'key': key, 'label': label, 'note': note, 'found': count > 0,
                         'checked': checked, 'qualifies': checked and qualifying is not None and qualifying > 0,
                         'quantity': raw, 'accounting_quantity': accounting, 'amount': amount,
                         'ddp': amount / accounting if amount is not None and accounting is not None and accounting > 0 else None})
        return {'rows': rows, 'supported': any(row['qualifies'] for row in rows),
                'plans': sources, 'checked_at': timezone.now(), 'complete': include_cost}
    finally:
        connection.close()
