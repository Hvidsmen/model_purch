import json
from decimal import Decimal
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction, connection, OperationalError
from django.db.models import F
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_http_methods
from base.access import can_access
from .models import (GlobalCoeffVersion, SalesPlanScenario, Subdivision, CoefficientBalanceLog,
                     GlobalCoeff, SubdivisionCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff)
from .services.balancing import Budget

SALT = 'motivation-coefficient-balancing-v1'


def dataset(data):
    version = get_object_or_404(GlobalCoeffVersion, pk=int(data['version']))
    if not version.is_editable:
        raise ValidationError('Балансировка доступна только в последнем редактируемом черновике.')
    plan = get_object_or_404(SalesPlanScenario, pk=int(data['plan']), is_deleted=False)
    local = data.get('scope') == 'local'
    if data.get('scope') not in {'local', 'global'}:
        raise ValidationError('Выберите глобальные коэффициенты или коэффициенты подразделений.')
    sub = get_object_or_404(Subdivision, pk=int(data['subdivision'])) if local and data.get('subdivision') else None
    if local and data.get('mode') != 'company' and not sub:
        raise ValidationError('Выберите подразделение или режим сохранения бюджета компании.')
    return Budget(version, plan, local, None if data.get('mode') == 'company' else sub)


@require_http_methods(['GET', 'POST'])
def balancing(request):
    if not can_access(request.user, 'motivation_admin'):
        return JsonResponse({'error': 'Балансировка доступна администратору Мотивации.'}, status=403) if request.method == 'POST' else HttpResponseForbidden('Доступно администратору Мотивации.')
    if request.method == 'GET':
        latest = GlobalCoeffVersion.objects.exclude(status='superseded').order_by('-pk').first()
        editable = latest if latest and latest.is_editable else None
        if request.GET.get('version') and (not editable or request.GET['version'] != str(editable.pk)):
            return HttpResponseForbidden('Балансировка доступна только в последнем редактируемом черновике.')
        return render(request, 'admin_motivation/balancing.html', {
            'versions': [editable] if editable else [],
            'plans': SalesPlanScenario.objects.filter(is_deleted=False),
            'subdivisions': Subdivision.objects.all(),
            'initial': {key: request.GET.get(key, '') for key in ('version', 'subdivision', 'plan')},
            'history': CoefficientBalanceLog.objects.select_related('version', 'plan', 'user').order_by('-pk')[:15],
        })
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValidationError('Некорректные параметры балансировки.')
        action = data.get('action')
        if action == 'save':
            return _save(request, data)
        budget = dataset(data)
        if action == 'load':
            return JsonResponse(budget.payload())
        if action != 'preview':
            raise ValidationError('Неизвестное действие.')
        if data.get('fingerprint') != budget.fingerprint:
            raise ValidationError('План или коэффициенты изменились. Загрузите исходные значения заново.')
        result = budget.balance(data.get('manual'), data.get('compensate'), data.get('mode'))
        parameters = {key: data.get(key) for key in ('version', 'plan', 'scope', 'subdivision', 'mode', 'manual', 'compensate', 'fingerprint')}
        result['token'] = signing.dumps({'user': request.user.pk, 'parameters': parameters}, salt=SALT, compress=True)
        result['editable'] = budget.version.is_editable
        return JsonResponse(result)
    except OperationalError as error:
        if connection.vendor == 'sqlite' and any(word in str(error).lower() for word in ('locked', 'busy')):
            return JsonResponse({'error': 'Данные сейчас изменяются другим процессом. Повторите действие после его завершения.'}, status=409)
        raise
    except signing.BadSignature:
        return JsonResponse({'error': 'Предварительный расчёт устарел. Выполните балансировку заново.'}, status=400)
    except ValidationError as error:
        return JsonResponse({'error': ' '.join(error.messages)}, status=400)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return JsonResponse({'error': 'Некорректные параметры балансировки.'}, status=400)


@transaction.atomic
def _save(request, data):
    review = signing.loads(data.get('token', ''), salt=SALT, max_age=900)
    if review['user'] != request.user.pk:
        raise ValidationError('Расчёт принадлежит другому пользователю.')
    params = review['parameters']
    # SQLite ignores row locks: acquire its writer lock before reading the snapshot.
    if connection.vendor == 'sqlite':
        GlobalCoeffVersion.objects.filter(pk=params['version']).update(revision=F('revision'))
    # Lock order matches version creation/approval. Lock the plan before re-reading its source lines.
    list(GlobalCoeffVersion.objects.select_for_update().order_by('pk'))
    get_object_or_404(SalesPlanScenario.objects.select_for_update(), pk=params['plan'], is_deleted=False)
    budget = dataset(params)
    if budget.fingerprint != params['fingerprint']:
        raise ValidationError('План или коэффициенты изменились после расчёта. Загрузите исходные значения заново.')
    result = budget.balance(params['manual'], params['compensate'], params['mode'])
    model = SubdivisionCoeff if budget.local else GlobalCoeff
    director_model = SubdivisionDirectorCoeff if budget.local else GlobalDirectorCoeff
    ordinary = {}
    director_updates = {}
    for key, change in result['changes'].items():
        parts = key.split(':')
        if parts[0] == 'c':
            ordinary[int(parts[1])] = float(change['after'])
        else:
            director_updates.setdefault(int(parts[1]), {})[f'k{parts[2]}'] = Decimal(change['after'])
    rows = list(model.objects.filter(pk__in=ordinary, version=budget.version))
    for row in rows:
        row.motivation_coeff = ordinary[row.pk]
    if rows:
        model.objects.bulk_update(rows, ['motivation_coeff'], batch_size=200)
    rows = list(director_model.objects.filter(pk__in=director_updates, version=budget.version))
    for row in rows:
        for field, value in director_updates[row.pk].items():
            setattr(row, field, value)
    if rows:
        director_model.objects.bulk_update(rows, [f'k{i}' for i in range(5)], batch_size=100)
    # Verify the stored representation using a fresh read, not the preview numbers.
    fresh = dataset(params)
    actual = fresh.totals({key: Decimal(cell['value']) for key, cell in fresh.cells.items()})
    expected = {(row['subdivision'], row['kind']): Decimal(row['after']) for row in result['totals']}
    if actual != expected:
        raise ValidationError('Сохранённые коэффициенты не прошли проверку премии. Изменения отменены.')
    log = CoefficientBalanceLog.objects.create(version=budget.version, plan=budget.plan, user=request.user,
        parameters={**{key: value for key, value in params.items() if key != 'fingerprint'},
                    'version_snapshot': {'id': budget.version.pk, 'title': str(budget.version), 'effective_from': budget.version.effective_from.isoformat()}}, result=result, fingerprint=budget.fingerprint)
    return JsonResponse({'message': 'Балансировка сохранена в черновик. Плановая премия проверена. Для применения утвердите версию и выполните выгрузку и расчёт.', 'log': log.pk, 'version': budget.version.pk})
