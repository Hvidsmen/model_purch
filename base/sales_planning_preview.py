"""Isolated, static UX prototype. No ERP/DWH calls or production writes."""
from pathlib import Path
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from .access import can_access


@login_required
@never_cache
@require_GET
def preview(request):
    if not can_access(request.user, 'admin'):
        raise PermissionDenied
    path = Path(settings.BASE_DIR) / 'docs' / 'sales-planning-split' / 'prototype.html'
    response = FileResponse(path.open('rb'), content_type='text/html; charset=utf-8')
    response['X-Content-Type-Options'] = 'nosniff'
    return response
