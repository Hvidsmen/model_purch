"""Sales-planning reference directories, loaded explicitly by an administrator."""
import logging
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django.core.exceptions import ValidationError
from .models import SalesPlanningDirectoryLoad
from .sales_planning_source import load_channels, load_goods
from .sales_planning_scope import split_goods
from .access import can_access
from .sales_planning_access import require as require_planning,goods_for


@login_required
@require_GET
def index(request):
    require_planning(request.user)
    return redirect('sales_planning_split')


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def directories(request):
    require_planning(request.user)
    if request.method == 'POST':
        if not can_access(request.user,'sales_planning_admin'):raise PermissionDenied
        kind = {'load_channels': 'channels', 'load_goods': 'goods'}.get(request.POST.get('action'))
        if not kind:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Неизвестное действие.')
        return directory(request, kind)
    return render(request, 'base/sales_planning_directories.html')


@login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def directory(request, kind):
    require_planning(request.user)
    from django.http import HttpResponseBadRequest
    from django.urls import reverse
    from .sales_planning_directory_filters import filtered_directory
    if kind not in ('channels', 'goods'):
        return HttpResponseBadRequest('Неизвестный справочник.')
    error = ''
    if request.method == 'POST':
        if not can_access(request.user,'sales_planning_admin'):raise PermissionDenied
        action = 'load_' + kind
        if request.POST.get('action') != action:
            return HttpResponseBadRequest('Неизвестное действие для этого справочника.')
        try:
            count = load_channels() if kind == 'channels' else load_goods()
        except ValidationError as exc:
            error = ' '.join(exc.messages)
        except Exception:
            logging.getLogger(__name__).exception('Sales-planning directory import failed: %s', action)
            error = 'Не удалось загрузить справочник из DataWH. Проверьте подключение и журнал сервера. Сохранённые данные не изменены.'
        else:
            messages.success(request, f'Каналы продаж загружены: {count}.' if kind == 'channels' else f'Номенклатура загружена: {count}.')
            query = request.GET.copy()
            query.pop('page', None)
            url = reverse('sales_planning_' + kind)
            return redirect(url + ('?' + query.urlencode() if query else ''))
    try:
        context = filtered_directory(kind,request.GET,goods=goods_for(request.user) if kind=='goods' else None)
    except ValidationError as exc:
        return HttpResponseBadRequest(' '.join(exc.messages))
    context['advanced_filter_count']=sum(bool(item['selected']) for item in context['filters'] if item['name']!='brand')
    context.update(kind=kind, title='Каналы продаж' if kind == 'channels' else 'Номенклатура',
                   error=error, load=SalesPlanningDirectoryLoad.objects.filter(directory=kind).first())
    return render(request, 'base/sales_planning_channels.html' if kind=='channels' else 'base/sales_planning_goods.html', context)





@login_required
@never_cache
@require_http_methods(['GET','POST'])
def split(request):
    from .split_views import scenarios
    return scenarios(request)
