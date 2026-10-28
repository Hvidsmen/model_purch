from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from .models import ScenarioModel
from .services.scenario_setup import finish_setup


@require_POST
def dismiss_setup(request, scenario_id):
    scenario = get_object_or_404(ScenarioModel, pk=scenario_id)
    finish_setup(request, scenario)
    return redirect(reverse('pggoods_list') + f'?scenario={scenario.pk}')
