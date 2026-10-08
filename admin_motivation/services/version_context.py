from datetime import timedelta
from django.http import Http404
from django.shortcuts import get_object_or_404
from ..models import GlobalCoeffVersion
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
    versions = list(GlobalCoeffVersion.objects.order_by('-effective_from'))
    for i, version in enumerate(versions):
        version.effective_until = versions[i - 1].effective_from - timedelta(days=1) if i else None
        version.coefficient_count = version.coefficients.count() + version.subdivision_coefficients.count() + version.manager_coefficients.count()
    minimum = latest.effective_from + timedelta(days=1) if latest else None
    form = version_form or GlobalCoeffVersionForm(initial={'source_version': latest.pk if latest else None,
        'effective_from': max(today(), minimum) if minimum else '2001-01-01'})
    if minimum:
        form.fields['effective_from'].widget.attrs['min'] = minimum.isoformat()
    return {'versions': versions, 'selected_version': selected, 'latest_version': latest,
            'effective_version': effective_version(), 'version_form': form,
            'version_editable': bool(selected and latest and selected.pk == latest.pk)}
