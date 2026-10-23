"""Admin-only, read-only rendering under another user's existing permissions."""
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from .access import can_access, role_for, MOTIVATION_VIEWER, motivation_viewer_read_request

SESSION_KEY = 'portal_user_preview'


class UserPreviewMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.portal_actor = request.user
        request.portal_preview_user = None
        config = request.session.get(SESSION_KEY)
        controls = {reverse('portal_user_preview'), reverse('portal_stop_preview'), reverse('portal_logout')}
        if config:
            if not isinstance(config, dict) or str(request.user.pk) != str(config.get('actor')) or not can_access(request.user, 'admin'):
                request.session.pop(SESSION_KEY, None)
            elif request.path not in controls:
                target = get_user_model().objects.filter(pk=config.get('target'), is_active=True).first()
                if target is None:
                    request.session.pop(SESSION_KEY, None)
                else:
                    request.portal_preview_user = target
                    request.user = target
                    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not (role_for(target)==MOTIVATION_VIEWER and motivation_viewer_read_request(request)):
                        if '/api/' in request.path or 'application/json' in request.headers.get('Accept', ''):
                            return JsonResponse({'error': 'В режиме просмотра нельзя изменять данные.'}, status=403)
                        return render(request, 'base/preview_readonly.html', status=403)
        return self.get_response(request)
