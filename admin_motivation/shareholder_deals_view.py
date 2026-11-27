"""Use the same page and columns as ordinary closed deals, with frozen facts."""
from django.core.exceptions import ValidationError
from django.http import FileResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from .services import shareholder_deals, shareholder_reports
from .services.deal_presentation import COLUMNS, page_csv


def report(request, run):
    # Caller has checked shareholder access and the completed snapshot version.
    from .services.shareholder_scope import query as scoped_query,report_kinds
    query = scoped_query(request.GET)
    choices_only = query.pop('_choices', None) == ['1']
    for field in ('period', 'currency', 'year', 'execution'):
        query.pop(field, None)
    for field in ('type', 'subdivision'):
        query.setlist(field, [v for v in query.getlist(field) if v][:1])
    query.setdefault('start', run.as_of.replace(month=1, day=1).isoformat())
    query.setdefault('end', run.as_of.isoformat())
    data, filters, errors = None, [], []
    load = not choices_only and (query.get('run') == '1' or bool(query.get('export')))
    try:
        data, filters = shareholder_deals.build(run, query, load=load)
        if load and (not query.get('type') or not query.get('subdivision')):
            raise ValidationError('Выберите тип коэффициента и подразделение.')
    except ValidationError as exc:
        errors = exc.messages
    if choices_only:
        return JsonResponse({'errors': errors}, status=400) if errors else JsonResponse({
            'html': render_to_string('admin_motivation/reports_next/_deal_filter_fields.html', {'filters': filters}, request=request)})
    if data is not None and query.get('export') == 'csv':
        return page_csv(data)
    if query.get('_partner_rows')=='1':
        from .services.deal_partners import response
        return response(request,data,errors)
    if data is not None and query.get('export') == 'xlsx':
        from .shareholder_views import plain_excel, metadata
        rows = []
        for row in shareholder_deals.records(run, query['type']):
            if not query['start'] <= row['ДатаЗакрытияСделки'].isoformat() <= query['end']:
                continue
            if any(query.getlist(key) and row.get(column) not in query.getlist(key)
                   for key, column in shareholder_deals.FILTER_COLUMNS.items() if key != 'type'):
                continue
            rows.append([row.get(key) for key, _, _ in COLUMNS])
        output = plain_excel('Факт закрытых сделок', [title for _, title, _ in COLUMNS], rows, metadata(run))
        return FileResponse(output, as_attachment=True, filename=f'shareholder-deals-{run.pk}.xlsx')
    navigation = []
    for key, title in shareholder_reports.TITLES.items():
        if key not in report_kinds(request.user):continue
        target = query.copy()
        for field in ('run', 'export', 'page', 'start', 'end', 'deal_view', '_partner', '_partner_rows'):
            target.pop(field, None)
        navigation.append(dict(title=title, active=key == 'deals', url=reverse(
            'motivation_shareholder_report', args=[run.pk, key]) + '?' + target.urlencode()))
    csv_query = query.copy(); csv_query['export'] = 'csv'; csv_query['run'] = '1'
    excel_query = query.copy(); excel_query['export'] = 'xlsx'; excel_query.pop('page', None)
    audit_query = query.copy(); audit_query['audit'] = '1'
    return render(request, 'admin_motivation/reports_next/deals.html', {
        'kind': 'deals', 'title': 'Факт закрытых сделок', 'shareholder_report': True,
        'skip_motivation_workflow': True, 'run': run, 'navigation': navigation,
        'query': query, 'filters': filters, 'data': data, 'errors': errors,
        'csv_url': '?' + csv_query.urlencode(), 'export_url': '?' + excel_query.urlencode(),
        'audit_url': reverse('motivation_shareholder_report', args=[run.pk, 'detail']) + '?' + audit_query.urlencode(),
        'reset_url': reverse('motivation_shareholder_report', args=[run.pk, 'deals']),
    })
