"""Trial-only database guards and a visible indication of the isolated environment."""
from django.conf import settings
from django.http import HttpResponseForbidden


class SourceReadOnlyRouter:
    def allow_migrate(self,db,app_label,model_name=None,**hints):
        return False if db=='trial_source' else None


class TrialIsolationMiddleware:
    def __init__(self,get_response):self.get_response=get_response
    def __call__(self,request):
        response=self.get_response(request)
        response['X-Portal-Environment']='SQLSERVER-TRIAL'
        response['X-Robots-Tag']='noindex, nofollow'
        return response
    def process_view(self,request,view_func,view_args,view_kwargs):
        match=request.resolver_match
        name=match.url_name or '' if match else ''
        if name.startswith(('portal_update','portal_task','update_portal')) or name in (
            'bulk_export_scenarios','export_scenario_to_sql','portal_database_diagnostics',
            'split_monitoring_index','split_monitoring_asset','split_monitoring_api','split_monitoring_updates'):
            return HttpResponseForbidden('В тестовом контуре обновление рабочего портала и внешний SQL-экспорт отключены.')


def context(request):
    return {'portal_sqlserver_trial':getattr(settings,'PORTAL_SQLSERVER_TRIAL',False),
            'portal_trial_database':settings.DATABASES['default']['NAME'] if getattr(settings,'PORTAL_SQLSERVER_TRIAL',False) else ''}
