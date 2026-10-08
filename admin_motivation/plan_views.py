import csv
import logging
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from .forms import SalesPlanScenarioForm
from .models import SalesPlanScenario, Subdivision
from .services.sales_plans import load_plan, calculate_plan, source_versions

logger = logging.getLogger(__name__)


def sales_plans(request, scenario_id=None):
    scenario = get_object_or_404(SalesPlanScenario, pk=scenario_id) if scenario_id else None
    form = SalesPlanScenarioForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'create' else None)
    versions = request.session.get('sales_plan_source_versions', [])
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if action == 'create' and form.is_valid():
                created = SalesPlanScenario.objects.create(**form.cleaned_data)
                return redirect('motivation_sales_plan', scenario_id=created.pk)
            elif action == 'refresh_sources':
                versions = source_versions()
                request.session['sales_plan_source_versions'] = versions
                messages.success(request, f'Найдено версий плана: {len(versions)}.')
            elif scenario and action == 'load':
                count = load_plan(scenario.pk)
                messages.success(request, f'План загружен: {count} строк подразделений. Глобальный план сформирован суммированием.')
            elif scenario and action == 'calculate':
                count = calculate_plan(scenario.pk)
                messages.success(request, f'Расчёт выполнен: {count} строк. Результаты сохранены вместе с использованными коэффициентами.')
            elif action != 'create':
                messages.error(request, 'Неизвестное действие.')
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        except Exception:
            logger.exception('Sales plan operation failed')
            messages.error(request, 'Операция не выполнена. Проверьте подключение DWH_SQL_CONN_STR и журнал сервера. Сохранённые данные не изменены.')
        if not (action == 'create' and form.errors):
            return redirect(reverse('motivation_sales_plan', args=[scenario.pk]) if scenario else reverse('motivation_sales_plans'))
    scope = 'subdivisions' if request.GET.get('scope') == 'subdivisions' else 'global'
    lines = scenario.lines.all() if scenario else None
    if lines is not None:
        lines = lines.exclude(subdivision='') if scope == 'subdivisions' else lines.filter(subdivision='')
        lines = lines.select_related('coefficient_version')
    if scenario and request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="motivation-plan-{scenario.pk}-{scope}.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow(['Дата', 'Подразделение', 'Группа планов', 'Группа', 'Марка', 'План USD', *[f'USD_O{i}' for i in range(5)], 'Версия коэффициентов', 'Политики USD', 'Продажи USD', 'Итого USD', *[f'{kind} {field} O{i}' for kind in ['Политики', 'Продажи'] for i in range(5) for field in ['k', 'Мотивация']]])
        for line in lines.iterator():
            details = [line.calculation.get(kind, {}).get(f'O{i}', {}).get(field, '') for kind in ['Политики', 'Продажи'] for i in range(5) for field in ['coefficient', 'motivation']]
            values = [line.plan_date, line.subdivision or 'Глобальный план', line.planning_group_sales, line.group, line.brand, line.amount_usd, *[line.segment_amounts.get(f'O{i}', '') for i in range(5)], str(line.coefficient_version or ''), line.policies_usd, line.sales_usd, line.total_usd, *details]
            # Escape external labels only; negative numeric results remain numeric.
            for index in [1, 2, 3, 4, 11]:
                value = str(values[index])
                if value.lstrip().startswith(('=', '+', '-', '@')):
                    values[index] = "'" + value
            writer.writerow(values)
        return response
    return render(request, 'admin_motivation/sales_plans.html', {
        'scenario': scenario, 'scenarios': SalesPlanScenario.objects.all(), 'form': form,
        'source_versions': versions, 'scope': scope, 'subdivisions': Subdivision.objects.all(),
        'page': Paginator(lines, 50).get_page(request.GET.get('page')) if lines is not None else None,
        'totals': lines.aggregate(plan=Sum('amount_usd'), policies=Sum('policies_usd'), sales=Sum('sales_usd'), total=Sum('total_usd')) if lines is not None else {},
    })
