from ..models import ScenarioModel
from django.http import Http404

SESSION_KEY = 'model_purch_scenario_id'


def current_scenario(request):
    scenarios = ScenarioModel.objects.order_by('-date_start_plan', '-pk')
    selected = request.POST.get('scenario') if request.method == 'POST' else None
    selected = selected or request.GET.get('scenario') or request.session.get(SESSION_KEY)
    try:
        scenario = scenarios.filter(pk=int(selected)).first() if selected else None
    except (TypeError, ValueError):
        scenario = None
    explicit = request.POST.get('scenario') or request.GET.get('scenario')
    if scenario is None and explicit:
        raise Http404('Выбранный сценарий не найден.')
    if scenario is None:
        scenario = scenarios.first()
    if scenario:
        request.session[SESSION_KEY] = scenario.pk
    else:
        request.session.pop(SESSION_KEY, None)
    return scenario, scenarios


def remember_scenario(request, scenario):
    request.session[SESSION_KEY] = scenario.pk
