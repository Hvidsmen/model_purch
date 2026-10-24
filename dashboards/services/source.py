"""Read-only DataWH source. A failed/incomplete read is never cached as a result."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import hashlib
from threading import Lock
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_purch.conns import connect_database

SQL_PATH = Path(__file__).resolve().parents[1] / 'sql' / 'dealer_sales.sql'
MAX_ROWS = 500_000
_source_lock = Lock()


def load_sales(as_of):
    sql = SQL_PATH.read_text(encoding='utf-8')
    end = as_of.replace(day=1)  # Never mix an incomplete month with complete years.
    key = 'dealer-sales:v1:' + hashlib.sha256((sql + str(end)).encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    # Prevent concurrent initial page requests from duplicating an expensive scan.
    with _source_lock:
        cached = cache.get(key)
        if cached is not None:
            return cached
        connection = None
        try:
            connection, cursor = connect_database('vm-dwh', 'DataWH')
            connection.timeout = 120
            cursor.execute(sql, date(2021, 1, 1), end)
            columns = [column[0] for column in cursor.description]
            rows = []
            while True:
                batch = cursor.fetchmany(5000)
                if not batch:
                    break
                for values in batch:
                    row = dict(zip(columns, values))
                    if row['dealer_code'] is None or not str(row['dealer_code']).strip():
                        raise ValidationError('Источник содержит продажи без кода дилера. Консолидация невозможна.')
                    row['dealer_code'] = str(row['dealer_code']).strip()
                    for field in ('dealer_name', 'branch', 'brand', 'equipment', 'region', 'city', 'product_range', 'planning_group'):
                        row[field] = str(row.get(field) or 'Не указан').strip()
                    if isinstance(row['month'], datetime):
                        row['month'] = row['month'].date()
                    row['quantity'] = Decimal(str(row.get('quantity') or 0))
                    row['amount'] = Decimal(str(row.get('amount') or 0))
                    if not row['quantity'].is_finite() or not row['amount'].is_finite():
                        raise ValidationError('Источник содержит некорректные числовые значения.')
                    rows.append(row)
                if len(rows) > MAX_ROWS:
                    raise ValidationError('Источник превысил 500 000 месячных строк. Требуется материализованная витрина; неполные данные не показаны.')
            result = {'rows': rows, 'loaded_at': timezone.now().isoformat(), 'end_exclusive': end.isoformat()}
            cache.set(key, result, 15 * 60)
            return result
        finally:
            if connection is not None:
                connection.close()
