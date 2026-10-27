import json
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse, Http404
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from .access import can_access
from . import portal_updates
from scripts import portal_update_state as protocol


def administrator(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied


@never_cache
@require_GET
def updates(request):
    administrator(request)
    available, reason = portal_updates.availability()
    current = protocol.observe(portal_updates.root(), protocol.active(portal_updates.root()))
    from .release_notes import RELEASE
    return render(request, 'base/updates.html', {'installed_release':RELEASE, 'update_available': available, 'update_disabled_reason': reason,
        'update_current': current, 'update_history': [protocol.observe(portal_updates.root(), row) for row in protocol.history(portal_updates.root())],
        'update_revision': portal_updates.revision()[:12]})


@never_cache
@require_POST
def start(request):
    administrator(request)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or data.get('confirm') is not True:
            raise ValueError('Подтвердите временную остановку портала для обновления.')
        current, token, control = portal_updates.create(request.user)
        # Browser stores these before asking Windows to launch the task.
        return JsonResponse({'run': current, 'token': token, 'control': control}, status=201)
    except (ValueError, OSError) as error:
        return JsonResponse({'error': str(error) if isinstance(error, ValueError) else 'Не удалось подготовить запуск обновления.'}, status=409)


@never_cache
@require_POST
def launch(request, run_id):
    administrator(request)
    try:
        if not protocol.RUN_PATTERN.fullmatch(run_id):
            raise ValueError('Некорректный запуск обновления.')
        return JsonResponse(portal_updates.launch(run_id))
    except (ValueError, OSError) as error:
        return JsonResponse({'error': str(error) if isinstance(error, ValueError) else 'Задание уже запускается. Дождитесь обновления статуса.'}, status=409)


@never_cache
@require_GET
def status(request, run_id):
    try:
        authorized = protocol.authorized(portal_updates.root(), run_id, request.headers.get('X-Portal-Update-Status', ''))
        if not authorized:
            administrator(request)
        data = protocol.state(portal_updates.root(), run_id)
    except ValueError:
        raise Http404
    if data is None:
        raise Http404
    return JsonResponse(protocol.observe(portal_updates.root(), data))


@never_cache
@require_GET
def health(request):
    # This contains no user/database information. Maintenance returns 503 here.
    return JsonResponse({'ready': True, 'revision': portal_updates.revision()})


@never_cache
@require_POST
def credentials(request, run_id):
    """Recover an allocated but unstarted run after closing the original tab."""
    administrator(request)
    import secrets
    try:
        with protocol.lock(protocol.directory(portal_updates.root()) / 'dispatch.lock'):
            current = protocol.active(portal_updates.root())
            if not current or current['id'] != run_id or current['status'] != 'queued':
                raise ValueError('Доступ можно восстановить только до запуска обновления.')
            path = protocol.run_directory(portal_updates.root(), run_id) / 'request.json'
            token, control = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            protocol.write(path, {**protocol.read(path, {}), 'id': run_id, 'status_hash': protocol.digest(token), 'control_hash': protocol.digest(control)})
        return JsonResponse({'token': token, 'control': control})
    except (ValueError, OSError) as error:
        return JsonResponse({'error': str(error) if isinstance(error, ValueError) else 'Задание уже запускается.'}, status=409)
