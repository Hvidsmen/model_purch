from django.conf import settings
from django.urls import reverse
from .services.scenarios import current_scenario


def workspace(request):
    if not request.path.startswith('/model_purch/'):
        return {}
    scenario, scenarios = current_scenario(request)
    route = request.resolver_match.url_name if request.resolver_match else ''
    target = 'purch_list' if route in {'purch_create', 'purch_edit', 'purch_delete'} else 'pggoods_list' if route in {'edit_pggoods', 'delete_pggoods'} else None
    return {'workspace_selection_url': reverse(target) if target else request.path,
            'active_database': str(settings.DATABASES['default']['NAME']),
            'workspace_scenario': scenario, 'workspace_scenarios': scenarios}
