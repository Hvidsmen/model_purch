from .models import ScenarioModel

def get_current_scenario(request):
    """Возвращает (current_scenario, all_scenarios). По умолчанию — последний созданный."""
    all_scenarios = ScenarioModel.objects.all().order_by('-date_start_plan', '-id')
    scenario_id = request.GET.get('scenario')

    if scenario_id:
        try:
            current_scenario = ScenarioModel.objects.get(pk=scenario_id)
        except ScenarioModel.DoesNotExist:
            current_scenario = all_scenarios.first()
    else:
        current_scenario = all_scenarios.first()

    return current_scenario, all_scenarios