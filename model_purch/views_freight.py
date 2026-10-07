from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_http_methods

from .forms import FreightForm, FreightCopyForm, FreightScenarioForm
from .models import Freight, ScenarioModel


def freight_redirect(scenario=None):
    url = reverse('freight')
    if scenario:
        url += f'?scenario={scenario.pk}'
    return redirect(url)


@require_http_methods(['GET', 'POST'])
def freight_page(request):
    if request.method == 'POST':
        selection = FreightScenarioForm(request.POST)
        if not selection.is_valid():
            messages.error(request, 'Не выбран действующий сценарий. Данные не сохранены.')
            return freight_redirect()
        scenario = selection.cleaned_data['scenario']
    else:
        if request.GET.get('scenario'):
            selection = FreightScenarioForm(request.GET)
            if not selection.is_valid():
                messages.error(request, 'Выбранный сценарий не найден.')
                return freight_redirect()
            scenario = selection.cleaned_data['scenario']
        else:
            scenario = ScenarioModel.objects.order_by('-date_start_plan', '-pk').first()
    freight = Freight.objects.filter(scenario=scenario).first() if scenario else None
    form = FreightForm(request.POST if request.method == 'POST' else None, instance=freight)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            Freight.objects.update_or_create(scenario=scenario, defaults={
                name: form.cleaned_data[name] for name in form.Meta.fields
            })
        messages.success(request, 'Параметры фрахта сохранены.')
        return freight_redirect(scenario)
    scenarios = ScenarioModel.objects.order_by('name', 'pk')
    return render(request, 'model_purch/freight.html', {
        'current_scenario': scenario, 'scenarios': scenarios, 'form': form,
        'source_scenarios': scenarios.exclude(pk=scenario.pk) if scenario else scenarios.none(),
    })


@require_POST
def copy_freight(request):
    form = FreightCopyForm(request.POST)
    if not form.is_valid():
        messages.error(request, ' '.join(str(error) for errors in form.errors.values() for error in errors))
        return freight_redirect(form.cleaned_data.get('scenario'))
    target = form.cleaned_data['scenario']
    source = form.cleaned_data['source_scenario_id']
    with transaction.atomic():
        freight = Freight.objects.filter(scenario=source).first()
        if freight is None:
            messages.error(request, 'В сценарии-источнике параметры фрахта не заполнены.')
        else:
            Freight.objects.update_or_create(scenario=target, defaults={
                'price_per_container': freight.price_per_container,
                'volume_per_container': freight.volume_per_container,
            })
            messages.success(request, f'Параметры фрахта скопированы из сценария «{source.name}».')
    return freight_redirect(target)
