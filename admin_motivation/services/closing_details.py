"""CEO overrides and amount-weighted impact; amounts are never averaged factors."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.core.exceptions import ValidationError

KINDS = ('Продажи', 'Политики', 'Директора')
PERSONAL = ('Политики', 'Директора')
CENT = Decimal('.01')


def factor(value):
    try:
        value = Decimal(str(value).replace(',', '.'))
        if not value.is_finite() or not 0 <= value <= Decimal('99999999.9999') or value != value.quantize(Decimal('.0001')):
            raise ValueError
        return value
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError('Коэффициент: неотрицательное число, до четырёх знаков после запятой.')


def identity(value):
    return ' '.join(str(value or '').split()).casefold()


def normalize_settings(kinds, people):
    if not isinstance(kinds, dict) or not isinstance(people, dict) or set(kinds)-set(KINDS) or set(people)-set(PERSONAL):
        raise ValidationError('Недоступный вид мотивации.')
    normalized = {}
    for kind, members in people.items():
        if not isinstance(members, dict) or len(members) > 500:
            raise ValidationError('Не более 500 персональных коэффициентов на вид мотивации.')
        target = {}; seen = set()
        for author, value in members.items():
            author = ' '.join(str(author).split())
            if not author or len(author) > 255 or identity(author) in seen:
                raise ValidationError('Укажите уникальное полное ФИО сотрудника.')
            seen.add(identity(author)); target[author] = str(factor(value))
        normalized[kind] = target
    return {kind: str(factor(value)) for kind, value in kinds.items()}, normalized


def effective(general, kinds, people, kind, author, *, normalized=False):
    if kind in PERSONAL:
        members=people.get(kind,{})
        if author in members:
            return Decimal(str(members[author]))
        key=identity(author)
        if key in members:
            return Decimal(str(members[key]))
        if not normalized:
            for name, value in members.items():
                if identity(name) == key:
                    return Decimal(str(value))
    return Decimal(str(kinds.get(kind, general)))


def impact(rows, general=1, kinds=None, people=None):
    kinds, people = normalize_settings(kinds or {}, people or {})
    general = factor(general)
    def total(members):
        before = sum((Decimal(str(r['before'])) for r in members), Decimal(0))
        after = sum((r['after'] for r in members), Decimal(0))
        return dict(before=before, after=after, change=after-before,
                    coefficient=after/before if before else None,
                    percent=(after/before-1)*100 if before else None)
    detail = []
    for row in rows:
        value = effective(general, kinds, people, row['kind'], row['author'])
        before = Decimal(str(row['before']))
        missing = not identity(row['author']) or identity(row['author']) in ('—', '-')
        unallocated = row.get('author_status') == 'unallocated'
        label = 'Нераспределённая премия' if unallocated else ('Автор не указан' if missing else row['author'])
        detail.append(dict(row, author_label=label, can_edit_person=not missing and not unallocated and row['kind'] in PERSONAL, before=before.quantize(CENT, rounding=ROUND_HALF_UP), factor=value, after=(before*value).quantize(CENT, rounding=ROUND_HALF_UP)))
    result = total(detail)
    result['kinds'] = [dict(kind=kind, **total([r for r in detail if r['kind']==kind])) for kind in KINDS]
    result['payments'] = [dict(payment=payment, label=label, **total([r for r in detail if r['payment']==payment]))
                          for payment, label in [('quarter','Квартальная премия'),('annual','Годовая премия'),('catchup','Доплата за кварталы')]
                          if any(r['payment']==payment for r in detail)]
    result['rows'] = detail
    return result


def source_rows(execution, period, currency):
    """Three grouped queries for all branches, cached by the validated execution."""
    from django.core.cache import cache
    from . import reports_next
    from .reports import load_report
    from .closing_coefficient import without_correction
    if execution.parameters.get('manager_correction_mode') != 'period-unified-v1':
        raise ValidationError('Нужен новый расчёт с единым коэффициентом: старый уже содержит поправки руководителя.')
    variant = execution.parameters.get('variant', 'main')
    if variant == 'main' and period in (1,2,3) and period not in execution.parameters.get('premium_amount_quarters', []):
        raise ValidationError('Запустите основной расчёт с выбранным кварталом.')
    key = f'closing-before-v2:{execution.pk}:{execution.completed_at}:{period}:{currency}'
    with reports_next.current_run(execution):
        cached = cache.get(key)
        if cached is not None:
            return cached
        token = without_correction.set(True)
        result = []
        try:
            for kind in KINDS:
                data = load_report('manager-detail', {'TypeCoeff_':kind, 'Subdivision':[], 'DealAuthor':[],
                    'gp_sales':[], 'GPGoods':[], 'Brend':[], 'Years':[str(execution.parameters['fact_year'])], 'Currency':currency},
                    premium_period=[str(period)], summary_only=True, calculation_variant=variant)
                summary = data['period_summary']
                index = next((i for i,c in enumerate(summary['columns']) if reports_next.period_code(c['label'])==str(period)), None)
                if index is None:
                    raise ValidationError('В расчёте отсутствует выбранный период.')
                for row in summary['rows']:
                    for payment in (['annual','catchup'] if period==5 else ['quarter']):
                        amount = row.get('remainder') if payment=='catchup' else row['values'][index]
                        if amount is not None:
                            result.append(dict(branch=row['subdivision'], kind=kind, author='' if row.get('unallocated') else row['author'],
                                               author_status='unallocated' if row.get('unallocated') else 'employee',
                                               payment=payment, before=str(Decimal(str(amount)))))
        finally:
            without_correction.reset(token)
        cache.set(key, result, 600)
        return result
