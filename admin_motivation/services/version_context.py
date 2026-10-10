from datetime import timedelta
from django.db.models import Count
from ..models import GlobalCoeff, SubdivisionCoeff, SubdivisionManagerCoeff
from django.http import Http404
from django.shortcuts import get_object_or_404
from ..models import GlobalCoeffVersion, SalesPlanScenario
from ..forms import GlobalCoeffVersionForm
from .versions import latest_version, effective_version, today


def version_context(request, version_form=None):
    latest = latest_version()
    version_id = request.GET.get('version')
    if version_id:
        if not version_id.isdigit():
            raise Http404('Версия не найдена.')
        selected = get_object_or_404(GlobalCoeffVersion, pk=version_id)
        request.session['motivation_version'] = selected.pk
    else:
        selected = GlobalCoeffVersion.objects.filter(pk=request.session.get('motivation_version')).first() or latest
    versions = list(GlobalCoeffVersion.objects.order_by('-effective_from', '-pk'))
    approved = sorted([v for v in versions if v.status == 'approved'], key=lambda v: v.effective_from)
    counts = {}
    for model in (GlobalCoeff, SubdivisionCoeff, SubdivisionManagerCoeff):
        for row in model.objects.order_by().values('version_id').annotate(n=Count('pk')):
            counts[row['version_id']] = counts.get(row['version_id'], 0) + row['n']
    for i, version in enumerate(versions):
        next_version = next((v for v in approved if v.effective_from > version.effective_from), None)
        version.effective_until = next_version.effective_from - timedelta(days=1) if next_version and version.status == 'approved' else None
        version.coefficient_count = counts.get(version.pk, 0)
    minimum = latest.effective_from + timedelta(days=1) if latest else None
    form = version_form or GlobalCoeffVersionForm(initial={'source_version': latest.pk if latest else None,
        'effective_from': max(today(), minimum) if minimum else '2001-01-01'})
    if minimum:
        form.fields['effective_from'].widget.attrs['min'] = '2001-01-01'
    return {'versions': versions, 'selected_version': selected, 'latest_version': latest,
            'effective_version': effective_version(), 'version_form': form,
            'version_editable': bool(selected and selected.is_editable),
            'comparison_plans': SalesPlanScenario.objects.all(),
            'comparison_plan': request.session.get('motivation_comparison_plan'),
            'comparison_baselines': [v for v in versions if not v.is_editable],
            'comparison_baseline': request.session.get('motivation_comparison_baseline')}
