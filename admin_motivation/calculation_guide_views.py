"""A short calculation explanation, available to every motivation role."""
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.views.decorators.http import require_safe
from base.access import can_access
from .services.main_quarter_mode import enabled


@require_safe
def page(request):
    if not can_access(request.user,'motivation'):raise PermissionDenied
    return render(request,'admin_motivation/calculation_guide.html',{'skip_motivation_workflow':True,'main_all_quarters':enabled()})
