from datetime import timedelta
from base.access import role_for, MOTIVATION_VIEWER, FINANCIER
from django.db.models import Count
from ..models import GlobalCoeff, SubdivisionCoeff, SubdivisionManagerCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff
from django.http import Http404
from django.shortcuts import get_object_or_404
from ..models import GlobalCoeffVersion, SalesPlanScenario
from ..forms import GlobalCoeffVersionForm
from .versions import latest_version, effective_version, today


def version_context(request, version_form=None):
    if role_for(request.user) == FINANCIER:
        return financier_version_context(request)
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
    for model in (GlobalCoeff, SubdivisionCoeff, SubdivisionManagerCoeff, GlobalDirectorCoeff, SubdivisionDirectorCoeff):
        for row in model.objects.order_by().values('version_id').annotate(n=Count('pk')):
            counts[row['version_id']] = counts.get(row['version_id'], 0) + row['n'] * (5 if model in (GlobalDirectorCoeff, SubdivisionDirectorCoeff) else 1)
    for i, version in enumerate(versions):
        next_version = next((v for v in approved if v.effective_from > version.effective_from), None)
        version.effective_until = next_version.effective_from - timedelta(days=1) if next_version and version.status == 'approved' else None
        version.coefficient_count = counts.get(version.pk, 0)
    last_approved = approved[-1] if approved else None
    minimum = latest.effective_from + timedelta(days=1) if latest else None
    form = version_form or GlobalCoeffVersionForm(initial={'source_version': latest.pk if latest else None,
        'effective_from': max(today(), minimum) if minimum else '2001-01-01'})
    if minimum:
        form.fields['effective_from'].widget.attrs['min'] = '2001-01-01'
    return {'versions': versions, 'selected_version': selected, 'latest_version': latest, 'reopen_logs': selected.reopen_history.select_related('actor').order_by('-pk')[:20] if selected else [],
            'effective_version': effective_version(), 'last_approved_version': last_approved, 'version_form': form,
            'version_editable': bool(selected and selected.is_editable and role_for(request.user) != MOTIVATION_VIEWER),
            'comparison_plans': SalesPlanScenario.objects.filter(is_deleted=False),
            'comparison_plan': request.session.get('motivation_comparison_plan'),
            'comparison_baselines': [v for v in versions if not v.is_editable],
            'comparison_baseline': request.session.get('motivation_comparison_baseline')}


def financier_version_context(request):
    versions = list(GlobalCoeffVersion.objects.filter(status='approved').order_by('-effective_from', '-pk'))
    version_id = request.GET.get('version')
    if version_id:
        if not version_id.isdigit():
            raise Http404('Утверждённая версия не найдена.')
        selected = next((version for version in versions if version.pk == int(version_id)), None)
        if selected is None:
            raise Http404('Утверждённая версия не найдена.')
        request.session['motivation_version'] = selected.pk
    else:
        selected = next((version for version in versions if version.pk == request.session.get('motivation_version')), None)
        selected = selected or effective_version() or (versions[0] if versions else None)
    for index, version in enumerate(versions):
        version.effective_until = versions[index - 1].effective_from - timedelta(days=1) if index else None
    return {'versions': versions, 'selected_version': selected, 'version_editable': False,
            'latest_version': versions[0] if versions else None, 'effective_version': effective_version()}
