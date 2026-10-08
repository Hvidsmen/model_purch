import csv
import logging
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from .forms import SalesPlanScenarioForm
from .models import SalesPlanScenario, Subdivision
from .services.sales_plans import load_plan, calculate_plan, source_versions
from .services.plan_operations import operation_response, scenario_operation
from .services.plan_report import period_report

logger = logging.getLogger(__name__)


def sales_plans(request, scenario_id=None):
    scenario = get_object_or_404(SalesPlanScenario, pk=scenario_id) if scenario_id else None
    form = SalesPlanScenarioForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'create' else None)
    versions = request.session.get('sales_plan_source_versions', [])
    if request.method == 'POST':
        action = request.POST.get('action')
        if scenario and action in ['load', 'calculate'] and 'application/x-ndjson' in request.headers.get('Accept', ''):
            return operation_response(scenario.pk, action, load_plan if action == 'load' else calculate_plan)
        try:
            if action == 'create' and form.is_valid():
                created = SalesPlanScenario.objects.create(**form.cleaned_data)
                return redirect('motivation_sales_plan', scenario_id=created.pk)
            elif action == 'refresh_sources':
                versions = source_versions()
                request.session['sales_plan_source_versions'] = versions
                messages.success(request, f'Найдено версий плана: {len(versions)}.')
            elif scenario and action == 'load':
                with scenario_operation(scenario.pk):
                    count = load_plan(scenario.pk)
                messages.success(request, f'План загружен: {count} строк подразделений. Глобальный план сформирован суммированием.')
            elif scenario and action == 'calculate':
                with scenario_operation(scenario.pk):
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
    report = period_report(scenario, scope, request.GET) if scenario else {}
    lines = report.get('report_lines')
    if scenario and request.GET.get('export') in ['csv', 'details']:
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="motivation-plan-{scenario.pk}-{scope}.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        if request.GET.get('export') == 'csv':
            writer.writerow(['Подразделение', 'Группа планов продаж', 'Группа планов ERP', 'Марка / бренд', 'План USD', 'Политики USD', 'Продажи USD', 'Итого мотивация USD'])
            for row in report['report_rows']:
                labels = [row[field] or 'Глобальный план' for field in ['subdivision', 'planning_group_sales', 'group', 'brand']]
                labels = ["'" + label if label.lstrip().startswith(('=', '+', '-', '@')) else label for label in labels]
                writer.writerow([*labels, row['plan'], row['policies'], row['sales'], row['total']])
            return response
        # Optional audit export retains original dated coefficient snapshots.
        lines = lines.select_related('coefficient_version')
        writer.writerow(['Дата', 'Подразделение', 'Группа планов', 'Группа', 'Марка', 'План USD', *[f'USD_O{i}' for i in range(5)], 'Версия коэффициентов', 'Политики USD', 'Продажи USD', 'Итого USD', *[f'{kind} {field} O{i}' for kind in ['Политики', 'Продажи'] for i in range(5) for field in ['k', 'Мотивация', 'Источник', 'Версия источника', 'Подразделение источника', 'Группа планов источника']]])
        for line in lines.iterator():
            details = [line.calculation.get(kind, {}).get(f'O{i}', {}).get(field, '') for kind in ['Политики', 'Продажи'] for i in range(5) for field in ['coefficient', 'motivation', 'source', 'source_version', 'source_subdivision', 'source_planning_group']]
            values = [line.plan_date, line.subdivision or 'Глобальный план', line.planning_group_sales, line.group, line.brand, line.amount_usd, *[line.segment_amounts.get(f'O{i}', '') for i in range(5)], str(line.coefficient_version or ''), line.policies_usd, line.sales_usd, line.total_usd, *details]
            # Escape external labels only; negative numeric results remain numeric.
            label_indices = [1, 2, 3, 4, 11] + [i for i in range(15, len(values)) if (i - 15) % 6 >= 3]
            for index in label_indices:
                value = str(values[index])
                if value.lstrip().startswith(('=', '+', '-', '@')):
                    values[index] = "'" + value
            writer.writerow(values)
        return response
    return render(request, 'admin_motivation/sales_plans.html', {
        'scenario': scenario, 'scenarios': SalesPlanScenario.objects.all(), 'form': form,
        'source_versions': versions, 'scope': scope, 'subdivisions': Subdivision.objects.all(),
        **report,
    })
