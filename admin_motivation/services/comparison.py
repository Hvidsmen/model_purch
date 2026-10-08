"""Three read-only budget variants over the same loaded sales plan."""
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from ..models import GlobalCoeffVersion, GlobalCoeff, SubdivisionCoeff
from .sales_plans import KINDS, SEGMENTS, number


def coefficient_input(value):
    text = str(value).strip().replace(',', '.')
    try:
        result = Decimal(text.rstrip('%')) / (100 if text.endswith('%') else 1)
    except (InvalidOperation, ValueError):
        raise ValidationError('Некорректный коэффициент в предварительном расчёте.')
    if not result.is_finite() or not -1 <= result <= 1:
        raise ValidationError('Коэффициенты должны быть от -100% до 100%.')
    return result


def proposed_changes(version, subdivision, changes=None, deleted=None):
    model = SubdivisionCoeff if subdivision else GlobalCoeff
    queryset = model.objects.filter(version=version)
    if subdivision:
        queryset = queryset.filter(subdivision=subdivision)
    rows = {str(row.pk): row for row in queryset}
    normalized = {}
    for pk, value in (changes or {}).items():
        if str(pk) not in rows:
            raise ValidationError('Изменения содержат коэффициент другой версии или подразделения.')
        parsed = Decimal(str(float(coefficient_input(value))))
        if parsed != Decimal(str(rows[str(pk)].motivation_coeff)):
            normalized[str(pk)] = str(parsed)
    goods = {row.goods_id for row in rows.values()}
    try:
        deleted = sorted({int(pk) for pk in (deleted or [])})
    except (ValueError, TypeError):
        raise ValidationError('Некорректный список удаляемых товаров.')
    if subdivision and deleted:
        raise ValidationError('Удаление товаров здесь доступно только для глобальных коэффициентов.')
    if set(deleted) - goods:
        raise ValidationError('Удаляемый товар не принадлежит выбранной версии.')
    if (normalized or deleted) and not version.is_editable:
        raise ValidationError('Утверждённая или историческая версия доступна только для просмотра.')
    return normalized, deleted


class Index:
    def __init__(self, rows):
        self.cells, self.group_min, self.table_min, self.cache = {}, {}, {}, {}
        for row in rows:
            version, sub, pg, group, brand, kind, segment, value = row
            segment = segment.upper().replace('K', 'O', 1)
            if kind not in KINDS or segment not in SEGMENTS:
                continue
            value = coefficient_input(value)
            key = (version, sub, pg, group, brand, kind, segment)
            if key in self.cells:
                raise ValidationError('Неоднозначные коэффициенты для одной товарной классификации.')
            self.cells[key] = value
            scope = (version, sub, pg, kind)
            self.group_min[scope] = min(self.group_min.get(scope, value), value)
            self.table_min[kind] = min(self.table_min.get(kind, value), value)

    def coefficients(self, version, sub, pg, group, brand):
        scope = (version, sub, pg, group, brand)
        if scope not in self.cache:
            matrix, fallback_count = [], 0
            for kind in KINDS:
                values = []
                for segment in SEGMENTS:
                    key = (*scope, kind, segment)
                    value = self.cells.get(key)
                    if value is None:
                        fallback_count += 1
                        value = self.group_min.get((version, sub, pg, kind), self.table_min.get(kind))
                    if value is None:
                        raise ValidationError(f'Нет коэффициентов для сравнения: {sub or "Глобальный план"}, {pg}, {kind}.')
                    values.append(value)
                matrix.append(values)
            if len(self.cache) >= 10000:
                self.cache.clear()
            self.cache[scope] = (matrix, fallback_count)
        return self.cache[scope]


def coefficient_rows(version_ids, subdivision=None, current=None, changes=None, deleted=None, changed_subdivision=None):
    model = SubdivisionCoeff if subdivision else GlobalCoeff
    fields = ['version_id', 'goods__planning_group_sales', 'goods__group', 'goods__brand',
              'type_coeff__type_coeff_name', 'segment__segment_name', 'motivation_coeff', 'pk', 'goods_id']
    if subdivision:
        fields.insert(1, 'subdivision__subdivision_key')
    for row in model.objects.filter(version_id__in=version_ids).values_list(*fields).iterator(chunk_size=2000):
        row = row if subdivision else (row[0], '', *row[1:])
        version, sub, pg, group, brand, kind, segment, value, pk, goods = row
        matching_sub = sub == changed_subdivision.subdivision_key if changed_subdivision else not sub
        if current and version == current.pk and matching_sub:
            if goods in (deleted or []):
                continue
            value = (changes or {}).get(str(pk), value)
        yield (version, sub, pg, group, brand, kind, segment, value)


