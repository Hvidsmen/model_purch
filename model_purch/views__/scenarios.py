import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db import transaction
from django.urls import reverse

from .models import ScenarioModel
from .forms import ScenarioModelForm, ScenarioPlanSalesFormSet
from .core import get_current_scenario
from .sync import sync_purch_data_for_scenario, sync_pggoods_data_for_scenario

logger = logging.getLogger(__name__)

def scenario_list(request):
    scenarios = ScenarioModel.objects.all().order_by('-date_start_plan')
    return render(request, 'model_purch/scenario_list.html', {'scenarios': scenarios})

def scenario_create(request):
    if request.method == 'POST':
        scenario_form = ScenarioModelForm(request.POST)
        plan_formset = ScenarioPlanSalesFormSet(request.POST, prefix='plans')

        if scenario_form.is_valid() and plan_formset.is_valid():
            scenario = scenario_form.save()
            plan_formset.instance = scenario
            plan_formset.save()

            try:
                sync_purch_data_for_scenario(scenario)
                sync_pggoods_data_for_scenario(scenario)
                messages.success(request, f'Сценарий "{scenario.name}" создан, данные синхронизированы!')
            except Exception as e:
                logger.error(f"Ошибка синхронизации: {e}")
                messages.warning(request, f'Сценарий создан, но ошибка загрузки данных MS SQL: {e}')
            return redirect('scenario_list')
    else:
        scenario_form = ScenarioModelForm()
        plan_formset = ScenarioPlanSalesFormSet(prefix='plans')

    return render(request, 'model_purch/scenario_form.html', {
        'scenario_form': scenario_form, 'plan_formset': plan_formset, 'title': 'Создание сценария'
    })

def scenario_edit(request, pk):
    scenario = get_object_or_404(ScenarioModel, pk=pk)
    if request.method == 'POST':
        scenario_form = ScenarioModelForm(request.POST, instance=scenario)
        plan_formset = ScenarioPlanSalesFormSet(request.POST, instance=scenario, prefix='plans')

        if scenario_form.is_valid() and plan_formset.is_valid():
            scenario_form.save()
            plan_formset.save()

            try:
                sync_purch_data_for_scenario(scenario)
                sync_pggoods_data_for_scenario(scenario)
                messages.success(request, f'Сценарий "{scenario.name}" обновлён, данные синхронизированы!')
            except Exception as e:
                logger.error(f"Ошибка синхронизации: {e}")
                messages.warning(request, f'Сценарий обновлён, но ошибка загрузки данных MS SQL: {e}')
            return redirect('scenario_list')
    else:
        scenario_form = ScenarioModelForm(instance=scenario)
        plan_formset = ScenarioPlanSalesFormSet(instance=scenario, prefix='plans')

    return render(request, 'model_purch/scenario_form.html', {
        'scenario_form': scenario_form, 'plan_formset': plan_formset,
        'title': f'Редактирование: {scenario.name}', 'scenario': scenario
    })

def scenario_delete(request, pk):
    scenario = get_object_or_404(ScenarioModel, pk=pk)
    if request.method == 'POST':
        scenario_name = scenario.name
        scenario.delete()
        messages.success(request, f'Сценарий "{scenario_name}" удалён!')
        return redirect('scenario_list')
    return render(request, 'model_purch/scenario_confirm_delete.html', {'scenario': scenario})