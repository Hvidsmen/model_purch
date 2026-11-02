"""Closed-deal facts in the new UI, using the legacy datasets and access scopes."""
import logging
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from base.access import role_for, REPORT_ROLES
from base.report_access import politician_scope, scoped_query, scoped_values, identity_choices
from .models import MotivationExecution
from .services.reports import FILTERS, parameters, load_report
from .services.deal_filter_choices import choices
from .services.deal_presentation import prepare, page_csv
from .services.versions import today


def report(request):
    from .reports_next_views import report_navigation
    from .services.reports_next import available, current_run
    scope = politician_scope(request.user)
    if role_for(request.user, 'motivation') in REPORT_ROLES and not scope:
        raise PermissionDenied
    query = scoped_query(request.GET, scope, 'deals') if scope else request.GET.copy()
    choices_only = query.pop('_choices', None) == ['1']
    query.pop('execution', None)
    for field in ('type', 'subdivision'):
        if query.getlist(field):
            query.setlist(field, [query.getlist(field)[0]])
    # Facts use exact close dates, not the Q4/annual premium-period restrictions.
    query.pop('period', None)
    query.setdefault('start', today().replace(month=1, day=1).isoformat())
    query.setdefault('end', today().isoformat())
    selected = MotivationExecution.objects.filter(state='completed').order_by('-completed_at', '-started_at', '-pk').first()
    data, errors, filters, values = None, [], [], {}
    try:
        values = parameters('deals', query, trusted_identity=bool(scope))
        if scope:
            values = scoped_values('deals', values, scope)
        filters = choices('deals', values, refresh=query.get('refresh_choices') == '1', **({'access_scope': scope} if scope else {}))
        if not choices_only and (query.get('run') == '1' or query.get('export') == 'csv'):
            try:
                page = max(1, int(query.get('page', '1')))
            except (ValueError, TypeError):
                page = 1
            if not selected:
                raise ValidationError('Нет подтверждённых завершённых расчётов.')
            with current_run(selected):
                data = load_report('deals', values, page=page, **({'access_scope': scope} if scope else {}))
            prepare(data, query)
            if query.get('export') == 'csv':
                return page_csv(data)
    except PermissionDenied:
        raise
    except ValidationError as exc:
        errors = exc.messages
    except RuntimeError:
        errors = ['Не настроено подключение DWH_SQL_CONN_STR к данным отчётов.']
    except Exception:
        logging.getLogger(__name__).exception('New closed-deals report failed')
        errors = ['Не удалось получить данные отчёта. Проверьте доступ к DataWH и журнал сервера.']
    if not filters:
        filters = [{'name': name, 'label': caption, 'multiple': multiple, 'options': []} for name, _, caption, _, multiple in FILTERS['deals']]
    if scope:
        fixed = identity_choices('deals', values, scope)
        for item in filters:
            if item['name'] in fixed:
                item.update(fixed[item['name']])
    if choices_only:
        if errors:
            return JsonResponse({'errors': errors}, status=400)
        return JsonResponse({'html': render_to_string('admin_motivation/reports_next/_deal_filter_fields.html', {'filters': filters}, request=request)})
    csv_query = query.copy()
    csv_query['export'], csv_query['run'] = 'csv', '1'
    return render(request, 'admin_motivation/reports_next/deals.html', {
        'kind': 'deals', 'title': 'Факт закрытых сделок', 'catalog': available(request.user),
        'report_navigation': report_navigation(request.user, 'deals', query),
        'query': query, 'filters': filters, 'data': data, 'errors': errors, 'execution': selected,
        'csv_url': '?' + csv_query.urlencode(), 'reset_url': reverse('motivation_report_next', args=['deals']),
    })
