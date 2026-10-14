import logging
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import ScenarioModel
from .services.erp_plan_parameters import source_versions, update_parameters

logger = logging.getLogger(__name__)


@require_http_methods(['GET', 'POST'])
def plan_parameters(request, scenario_id):
    scenario = get_object_or_404(ScenarioModel, pk=scenario_id)
    if request.method == 'POST':
        try:
            if request.POST.get('action') == 'sources':
                request.session['goods_erp_plan_versions'] = source_versions()
                return redirect('goods_plan_parameters', scenario_id=scenario.pk)
            if request.POST.get('action') == 'load':
                if request.POST.get('confirm') != 'yes':
                    raise ValidationError('Подтвердите обновление параметров по плану.')
                matched, missing = update_parameters(scenario, request.POST.getlist('sources'))
                messages.success(request, f'Параметры плана обновлены у {matched} товаров. Без совпадения по группе: {missing}.')
                return redirect(reverse('pggoods_list') + f'?scenario={scenario.pk}')
            raise ValidationError('Неизвестное действие.')
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
        except Exception:
            logger.exception('ERP plan parameter load failed')
            messages.error(request, 'Не удалось загрузить параметры плана. Проверьте подключение DWH_SQL_CONN_STR и журнал сервера. Данные не изменены.')
    choices = sorted(set(request.session.get('goods_erp_plan_versions', []) + scenario.erp_sales_plan_sources))
    return render(request, 'model_purch/plan_parameters.html', {
        'current_scenario': scenario, 'scenarios': ScenarioModel.objects.all(), 'sources': choices,
        'selected_sources': request.POST.getlist('sources') if request.method == 'POST' else scenario.erp_sales_plan_sources,
    })
