from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from base.access import role_for, ADMIN, MODELER, DIRECTOR, FINANCIER, MOTIVATION_VIEWER
from .models import GlobalCoeffVersion, SalesPlanScenario, Subdivision
from .services import coefficient_coverage
from .services.versions import today


def versions_for(user):
    versions = GlobalCoeffVersion.objects.order_by('-pk')
    return versions.filter(status__in=['approved', 'finance_review', 'director_review']) if role_for(user, 'motivation') == FINANCIER else versions


@require_GET
def coverage(request):
    if role_for(request.user, 'motivation') not in (ADMIN, MODELER, DIRECTOR, FINANCIER, MOTIVATION_VIEWER):
        raise PermissionDenied
    versions = versions_for(request.user)
    selected = get_object_or_404(versions, pk=request.GET['version']) if request.GET.get('version', '').isdigit() else versions.first()
    plans = SalesPlanScenario.objects.filter(is_deleted=False, loaded_at__isnull=False).order_by('-loaded_at', '-pk')
    plan = get_object_or_404(plans, pk=request.GET['plan']) if request.GET.get('plan', '').isdigit() else plans.first()
    source = 'fact' if request.GET.get('source') == 'fact' else 'plan'
    kind = request.GET.get('kind', '')
    if kind not in ('', 'Политики', 'Продажи', 'Директора'):
        raise PermissionDenied
    subdivision = request.GET.get('subdivision', '')
    if subdivision:
        get_object_or_404(Subdivision, subdivision_key=subdivision)
    error, result, page = '', None, None
    try:
        year = int(request.GET.get('year', today().year))
        if not 1900 <= year <= 9998:
            raise ValueError
        if selected and request.GET.get('run') == '1':
            if source == 'plan' and not plan:
                raise ValidationError('Сначала загрузите план продаж.')
            rows = coefficient_coverage.fact_rows(year, subdivision) if source == 'fact' else coefficient_coverage.plan_rows(plan, subdivision)
            result = coefficient_coverage.check(selected, rows, kind)
            page = Paginator(result['issues'], 100).get_page(request.GET.get('page'))
    except (ValueError, ValidationError, RuntimeError) as exception:
        error = ' '.join(exception.messages) if isinstance(exception, ValidationError) else 'Проверьте год и подключение к DWH. Требуется настроенный DWH_SQL_CONN_STR.'
        year = request.GET.get('year', '')
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Coefficient coverage query failed')
        error = 'Не удалось проверить факт. Проверьте подключение к DWH и журнал сервера.'
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'admin_motivation/coefficient_coverage.html', {'versions': versions, 'selected_version': selected,
        'plans': plans, 'plan': plan, 'source': source, 'kind': kind, 'year': year,
        'subdivision': subdivision, 'subdivisions': Subdivision.objects.all(), 'result': result,
        'page': page, 'coverage_error': error, 'coverage_query': query.urlencode()})


@require_POST
def save_formula(request):
    from .services.formula_defaults import save_default
    try:
        version = save_default(request.user, int(request.POST.get('version', '')), request.POST.get('revision'), request.POST.get('fallback_variation', ''))
        messages.success(request, 'Формула по умолчанию сохранена в черновике. Точные настройки и ставки не изменены. Для применения утвердите версию, выполните выгрузку и расчёт.')
        return redirect(reverse('global_coeff_admin_motivation') + f'?version={version.pk}')
    except (ValidationError, ValueError, TypeError) as error:
        messages.error(request, ' '.join(error.messages) if isinstance(error, ValidationError) else 'Проверьте выбранную версию.')
        return redirect('global_coeff_admin_motivation')
