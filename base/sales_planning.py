"""Sales-planning reference directories, loaded explicitly by an administrator."""
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from .models import SalesPlanningChannel, SalesPlanningDirectoryLoad
from .sales_planning_source import load_channels
from .access import can_access


@login_required
@require_GET
def index(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    return redirect('sales_planning_split')


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def split(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    error = ''
    if request.method == 'POST':
        if request.POST.get('action') != 'load_channels':
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Неизвестное действие.')
        try:
            count = load_channels()
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        except Exception:
            logging.getLogger(__name__).exception('Sales-planning channel import failed')
            error = 'Не удалось загрузить каналы продаж из DataWH. Проверьте подключение и журнал сервера. Сохранённые данные не изменены.'
        else:
            messages.success(request, f'Каналы продаж загружены: {count}.')
            return redirect('sales_planning_split')
    return render(request, 'base/sales_planning_split.html', {
        'error': error,
        'channel_load': SalesPlanningDirectoryLoad.objects.filter(directory='channels').first(),
        'channel_page': Paginator(SalesPlanningChannel.objects.filter(is_active=True), 50).get_page(request.GET.get('page')),
    })

