import json
from django.core.exceptions import ValidationError
from django.db import OperationalError, connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from .models import GlobalCoeffVersion, SalesPlanScenario, Subdivision, DirectorEmployee, Goods
from .services.comparison import compare_plan, proposed_changes
from .services.approval import review_approval, approve_review


@require_POST
def comparison_action(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or not isinstance(data.get('changes', {}), dict) or not isinstance(data.get('deleted', []), list):
            raise ValidationError('Некорректные параметры сравнения.')
        action = data.get('action', 'compare')
        if action == 'approve':
            from base.access import can_access
            from django.core.exceptions import PermissionDenied
            if not can_access(request.user, 'motivation_admin'):
                raise PermissionDenied
            version = approve_review(data.get('token', ''), data.get('confirm_overwrite') is True, actor=request.user, override_reason=str(data.get('reason') or ''))
            return JsonResponse({'approved': version.pk, 'message': 'Весь набор коэффициентов утверждён. Заменённые версии сохранены в истории.'})
        version = get_object_or_404(GlobalCoeffVersion, pk=int(data['version']))
        plan = get_object_or_404(SalesPlanScenario, pk=int(data['plan']), is_deleted=False) if data.get('plan') else None
        baseline = get_object_or_404(GlobalCoeffVersion, pk=int(data['baseline'])) if data.get('baseline') else None
        sub = get_object_or_404(Subdivision, pk=int(data['subdivision'])) if data.get('subdivision') else None
        if baseline and baseline.is_editable:
            raise ValidationError('Базовой выберите утверждённую или историческую версию, которую нельзя редактировать.')
        changes, deleted = proposed_changes(version, sub, data.get('changes'), data.get('deleted'))
        employee = get_object_or_404(DirectorEmployee, pk=data['employee']) if data.get('employee') else None
        director_changes = data.get('director_changes') or {}
        if not isinstance(director_changes, dict):
            raise ValidationError('Некорректные изменения коэффициентов директора.')
        if director_changes:
            if not version.is_editable or (sub and (not employee or not employee.subdivisions.filter(pk=sub.pk).exists())):
                raise ValidationError('Изменения директора недоступны для этой версии или филиала.')
            director_changes = _normalize_director_changes(director_changes)
            if action == 'review':
                raise ValidationError('Перед утверждением сохраните изменения коэффициентов директоров.')
        formulas=data.get('formula_changes') or {}
        if not isinstance(formulas,dict) or any(not key.startswith(('formula_coeff=','director_formula=')) for key in formulas):raise ValidationError('Некорректные изменения формул.')
        from .services.editor_patch import formulas as normalize_formulas
        staged={**formulas,'revision':str(data.get('revision'))}
        for key,value in director_changes.items():staged['director_coeff='+key.replace(':','=')]=value
        normalized_formulas=normalize_formulas(version,staged,sub,employee)
        if action=='review' and any(normalized_formulas):raise ValidationError('Перед утверждением сохраните изменения формул.')
        managers=data.get('manager_changes') or {}
        if not isinstance(managers,dict):raise ValidationError('Некорректные поправки руководителя.')
        if managers:
            from .models import KindManagerCoeff
            from .services.comparison import coefficient_input
            if not sub or not version.is_editable or str(data.get('revision'))!=str(version.revision):raise ValidationError('Поправки доступны только в текущем черновике подразделения.')
            ids=set()
            for key,value in managers.items():
                if not key.startswith('kind_coeff=') or not key.split('=')[1].isdigit():raise ValidationError('Некорректная поправка руководителя.')
                ids.add(int(key.split('=')[1]));coefficient_input(value)
            if KindManagerCoeff.objects.filter(pk__in=ids).count()!=len(ids):raise ValidationError('Период поправки удалён.')
            if action=='review':raise ValidationError('Сохраните поправки руководителя перед утверждением.')
        for key, value in [('motivation_comparison_plan', plan.pk if plan else None), ('motivation_comparison_baseline', baseline.pk if baseline else None)]:
            if request.session.get(key) != value:
                request.session[key] = value
        if action == 'review':
            from base.access import can_access
            from django.core.exceptions import PermissionDenied
            if not can_access(request.user, 'motivation_admin'):
                raise PermissionDenied
            return JsonResponse(review_approval(version, plan, baseline, sub, changes, deleted))
        if action != 'compare' or plan is None:
            raise ValidationError('Выберите загруженный план продаж для сравнения.')
        result=compare_plan(plan, version, baseline, sub, changes, deleted, director_changes=director_changes, employee=employee)
        if any(normalized_formulas):
            result['unsaved']=True
            result['formula_notice']='Учтены несохранённые формулы. Сравнение показывает возможную премию при 100% выполнения плана; формулы меняют базу и условия фактической выплаты, а не эту сумму.'
        if managers:
            result['unsaved']=True
            result['manager_notice']='Есть несохранённые поправки руководителя. Здесь показана возможная премия по ставкам до этих поправок и условий выплаты; их изменение смотрите в списке правок.'
        return JsonResponse(result)
    except OperationalError as error:
        if connection.vendor == 'sqlite' and any(word in str(error).lower() for word in ('locked', 'busy')):
            return JsonResponse({'error': 'База сейчас занята другой операцией. Дождитесь её завершения и повторите проверку утверждения.'}, status=409)
        raise
    except ValidationError as error:
        return JsonResponse({'error': ' '.join(error.messages)}, status=400)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return JsonResponse({'error': 'Некорректные параметры сравнения.'}, status=400)


def _normalize_director_changes(changes):
    from .services.comparison import coefficient_input
    normalized = {}
    for key, value in changes.items():
        parts = key.split(':')
        if len(parts) != 2 or not parts[0].isdigit() or parts[1] not in {'0', '1', '2', '3', '4'}:
            raise ValidationError('Некорректная ячейка директора.')
        normalized[key] = coefficient_input(value)
    ids = {int(key.split(':')[0]) for key in normalized}
    if Goods.objects.filter(pk__in=ids).count() != len(ids):
        raise ValidationError('Товарная матрица директора не найдена.')
    return normalized
