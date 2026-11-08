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
from .models import SalesPlanningChannel, SalesPlanningDirectoryLoad, SalesPlanningGoods
from .sales_planning_source import load_channels, load_goods
from .sales_planning_scope import split_goods
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
def directories(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    error = ''
    if request.method == 'POST':
        action = request.POST.get('action')
        if action not in ('load_channels', 'load_goods'):
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Неизвестное действие.')
        try:
            count = load_channels() if action == 'load_channels' else load_goods()
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        except Exception:
            logging.getLogger(__name__).exception('Sales-planning directory import failed: %s', action)
            error = 'Не удалось загрузить справочник из DataWH. Проверьте подключение и журнал сервера. Сохранённые данные не изменены.'
        else:
            messages.success(request, f'Каналы продаж загружены: {count}.' if action == 'load_channels' else f'Номенклатура загружена: {count}.')
            return redirect('sales_planning_directories')
    return render(request, 'base/sales_planning_directories.html', {
        'error': error,
        'channel_load': SalesPlanningDirectoryLoad.objects.filter(directory='channels').first(),
        'channel_page': Paginator(SalesPlanningChannel.objects.filter(is_active=True), 50).get_page(request.GET.get('page')),
        'goods_load': SalesPlanningDirectoryLoad.objects.filter(directory='goods').first(),
        'goods_count': split_goods().count(),
        'goods_page': Paginator(split_goods(), 50).get_page(request.GET.get('goods_page')),
    })




@login_required
@never_cache
@require_GET
def split(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    loads = {entry.directory: entry for entry in SalesPlanningDirectoryLoad.objects.all()}
    return render(request, 'base/sales_planning_split.html', {
        'directory_statuses': [{'label': label, 'load': loads.get(key), 'count': split_goods().count() if key == 'goods' else loads[key].row_count if key in loads else 0}
                               for key, label in [('channels', 'Каналы продаж'), ('goods', 'Номенклатура')]],
    })

