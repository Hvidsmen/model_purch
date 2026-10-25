"""Explicit, revision-checked creation of missing exact cells only."""
from decimal import Decimal, InvalidOperation
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from base.access import can_access
from ..models import (GlobalCoeffVersion, GlobalCoeff, GlobalDirectorCoeff, SubdivisionCoeff,
                      SubdivisionDirectorCoeff, Goods, TypeCoeff, SegmentCoeff, VariationCalculate,
                      Subdivision, DirectorEmployee)
from .directors import lock_editable

SALT = 'motivation.coverage.repair.v1'


def token_for(row, version, scope):
    return signing.dumps({'version': version.pk, 'revision': version.revision, 'scope': scope,
        'planning': row['planning'], 'group': row['group'], 'brand': row['brand'],
        'kind': row['kind'], 'subdivision': row['subdivision_id'], 'employee': row['employee_id']}, salt=SALT, compress=True)


def default_rates(version, data, subdivision=None):
    """Use the same indexes and dated scopes as the plan calculation."""
    from .subdivision_aliases import coefficient_rows
    ids = list(GlobalCoeffVersion.objects.filter(status='approved', effective_from__lt=version.effective_from).values_list('pk', flat=True)) + [version.pk]
    sub = subdivision.subdivision_key if subdivision else ''
    if data['kind'] == 'Директора':
        from .director_comparison import DirectorIndex
        index = DirectorIndex(ids, local=subdivision is not None)
        values, source = index.coefficients(version.pk, sub, data['employee'] if subdivision else 0,
                                             data['planning'], data['group'], data['brand'])
        return [(value, source) for value in values]
    from .comparison import Index, _coefficient_rows
    rows = coefficient_rows(_coefficient_rows(ids, subdivision=subdivision), 1, range(7))
    index = Index(rows)
    result = []
    for i in range(5):
        value = index.cells.get((version.pk, sub, data['planning'], data['group'], data['brand'], data['kind'], f'O{i}'))
        source = 'exact'
        if value is None:
            value = index.group_min.get((version.pk, sub, data['planning'], data['kind']))
            source = 'group_minimum'
        if value is None:
            value = index.table_min.get(data['kind'])
            source = 'table_minimum' if value is not None else 'missing'
        result.append((value, source))
    return result


