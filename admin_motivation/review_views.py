from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, connection
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from base.access import role_for, ADMIN, MODELER, FINANCIER, DIRECTOR
from .models import GlobalCoeffVersion, SalesPlanScenario, MotivationReview
from .services import review_workflow
from .services.approval import affected_versions


def require_reviewer(request):
    role = role_for(request.user, 'motivation')
    if role not in (ADMIN, MODELER, FINANCIER, DIRECTOR):
        raise PermissionDenied
    return role


def failure(request, error):
    if isinstance(error, OperationalError):
        if connection.vendor != 'sqlite' or not any(word in str(error).lower() for word in ('locked', 'busy')):
            raise error
        messages.error(request, 'База занята другой операцией. Решение не сохранено. Повторите действие.')
    else:
        messages.error(request, ' '.join(error.messages) if isinstance(error, ValidationError) else 'Проверьте выбранную версию и план.')


@require_http_methods(['GET', 'POST'])
def reviews(request):
    role = require_reviewer(request)
    editable = GlobalCoeffVersion.objects.exclude(status='superseded').order_by('-pk').first()
    editable = editable if editable and editable.is_editable else None
    plans = SalesPlanScenario.objects.filter(is_deleted=False, loaded_at__isnull=False).order_by(F('loaded_at').desc(), '-pk')
    values = request.POST if request.method == 'POST' else request.GET
    if request.method == 'POST':
        try:
            if role not in (ADMIN, MODELER):
                raise PermissionDenied
            review = review_workflow.submit(request.user, int(values.get('version', '')), int(values.get('plan', '')), values.get('revision'), values.get('comment', ''))
            messages.success(request, 'Набор отправлен финансисту. Коэффициенты закрыты для редактирования.')
            return redirect('motivation_review', review_id=review.pk)
        except (ValidationError, ValueError, TypeError, OperationalError, GlobalCoeffVersion.DoesNotExist, SalesPlanScenario.DoesNotExist) as error:
            failure(request, error)
    if request.method == 'GET' and request.GET.get('version', '').isdigit():
        latest = MotivationReview.objects.filter(version_id=request.GET['version']).order_by('-pk').first()
        if latest and latest.version.status in review_workflow.PENDING:
            return redirect('motivation_review', review_id=latest.pk)
    history = MotivationReview.objects.select_related('version', 'plan').order_by('-pk')
    page = Paginator(history, 25).get_page(request.GET.get('page'))
    for item in page:
        item.last_event = item.events.order_by('-pk').first()
    return render(request, 'admin_motivation/reviews.html', {'review_page': page,
        'editable': editable, 'plans': plans, 'can_submit': role in (ADMIN, MODELER),
        'selected_plan': str(values.get('plan') or (plans.first().pk if plans.first() else '')),
        'submission_comment': values.get('comment', '')})


@require_http_methods(['GET', 'POST'])
def review_detail(request, review_id):
    role = require_reviewer(request)
    review = get_object_or_404(MotivationReview.objects.select_related('version', 'plan', 'baseline'), pk=review_id)
    if request.method == 'POST':
        try:
            review_workflow.decide(request.user, review.pk, request.POST.get('action'), request.POST.get('comment', ''), request.POST.get('confirm_overwrite') == 'yes')
            messages.success(request, 'Решение сохранено в истории проверки.')
            return redirect('motivation_review', review_id=review.pk)
        except (ValidationError, OperationalError) as error:
            failure(request, error)
        review.refresh_from_db()
    active = review_workflow.active(review)
    stage = review.version.status
    allowed = active and (role == ADMIN or role == FINANCIER and stage == 'finance_review' or role == DIRECTOR and stage == 'director_review')
    impacts = []
    for key, title in [('global', 'Глобальная мотивация'), ('subdivisions', 'Мотивация подразделений')]:
        result = review.summary.get(key)
        if result:
            impacts.append({'title': title, 'before': result['variants']['approved'], 'after': result['variants']['current'], 'delta': result['deltas']['approved']['total']['amount']})
    filters = {key: request.GET.get(key, '').strip() for key in ('scope', 'kind', 'search')}
    show_all = request.GET.get('show') == 'all'
    rows = ([{**row, 'before': '—', 'after': row['value']} for row in review_workflow.cells(review.snapshot).values()] if show_all else review.changes)
    scopes, kinds = sorted({row['scope'] for row in rows}), sorted({row['kind'] for row in rows})
    for key in ('scope', 'kind'):
        if filters[key]:
            rows = [row for row in rows if row[key] == filters[key]]
    if filters['search']:
        rows = [row for row in rows if filters['search'].casefold() in f"{row['product']} {row['employee']} {row['field']}".casefold()]
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'admin_motivation/review_detail.html', {
        'review': review, 'review_active': active, 'selected_version': review.version,
        'decision_allowed': allowed, 'finance_decision': allowed and role == FINANCIER,
        'director_decision': allowed and role == DIRECTOR, 'admin_decision': allowed and role == ADMIN,
        'impacts': impacts, 'replaced': list(affected_versions(review.version)),
        'change_page': Paginator(rows, 100).get_page(request.GET.get('page')), 'change_count': len(review.changes),
        'show_all': show_all, 'scopes': scopes, 'kinds': kinds, 'filters': filters, 'filter_query': query.urlencode(),
        'events': review.events.order_by('pk'), 'decision_comment': request.POST.get('comment', '')})