def compare_plan(plan, current, baseline=None, subdivision=None, changes=None, deleted=None, all_subdivisions=False, change_subdivision=None):
    approved = list(GlobalCoeffVersion.objects.filter(status='approved').order_by('effective_from', 'pk'))
    before = [version for version in approved if version.effective_from < current.effective_from]
    proposed = [*before, current]
    use_subdivision = subdivision or all_subdivisions
    profiles = [('approved', approved), ('baseline', [baseline] if baseline else []), ('current', proposed)]
    results, indexes, date_cache = {}, {}, {}
    for name, versions in profiles:
        if not versions:
            results[name] = {'error': 'Нет утверждённых версий.' if name == 'approved' else 'Базовая версия не выбрана.'}
            continue
        indexes[name] = Index(coefficient_rows([version.pk for version in versions], use_subdivision,
            current=current if name == 'current' else None, changes=changes, deleted=deleted, changed_subdivision=change_subdivision or subdivision))
        results[name] = {'policies': Decimal(0), 'sales': Decimal(0), 'total': Decimal(0), 'fallbacks': 0}
    lines = plan.lines.exclude(subdivision='') if use_subdivision else plan.lines.filter(subdivision='')
    if subdivision:
        lines = lines.filter(subdivision=subdivision.subdivision_key)
    found = False
    for on_date, sub, pg, group, brand, amounts in lines.order_by('pk').values_list(
            'plan_date', 'subdivision', 'planning_group_sales', 'group', 'brand', 'segment_amounts').iterator(chunk_size=2000):
        found = True
        usd = [number(amounts.get(segment)) for segment in SEGMENTS]
        for name, versions in profiles:
            if results[name].get('error'):
                continue
            date_key = (name, on_date)
            if date_key not in date_cache:
                chosen = baseline if name == 'baseline' else next((v for v in reversed(versions) if v.effective_from <= on_date), None)
                date_cache[date_key] = chosen.pk if chosen else None
            try:
                matrix, fallbacks = indexes[name].coefficients(date_cache[date_key], sub, pg, group, brand)
            except ValidationError as error:
                results[name] = {'error': ' '.join(error.messages)}
                continue
            policies = sum((value * amount for value, amount in zip(matrix[0], usd)), Decimal(0))
            sales = sum((value * amount for value, amount in zip(matrix[1], usd)), Decimal(0))
            results[name]['policies'] += policies
            results[name]['sales'] += sales
            results[name]['total'] += policies + sales
            results[name]['fallbacks'] += fallbacks
    if not found:
        raise ValidationError('В выбранном плане нет строк для этого разреза. Сначала загрузите план.')
    output = {}
    for name, result in results.items():
        output[name] = {key: str(value) if isinstance(value, Decimal) else value for key, value in result.items()}
    deltas = {}
    for name in ['approved', 'baseline']:
        deltas[name] = {}
        for field in ['policies', 'sales', 'total']:
            if results[name].get('error') or results['current'].get('error'):
                deltas[name][field] = {'amount': None, 'percent': None}
            else:
                old, new = results[name][field], results['current'][field]
                change = new - old
                percent = change / abs(old) * 100 if old else (Decimal(0) if new == 0 else None)
                deltas[name][field] = {'amount': str(change), 'percent': str(percent) if percent is not None else None}
    return {'variants': output, 'deltas': deltas, 'current_date': current.effective_from.isoformat(),
            'unsaved': bool(changes or deleted), 'replaced_versions': [{'id': v.pk, 'title': str(v)} for v in approved if v.pk != current.pk and v.effective_from >= current.effective_from],
            'baseline_replaced': bool(baseline and current.effective_from <= baseline.effective_from)}