def proposal(actor, token):
    if not can_access(actor, 'motivation_editor'):
        raise PermissionDenied
    try:
        data = signing.loads(token, salt=SALT, max_age=86400)
    except signing.BadSignature:
        raise ValidationError('Ссылка устарела. Повторите проверку покрытия.')
    version = GlobalCoeffVersion.objects.filter(pk=data['version']).first()
    if version is None:
        raise ValidationError('Версия удалена. Повторите проверку покрытия.')
    if not version.is_editable or str(version.revision) != str(data['revision']):
        raise ValidationError('Черновик изменился. Повторите проверку покрытия.')
    director = data['kind'] == 'Директора'
    local = data['scope'] == 'subdivision'
    model = (SubdivisionDirectorCoeff if director else SubdivisionCoeff) if local else (GlobalDirectorCoeff if director else GlobalCoeff)
    goods = Goods.objects.filter(planning_group_sales=data['planning'], group=data['group'], brand=data['brand']).first()
    filters = {'version': version, 'goods': goods}
    if local:
        filters['subdivision'] = Subdivision.objects.filter(pk=data['subdivision']).first()
        if filters['subdivision'] is None:
            raise ValidationError('Подразделение изменилось. Повторите проверку покрытия.')
        if director:
            filters['employee'] = DirectorEmployee.objects.filter(pk=data['employee']).first()
            if filters['employee'] is None:
                raise ValidationError('Директор изменился. Повторите проверку покрытия.')
    rows = model.objects.filter(**filters) if goods else model.objects.none()
    if goods and local:
        from .subdivision_aliases import expand, mapping
        lookup = {name: value for name, value in filters.items() if name != 'subdivision'}
        rows = model.objects.filter(**lookup, subdivision__subdivision_key__in=expand(filters['subdivision'].subdivision_key, mapping()))
    if not director:
        rows = rows.filter(type_coeff__type_coeff_name=data['kind'])
    existing = rows.first() if director else {row.segment.segment_name.upper().replace('O', 'K'): row for row in rows.select_related('segment')}
    global_model = GlobalDirectorCoeff if director else GlobalCoeff
    source = global_model.objects.filter(version=version, goods=goods) if goods and local else global_model.objects.none()
    if not director:
        source = source.filter(type_coeff__type_coeff_name=data['kind'])
    global_row = source.first() if director else {row.segment.segment_name.upper().replace('O', 'K'): row for row in source.select_related('segment')}
    formulas = set(rows.values_list('variation_calculate__variation_name', flat=True)) - {None, ''}
    formula_name = next(iter(formulas)) if len(formulas) == 1 else version.fallback_variation or ('Группа планов продаж' if data['planning'] in ('1. RAC', '2. VRF', '3. PROF') else 'Подразделение')
    defaults = default_rates(version, data, filters.get('subdivision'))
    source_labels = {'exact': 'Точный коэффициент', 'group_minimum': 'Минимум по группе планов продаж',
                     'table_minimum': 'Минимум по таблице коэффициентов', 'missing': 'Нет исходных ставок — введите процент'}
    cells = []
    for i in range(5):
        segment = f'K{i}'
        present = bool(existing) if director else segment in existing
        candidate = global_row if director else global_row.get(segment)
        value, rate_source = defaults[i]
        label = source_labels[rate_source]
        if director and rate_source == 'missing':
            label = 'Нет исходных ставок директора — по правилу расчёта 0%'
        cells.append({'name': segment, 'present': present, 'value': Decimal(str(value)) * 100 if value is not None else '',
                      'source': label})
    return {'data': data, 'version': version, 'cells': cells, 'filters': filters, 'model': model,
            'formula_name': formula_name, 'global_rows': global_row, 'director': director, 'local': local, 'goods': goods}


@transaction.atomic
def create_missing(actor, token, values):
    info = proposal(actor, token)
    version = lock_editable(info['version'].pk)
    if version.revision != info['data']['revision']:
        raise ValidationError('Черновик изменился. Повторите проверку.')
    rates = {}
    for cell in info['cells']:
        if cell['present']:
            continue
        try:
            value = Decimal(str(values.get(cell['name'], '')).replace(',', '.')) / 100
        except (InvalidOperation, ValueError):
            raise ValidationError(f"Укажите процент для {cell['name']}.")
        if not value.is_finite() or not -1 <= value <= 1:
            raise ValidationError('Процент должен быть от −100 до 100.')
        rates[cell['name']] = value
    if not rates:
        raise ValidationError('Все точные коэффициенты уже существуют.')
    data = info['data']
    goods = info['goods']
    if goods is None:
        goods_key = ''.join((data['planning'], data['group'], data['brand']))
        if len(goods_key) > 255 or Goods.objects.filter(goods_key=goods_key).exists():
            raise ValidationError('Не удалось однозначно создать товарную классификацию. Проверьте справочник товаров.')
        goods = Goods.objects.create(goods_key=goods_key, planning_group_sales=data['planning'], group=data['group'], brand=data['brand'])
    filters = dict(info['filters'], goods=goods)
    formula_name = info['formula_name']
    formula, _ = VariationCalculate.objects.get_or_create(variation_name=formula_name)
    if info['director']:
        info['model'].objects.create(**filters, variation_calculate=formula,
            **{name.lower(): value for name, value in rates.items()})
    else:
        kind, _ = TypeCoeff.objects.get_or_create(type_coeff_name=data['kind'])
        for segment, value in rates.items():
            seg, _ = SegmentCoeff.objects.get_or_create(segment_name=segment)
            candidate = info['global_rows'].get(segment)
            info['model'].objects.create(**filters, type_coeff=kind, segment=seg,
                motivation_coeff=value, manager_coeff=candidate.manager_coeff if candidate else 1,
                variation_calculate=formula)
    return len(rates)
