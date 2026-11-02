"""Sales planning entry point. DataWH queries have not been provided yet."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from .access import can_access


@login_required
@require_GET
def index(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    return redirect('sales_planning_split')


@login_required
@never_cache
@require_GET
def split(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    # No connection or guessed SQL: an unconfigured source has no loaded data.
    return render(request, 'base/sales_planning_split.html', {
        'directories': ('Каналы продаж', 'Номенклатура'),
    })
