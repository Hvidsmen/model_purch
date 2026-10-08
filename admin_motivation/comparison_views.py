import json
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from .models import GlobalCoeffVersion, SalesPlanScenario, Subdivision
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
            version = approve_review(data.get('token', ''), data.get('confirm_overwrite') is True)
            return JsonResponse({'approved': version.pk, 'message': 'Весь набор коэффициентов утверждён. Заменённые версии сохранены в истории.'})
        version = get_object_or_404(GlobalCoeffVersion, pk=int(data['version']))
        plan = get_object_or_404(SalesPlanScenario, pk=int(data['plan'])) if data.get('plan') else None
        baseline = get_object_or_404(GlobalCoeffVersion, pk=int(data['baseline'])) if data.get('baseline') else None
        sub = get_object_or_404(Subdivision, pk=int(data['subdivision'])) if data.get('subdivision') else None
        if baseline and baseline.is_editable:
            raise ValidationError('Базовой выберите утверждённую или историческую версию, которую нельзя редактировать.')
        changes, deleted = proposed_changes(version, sub, data.get('changes'), data.get('deleted'))
        for key, value in [('motivation_comparison_plan', plan.pk if plan else None), ('motivation_comparison_baseline', baseline.pk if baseline else None)]:
            if request.session.get(key) != value:
                request.session[key] = value
        if action == 'review':
            return JsonResponse(review_approval(version, plan, baseline, sub, changes, deleted))
        if action != 'compare' or plan is None:
            raise ValidationError('Выберите загруженный план продаж для сравнения.')
        return JsonResponse(compare_plan(plan, version, baseline, sub, changes, deleted))
    except ValidationError as error:
        return JsonResponse({'error': ' '.join(error.messages)}, status=400)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return JsonResponse({'error': 'Некорректные параметры сравнения.'}, status=400)
