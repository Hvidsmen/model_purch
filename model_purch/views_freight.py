from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST, require_http_methods

from .forms import FreightForm, FreightCopyForm, FreightScenarioForm
from .models import Freight


def freight_redirect(scenario=None):
    url = reverse('pggoods_list')
    if scenario:
        url += f'?scenario={scenario.pk}'
    url += "#freight-settings"
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
        from .services.scenarios import current_scenario
        scenario, _ = current_scenario(request)
        return freight_redirect(scenario)
    freight = Freight.objects.filter(scenario=scenario).first()
    form = FreightForm(request.POST, instance=freight)
    if form.is_valid():
        with transaction.atomic():
            Freight.objects.update_or_create(scenario=scenario, defaults={
                'price_per_container': form.cleaned_data['price_per_container'],
            })
        messages.success(request, 'Цена фрахта сохранена.')
        return freight_redirect(scenario)
    from .views import pggoods_list
    return pggoods_list(request, freight_form=form, freight_scenario=scenario)


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
            })
            messages.success(request, f'Параметры фрахта скопированы из сценария «{source.name}».')
    return freight_redirect(target)
