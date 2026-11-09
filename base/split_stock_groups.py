"""Read-only group presence from the existing ERP StreamGoods source.

This adds scenario composition only. It does not import quantities into the plan
or assume warehouse/receipt-date rules from the purchasing module.
"""
import logging
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.utils import timezone
from model_purch.conns import connect_database
from .sales_planning_scope import PRODUCT_GROUP, split_groups
from .sales_planning_group_service import group_key


SNAPSHOT_SQL = 'SELECT COUNT_BIG(*) FROM DataWH.erp.StreamGoods WHERE ReportDate = ?'
PRESENCE_SQL = '''SELECT n.Даичи_ГруппаПланирования,
    SUM(COALESCE(sg.ВНаличии, 0)) AS StockQuantity,
    SUM(COALESCE(sg.ВПути, 0)) AS TransitQuantity
FROM DataWH.erp.StreamGoods sg
INNER JOIN DataWH.erp.[Справочники.Номенклатура] n ON n.Ссылка = sg.НоменклатураСсылка
WHERE sg.ReportDate = ? AND LTRIM(RTRIM(n.Родитель)) = ?
GROUP BY n.Даичи_ГруппаПланирования, sg.НоменклатураСсылка
HAVING SUM(COALESCE(sg.ВНаличии, 0)) > 0 OR SUM(COALESCE(sg.ВПути, 0)) > 0'''


def groups_from_stock(day):
    connection = cursor = None
    try:
        connection, cursor = connect_database('vm-dwh', 'DataWH')
        connection.timeout = 60
        cursor.execute(SNAPSHOT_SQL, str(day))
        snapshot = cursor.fetchone()
        if not snapshot or not snapshot[0]:
            raise ValidationError('В DataWH нет среза стока на ' + day.strftime('%d.%m.%Y') + '. Состав плана не изменён.')
        cursor.execute(PRESENCE_SQL, str(day), PRODUCT_GROUP)
        rows = cursor.fetchall()
    except ValidationError:
        raise
    except Exception:
        logging.getLogger(__name__).exception('Split stock group selection failed')
        raise ValidationError('Не удалось получить сток и товар в пути из DataWH. Проверьте подключение и журнал сервера. Состав плана не изменён.')
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            if connection is not None:
                connection.close()
    available = {group.source_key: group for group in split_groups().filter(is_active=True)}
    found, missing = {}, set()
    for name, stock, transit in rows:
        try:
            stock, transit = Decimal(str(stock)), Decimal(str(transit))
            if not stock.is_finite() or not transit.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            raise ValidationError('Источник вернул некорректные количества. Состав плана не изменён.')
        if stock <= 0 and transit <= 0:
            continue
        name = str(name or '').strip()
        if not name:
            raise ValidationError('На стоке или в пути есть номенклатура без группы планирования. Исправьте источник и обновите справочники.')
        group = available.get(group_key(name))
        if group is None:
            missing.add(name)
            continue
        entry = found.setdefault(str(group.pk), {'id': str(group.pk), 'name': group.name, 'stock': False, 'transit': False})
        entry['stock'] = entry['stock'] or stock > 0
        entry['transit'] = entry['transit'] or transit > 0
    if missing:
        raise ValidationError('На стоке или в пути найдены группы вне текущего активного справочника: '
            + ', '.join(sorted(missing)[:10]) + '. Обновите номенклатуру и группы планирования; состав пока не изменён.')
    return {'date': str(day), 'checked_at': timezone.now().isoformat(), 'source': 'DataWH.erp.StreamGoods',
            'groups': sorted(found.values(), key=lambda row: row['name'])}
