from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from .access import can_access


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
        elif module.startswith('admin_motivation.'):
            project = 'motivation'
        elif module.startswith(('PlanningSystem.', 'RefEditor.')) or (match and match.app_name == 'admin'):
            project = 'admin'
        elif not (match and match.url_name == 'index_base'):
            return None
        json_request = '/api/' in request.path or 'application/json' in request.headers.get('Accept', '') or request.content_type == 'application/json' or (match and match.url_name == 'motivation_coefficient_comparison')
        if not request.user.is_authenticated or not request.user.is_active:
            if json_request:
                return JsonResponse({'error': 'Требуется вход в портал.', 'login_url': reverse('portal_login')}, status=401)
            return redirect_to_login(request.get_full_path(), reverse('portal_login'))
        if project and not can_access(request.user, project):
            if json_request:
                return JsonResponse({'error': 'Ваша роль не разрешает доступ к этому разделу.'}, status=403)
            return render(request, 'base/forbidden.html', status=403)
        return None
