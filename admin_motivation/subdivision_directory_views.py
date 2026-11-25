"""Portal subdivision reference and source identities."""
from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.http import require_GET
from base.access import motivation_editor_required
from .models import Subdivision
from .services.subdivision_scope import included


@require_GET
def directory(request):
    motivation_editor_required(request)
    query=request.GET.get('q','').strip()
    rows=Subdivision.all_objects.select_related('chanel','merged_into').order_by('subdivision_name','subdivision_key')
    if query:
        rows=rows.filter(Q(subdivision_key__icontains=query)|Q(subdivision_name__icontains=query)|Q(chanel__chanel_name__icontains=query))
    rows=list(rows)
    for row in rows:
        row.excluded_from_motivation=not included(row.subdivision_name)
    return render(request,'admin_motivation/subdivision_directory.html',{
        'subdivisions':rows,'search':query,'skip_motivation_workflow':True,
    })
