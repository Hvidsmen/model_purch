import logging
from django import forms
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from .access import can_access
from .preview import SESSION_KEY

logger = logging.getLogger(__name__)


@require_GET
def windows_login(request):
    if not settings.WINDOWS_AUTH_ENABLED:
        raise Http404
    user = authenticate(request, remote_user=request.META.get('REMOTE_USER'))
    if user is None:
        return render(request, 'base/windows_unavailable.html', status=403)
    request.session.pop(SESSION_KEY, None)
    login(request, user)
    target = request.GET.get('next', '')
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        target = reverse('index_base')
    return redirect(target or reverse('index_base'))


class PreviewForm(forms.Form):
    user = forms.ModelChoiceField(label='Пользователь', queryset=get_user_model().objects.none(), empty_label='Выберите пользователя')

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = get_user_model().objects.filter(is_active=True).exclude(pk=actor.pk).order_by('username')


@require_http_methods(['GET', 'POST'])
def user_preview(request):
    actor = request.portal_actor
    if not can_access(actor, 'admin'):
        raise PermissionDenied
    form = PreviewForm(request.POST or None, actor=actor)
    if request.method == 'POST' and form.is_valid():
        target = form.cleaned_data['user']
        request.session[SESSION_KEY] = {'actor': str(actor.pk), 'target': str(target.pk)}
        logger.info('Portal user preview started: actor=%s target=%s', actor.pk, target.pk)
        return redirect('index_base')
    return render(request, 'base/user_preview.html', {'form': form})


@require_POST
def stop_preview(request):
    actor = request.portal_actor
    if not can_access(actor, 'admin'):
        raise PermissionDenied
    request.session.pop(SESSION_KEY, None)
    logger.info('Portal user preview stopped: actor=%s', actor.pk)
    return redirect('index_base')
