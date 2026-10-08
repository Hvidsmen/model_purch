from django.conf import settings
from django.urls import reverse
from .services.scenarios import current_scenario


def workspace(request):
    from base.access import can_access
    if not request.path.startswith('/model_purch/') or not can_access(request.user, 'purchases'):
        return {}
    route = request.resolver_match.url_name if request.resolver_match else ''
    if route in {'purch_list', 'purch_create', 'purch_edit', 'purch_delete'}:
        return {'workspace_shared_purchases': True, 'active_database': str(settings.DATABASES['default']['NAME'])}
    scenario, scenarios = current_scenario(request)
    export_state, export_label = 'stale', 'Сценарий не выбран'
    if scenario:
        export = scenario.exports.first()
        export_label = 'Не экспортирован в MS SQL'
        if export:
            from .services.preflight import snapshot, fingerprint
            try:
                current = fingerprint(snapshot(scenario)) == export.fingerprint
            except (ValueError, TypeError):
                current = False
            export_state = 'current' if current else 'stale'
            export_label = 'Экспорт актуален' if current else 'Изменён после экспорта'
    route = request.resolver_match.url_name if request.resolver_match else ''
    target = 'purch_list' if route in {'purch_create', 'purch_edit', 'purch_delete'} else 'pggoods_list' if route in {'edit_pggoods', 'delete_pggoods'} else None
    return {'workspace_export_state': export_state, 'workspace_export_label': export_label,
            'workspace_selection_url': reverse(target) if target else request.path,
            'active_database': str(settings.DATABASES['default']['NAME']),
            'workspace_scenario': scenario, 'workspace_scenarios': scenarios}
