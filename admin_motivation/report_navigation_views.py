"""Compatibility links for the single motivation report catalog."""
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.views.decorators.http import require_GET
from .services.reports_next import available, period_code

LEGACY = {'manager': 'mine', 'manager-detail': 'detail',
          'subdivisions': 'team', 'deals': 'deals', 'plan-summary': 'executive'}


@require_GET
def catalog(request):
    return HttpResponseRedirect(reverse('motivation_reports'))


@require_GET
def legacy_report(request, kind):
    if kind not in LEGACY:
        raise Http404('Отчёт не найден.')
    target = LEGACY[kind]
    if target not in available(request.user):
        return catalog(request)
    query = request.GET.copy()
    if 'period' not in query and query.get('quarter'):
        period = period_code(query['quarter'])
        if period:
            query['period'] = period
    query.pop('quarter', None)
    if kind == 'plan-summary' and query.get('scenario') and not query.get('plan'):
        query['plan'] = query['scenario']
    url = reverse('motivation_report_next', args=[target])
    return HttpResponseRedirect(url + ('?' + query.urlencode() if query else ''))
