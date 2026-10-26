"""Read-only journal presentation; historical values are never replayed into approved versions."""
from decimal import Decimal, InvalidOperation
from base.access import can_access
from ..models import CoefficientEditLog, Goods, VariationCalculate


def rows(log):
    labels = {good.pk:' / '.join([good.planning_group_sales or '—', good.group or '—', good.brand or '—']) for good in Goods.objects.filter(pk__in={change['goods_id'] for change in log.changes if change.get('goods_id')})}
    formulas = dict(VariationCalculate.objects.values_list('pk','variation_name'))
    def value(change, key):
        value = change[key]
        if value is None and change['field']=='variation_calculate_id':
            return 'По умолчанию'
        if value is None:
            return 'Нет записи' if key == 'before' else 'Удалено'
        if change['field'] == 'variation_calculate_id':
            return formulas.get(int(value), 'Формула #'+str(value))
        if isinstance(value, dict):
            return str(value)
        try:
            return format(Decimal(value)*100, 'f').rstrip('0').rstrip('.')+'%' if '.' in format(Decimal(value)*100, 'f') else format(Decimal(value)*100, 'f')+'%'
        except (InvalidOperation, TypeError):
            return str(value)
    for change in log.changes:
        field = change['field']
        yield [change.get('goods_label') or labels.get(change.get('goods_id'), 'Поправки руководителя'),
               'Удаление записи' if field == 'delete' else change.get('kind','')+' / Формула' if field == 'variation_calculate_id' else ' / '.join([change.get('kind','Ставка'),change.get('segment',field.upper())]),
               value(change,'before'),value(change,'after')]


def history_context(user, version, subdivision=None, employee=None):
    if not can_access(user,'motivation_editor'):
        return {}
    logs = list(CoefficientEditLog.objects.filter(version=version, subdivision=subdivision, employee=employee).order_by('-pk')[:20])
    for log in logs:
        from itertools import islice
        log.preview_rows=list(islice(rows(log),50))
    return {'coefficient_edit_history':logs}
