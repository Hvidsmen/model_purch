"""One period and one role-scoped registry for the closing workspace."""
from decimal import Decimal
from base.access import role_for, FINANCIER
from ..models import PeriodMotivationCoefficient
from .navigation_subdivisions import navigation_subdivisions
from .premium_distribution import branches, cash


def registry(user, year, period, batches, grid=None, archived=False):
    allowed = branches(user)
    subdivisions = navigation_subdivisions()
    if allowed is not None:
        subdivisions = subdivisions.filter(subdivision_key__in=allowed)
    coefficients = {c.subdivision_id: c for c in PeriodMotivationCoefficient.objects.select_related('confirmed_by').filter(year=year, period=period)}
    fields = {r['branch'].pk: r for r in grid.rows} if grid else {}
    by_branch = {}
    for batch in batches:
        by_branch.setdefault(batch.subdivision_id, []).append(batch)
    subdivisions=list(subdivisions)
    present={branch.pk for branch in subdivisions}
    for batch in batches:
        if batch.subdivision_id not in present:
            subdivisions.append(batch.subdivision);present.add(batch.subdivision_id)
    from .closing_return import pending
    rows = []
    for branch in subdivisions:
        matching = by_branch.get(branch.pk, [])
        if not matching and (archived or role_for(user, 'motivation') == FINANCIER):
            continue
        for batch in matching or [None]:
            record = coefficients.get(branch.pk)
            stage = batch.source.get('workflow', {}).get('stage', 'sales') if batch else None
            label = 'Завершено' if batch and batch.state == 'completed' else 'Архив' if archived else 'Ребаланс директоров' if stage == 'directors' else 'Ребаланс политиков' if stage == 'policies' else 'Продажи' if batch else 'Готово к распределению' if record else 'Ожидает множителя'
            awaiting = pending(record)
            if awaiting and not batch:label = 'Возвращено гендиректору: подтвердите коэффициенты'
            values = {}
            for kind, prefix in [('Продажи', 'sales'), ('Политики', 'policies'), ('Директора','directors')]:
                for currency in ('USD', 'RUB'):
                    values[prefix+'_'+currency.lower()] = sum((cash(f['amount']) for f in batch.funds if f['kind'] == kind), Decimal(0)) if batch and batch.currency == currency else None
            rows.append({'branch': branch, 'batch': batch, 'record': record,
                         'field': fields.get(branch.pk, {}).get('field'),
                         'locked': bool(batch), 'awaiting_confirmation':awaiting, 'stage_label': label,
                         'can_start': bool(record and not batch and not awaiting), **values})
    counts = {'waiting': sum(not row['record'] or row['awaiting_confirmation'] for row in rows),
              'working': sum(bool(row['batch'] and row['batch'].state == 'draft') for row in rows),
              'completed': sum(bool(row['batch'] and row['batch'].state == 'completed') for row in rows)}
    return rows, counts
