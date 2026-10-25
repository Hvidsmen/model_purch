"""Bulk filling from a signed, revision-bound coverage result, not the current page."""
from decimal import Decimal
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from base.access import can_access
from ..models import (GlobalCoeffVersion, Goods, Subdivision, DirectorEmployee, GlobalCoeff,
    SubdivisionCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff, TypeCoeff, SegmentCoeff, VariationCalculate)
from .coverage_repair import default_rates
from .directors import lock_editable

SALT = 'motivation.coverage.bulk.v1'


def selection_token(result, version, scope):
    rows = [{key: row[key] for key in ('planning', 'group', 'brand', 'kind', 'subdivision_id', 'employee_id', 'missing', 'formula')}
            for row in result['issues'] if row['missing'] and row['version_id'] == version.pk]
    if not rows:
        return ''
    return signing.dumps({'version': version.pk, 'revision': version.revision, 'scope': scope, 'rows': rows}, salt=SALT, compress=True)


def load(actor, token):
    if not can_access(actor, 'motivation_editor'):
        raise PermissionDenied
    try:
        data = signing.loads(token, salt=SALT, max_age=86400)
    except signing.BadSignature:
        raise ValidationError('Список устарел. Повторите проверку покрытия.')
    version = GlobalCoeffVersion.objects.filter(pk=data['version']).first()
    if not version or not version.is_editable or version.revision != data['revision']:
        raise ValidationError('Черновик изменился. Повторите проверку покрытия.')
    return data, version


def preview(actor, token):
    data, version = load(actor, token)
    branches = {row.pk: row for row in Subdivision.objects.all()}
    director_names = dict(DirectorEmployee.objects.values_list('pk', 'full_name'))
    indexes, items, blocked, seen = {}, [], [], set()
    for row in data['rows']:
        identity = (row['subdivision_id'], row['employee_id'], row['planning'], row['group'], row['brand'], row['kind'])
        if identity in seen:
            continue
        seen.add(identity)
        branch = branches.get(row['subdivision_id']) if data['scope'] == 'subdivision' else None
        if data['scope'] == 'subdivision' and branch is None:
            raise ValidationError('Подразделение изменилось. Повторите проверку.')
        values = default_rates(version, dict(row, employee=row['employee_id']), branch, indexes=indexes)
        missing = row['missing'].split(', ')
        rates = {f'K{i}': str(value) for i, (value, source) in enumerate(values) if f'K{i}' in missing and value is not None}
        item = dict(row, rates=rates, subdivision=branch.subdivision_key if branch else 'Глобально', employee=director_names.get(row['employee_id'], ''))
        if item['formula'] not in dict(GlobalCoeffVersion._meta.get_field('fallback_variation').choices) or not item['formula']:
            item['formula'] = version.fallback_variation or ('Группа планов продаж' if row['planning'] in ('1. RAC', '2. VRF', '3. PROF') else 'Подразделение')
        item['cells'] = [{'segment': segment, 'percent': Decimal(value) * 100} for segment, value in rates.items()]
        if len(rates) != len(missing):
            blocked.append(item)
        else:
            items.append(item)
    prepared = dict(data, rows=[{key: value for key, value in item.items() if key not in ('cells', 'subdivision', 'employee')} for item in items], prepared=True)
    return {'version': version, 'scope': data['scope'], 'items': items, 'blocked': blocked,
            'count': sum(len(item['rates']) for item in items),
            'token': signing.dumps(prepared, salt=SALT, compress=True) if items else ''}


@transaction.atomic
def save(actor, token):
    data, _ = load(actor, token)
    version = lock_editable(data['version'])
    if not data.get('prepared') or version.revision != data['revision']:
        raise ValidationError('Сначала подготовьте список коэффициентов.')
    local = data['scope'] == 'subdivision'
    models = (SubdivisionCoeff, SubdivisionDirectorCoeff) if local else (GlobalCoeff, GlobalDirectorCoeff)
    goods = {(row.planning_group_sales, row.group, row.brand): row for row in Goods.objects.all()}
    used_keys = {row.goods_key for row in goods.values()}
    new_goods = []
    for row in data['rows']:
        key = (row['planning'], row['group'], row['brand'])
        if key not in goods:
            goods_key = ''.join(key)
            if len(goods_key) > 255 or goods_key in used_keys:
                raise ValidationError('Неоднозначный ключ товара. Исправьте справочник и повторите проверку.')
            good = Goods(goods_key=goods_key, planning_group_sales=key[0], group=key[1], brand=key[2])
            goods[key] = good
            new_goods.append(good)
            used_keys.add(goods_key)
    if new_goods:
        Goods.objects.bulk_create(new_goods, batch_size=500)
    kinds = {row.type_coeff_name: row for row in TypeCoeff.objects.all()}
    segments = {row.segment_name.upper().replace('O', 'K'): row for row in SegmentCoeff.objects.all()}
    branches = set(Subdivision.objects.values_list('pk', flat=True))
    employees = set(DirectorEmployee.objects.values_list('pk', flat=True))
    managers = {(good, kind, segment.upper().replace('O', 'K')): value for good, kind, segment, value in GlobalCoeff.objects.filter(version=version).values_list('goods_id', 'type_coeff__type_coeff_name', 'segment__segment_name', 'manager_coeff').iterator(chunk_size=2000)} if local else {}
    formulas, batches, count = {}, {model: [] for model in models}, 0
    for row in data['rows']:
        if local and (row['subdivision_id'] not in branches or row['kind'] == 'Директора' and row['employee_id'] not in employees):
            raise ValidationError('Справочник изменился. Повторите проверку.')
        key = (row['planning'], row['group'], row['brand'])
        formula = row['formula']
        if formula not in dict(GlobalCoeffVersion._meta.get_field('fallback_variation').choices) or not formula:
            formula = version.fallback_variation or ('Группа планов продаж' if row['planning'] in ('1. RAC', '2. VRF', '3. PROF') else 'Подразделение')
        if formula not in formulas:
            formulas[formula], _ = VariationCalculate.objects.get_or_create(variation_name=formula)
        common = dict(version=version, goods=goods[key], variation_calculate=formulas[formula])
        if local:
            common['subdivision_id'] = row['subdivision_id']
        if row['kind'] == 'Директора':
            if local:
                common['employee_id'] = row['employee_id']
            batches[models[1]].append(models[1](**common, **{segment.lower(): Decimal(value) for segment, value in row['rates'].items()}))
        else:
            if row['kind'] not in kinds:
                kinds[row['kind']], _ = TypeCoeff.objects.get_or_create(type_coeff_name=row['kind'])
            for segment, value in row['rates'].items():
                if segment not in segments:
                    segments[segment], _ = SegmentCoeff.objects.get_or_create(segment_name=segment)
                batches[models[0]].append(models[0](**common, type_coeff=kinds[row['kind']], segment=segments[segment], motivation_coeff=Decimal(value), manager_coeff=managers.get((goods[key].pk, row['kind'], segment), 1)))
        count += len(row['rates'])
    for model, objects in batches.items():
        if objects:
            model.objects.bulk_create(objects, batch_size=500)
    return count, version.pk, data['scope']
