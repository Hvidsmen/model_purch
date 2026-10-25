from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from .access import can_access, role_for, REPORT_ROLES, BRANCH_DIRECTOR, MOTIVATION_VIEWER, motivation_viewer_read_request, FINANCIER, financier_read_request, DIRECTOR, MODELER


class PortalAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        module = view_func.__module__
        match = request.resolver_match
        project = None
        if module.startswith('model_purch.'):
            project = 'purchases'
        elif module.startswith('dashboards.'):
            project = 'dashboards'
        elif module.startswith('admin_motivation.'):
            project = 'motivation'
        elif module.startswith(('PlanningSystem.', 'RefEditor.')) or (match and match.app_name == 'admin'):
            project = 'admin'
        elif not (match and match.url_name == 'index_base'):
            return None
        if project == 'motivation' and role_for(request.user, 'motivation') in REPORT_ROLES:
            from django.conf import settings
            from .report_access import politician_scope, report_kinds
            name = match.url_name if match else None
            account_pages = {'index_base', 'portal_login', 'portal_logout', 'portal_windows_login'}
            personal = name == 'motivation_reports' or (name == 'motivation_report' and view_kwargs.get('kind') in report_kinds(request.user))
            static = module == 'django.views.static' and request.path.startswith(settings.STATIC_URL)
            if personal:
                request.personal_report_scope = politician_scope(request.user)
            if not (name in account_pages or static or (personal and request.method in ('GET', 'HEAD', 'OPTIONS') and request.personal_report_scope)):
                if '/api/' in request.path or 'application/json' in request.headers.get('Accept', ''):
                    return JsonResponse({'error': 'Доступны только отчёты в рамках назначенных подразделений и прав.'}, status=403)
                return render(request, 'base/forbidden.html', status=403)
        json_request = '/api/' in request.path or 'application/json' in request.headers.get('Accept', '') or request.content_type == 'application/json' or (match and match.url_name == 'motivation_coefficient_comparison')
        if not request.user.is_authenticated or not request.user.is_active:
            if json_request:
                return JsonResponse({'error': 'Требуется вход в портал.', 'login_url': reverse('portal_login')}, status=401)
            return redirect_to_login(request.get_full_path(), reverse('portal_login'))
        if project and not can_access(request.user, project):
            if json_request:
                return JsonResponse({'error': 'Ваша роль не разрешает доступ к этому разделу.'}, status=403)
            return render(request, 'base/forbidden.html', status=403)
        if project == 'motivation':
            role = role_for(request.user, 'motivation')
            name = match.url_name if match else None
            decisions = name in {'motivation_reviews', 'motivation_review'}
            if role == MODELER:
                allowed = {'index_admin_motivation', 'global_coeff_admin_motivation', 'gb_act', 'sub_act',
                    'coeff_subdivisions_admin_motivation', 'motivation_discount_segments', 'motivation_coefficient_balancing',
                    'motivation_bulk_formula', 'motivation_copy_group', 'motivation_coefficient_coverage', 'motivation_fallback_formula', 'motivation_coverage_repair', 'motivation_coverage_repair_all', 'motivation_director_coeff_import',
                    'motivation_global_template', 'motivation_subdivision_template', 'motivation_director_directory',
                    'motivation_directors_global', 'motivation_directors_local', 'motivation_coefficient_comparison',
                    'motivation_sales_plans', 'motivation_sales_plan', 'motivation_plan_summary', 'motivation_reports', 'motivation_report'}
                if not decisions and name not in allowed:
                    return render(request, 'base/forbidden.html', status=403)
            if role in (DIRECTOR, FINANCIER) and request.method not in ('GET', 'HEAD', 'OPTIONS') and not decisions and not motivation_viewer_read_request(request):
                return JsonResponse({'error': 'Изменение модели недоступно. Решения принимаются на странице «На проверке».'}, status=403)
        if project == 'motivation' and role_for(request.user, 'motivation') == FINANCIER and not financier_read_request(request):
            if json_request:
                return JsonResponse({'error': 'Финансист: доступны только утверждённые настройки подразделений, сегменты скидок и отчёты.'}, status=403)
            return render(request, 'base/forbidden.html', status=403)
        if project == 'motivation' and role_for(request.user, 'motivation') == MOTIVATION_VIEWER and not motivation_viewer_read_request(request):
            if json_request:
                return JsonResponse({'error':'Роль «Смотрящий»: разрешён только просмотр мотивации.'},status=403)
            return render(request,'base/forbidden.html',{'readonly_reason':'Роль «Смотрящий»: изменение данных мотивации запрещено.'},status=403)
        return None
