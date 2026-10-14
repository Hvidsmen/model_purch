from django.core.files.storage import FileSystemStorage
from django.shortcuts import render

from .models import *


# Create your views here.


def index(request):
    return render(request, "admin_motivation/index.html", )


def goods_matrix(request):
    goods = Goods.get_matrix_str()
    print(goods)
    return render(request, "admin_motivation/goods_matrix.html", {'goods': goods})


from datetime import timedelta
import math
import pandas as pd
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import GlobalCoeffVersionForm
from .services.versions import latest_version, effective_version, create_version, apply_to_subdivisions, effective_coefficients, today


from .services.version_context import version_context


def gloabal_coeff(request, version_form=None):
    context = version_context(request, version_form)
    selected = context['selected_version']
    example = ExampleFiles.objects.filter(pk=1).first()
    from .services.coefficient_groups import global_matrix
    return render(request, 'admin_motivation/gloabal_coeff.html', {
        **context, 'goods': global_matrix(selected), 'director_employees': DirectorEmployee.objects.all(),
        'segments': SegmentCoeff.objects.order_by('pk'), 'types_coeffs': TypeCoeff.objects.order_by('pk'),
        'var_calcs': VariationCalculate.objects.order_by('pk'),
        'gp_sales': PlanningGroupSales.objects.all(), 'groups': GroupGoods.objects.all(), 'brands': Brand.objects.all(),
        'subdivisions': Subdivision.objects.prefetch_related('directors'), 'templates_file': example.file if example else None,
    })


def coefficient_number(value):
    text = str(value).strip().replace(',', '.')
    percent = text.endswith('%')
    number = float(text.rstrip('%')) / (100 if percent else 1)
    if not math.isfinite(number) or not -1 <= number <= 1:
        raise ValidationError('Коэффициенты должны быть конечными числами от -1 до 1 (от -100% до 100%).')
    return number


def upsert_product(version, group_sales, group, brand, variation_id, policies, sales, subdivision=None):
    labels = [str(label).strip() for label in [group_sales, group, brand]]
    if not all(labels):
        raise ValidationError('Заполните группу планов продаж, группу и марку.')
    segments = list(SegmentCoeff.objects.order_by('pk'))
    if not segments or len(policies) != len(segments) or len(sales) != len(segments):
        raise ValidationError('Число коэффициентов должно соответствовать числу сегментов.')
    good, _ = Goods.objects.get_or_create(planning_group_sales=labels[0], group=labels[1], brand=labels[2],
                                          defaults={'goods_key': ''.join(labels)})
    variation = get_object_or_404(VariationCalculate, pk=variation_id)
    for type_name, values in [('Политики', policies), ('Продажи', sales)]:
        kind = get_object_or_404(TypeCoeff, type_coeff_name=type_name)
        for segment, value in zip(segments, values):
            model = SubdivisionCoeff if subdivision else GlobalCoeff
            extra = {'subdivision': subdivision} if subdivision else {}
            model.objects.update_or_create(version=version, **extra, goods=good, type_coeff=kind, segment=segment,
                defaults={'variation_calculate': variation, 'motivation_coeff': coefficient_number(value), 'manager_coeff': 1})


def coefficient_return_route(request, subdivision=None):
    if request.POST.get('return_directors') == 'global':
        return reverse('global_coeff_admin_motivation') + '?'
    route = reverse('coeff_subdivisions_admin_motivation', args=[subdivision.pk]) if subdivision else reverse('global_coeff_admin_motivation')
    employee = request.POST.get('return_employee', '')
    return route + (f'?employee={employee}&' if subdivision and employee.isdigit() else '?')


@require_POST
def gloabal_coeff_action(request):
    action = request.POST.get('action_button')
    if action == 'copy_version':
        from .services.versions import copy_from_version
        destination = get_object_or_404(GlobalCoeffVersion, pk=request.POST.get('version'))
        sub = get_object_or_404(Subdivision, pk=request.POST['return_subdivision']) if request.POST.get('return_subdivision') else None
        try:
            if request.POST.get('confirm_copy') != 'yes':
                raise ValidationError('Подтвердите замену текущих коэффициентов.')
            employee = get_object_or_404(DirectorEmployee, pk=request.POST['return_employee']) if sub and request.POST.get('return_employee') else None
            count = copy_from_version(destination.pk, int(request.POST.get('copy_source', '')), sub, employee=employee)
            messages.success(request, f'Скопировано коэффициентов: {count}. Дата и статус черновика сохранены.')
        except (ValidationError, ValueError) as error:
            messages.error(request, ' '.join(error.messages) if isinstance(error, ValidationError) else 'Выберите версию-источник.')
        route = coefficient_return_route(request, sub)
        return redirect(route + f'version={destination.pk}')
    if action == 'delete_version':
        from .services.versions import delete_draft
        destination = get_object_or_404(Subdivision, pk=request.POST['return_subdivision']) if request.POST.get('return_subdivision') else None
        version = get_object_or_404(GlobalCoeffVersion, pk=request.POST.get('version'))
        target = version
        try:
            if request.POST.get('confirm_delete') != 'yes':
                raise ValidationError('Подтвердите удаление черновика.')
            target = delete_draft(version.pk)
            messages.success(request, 'Черновик и его коэффициенты удалены. Другие версии сохранены.')
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        route = coefficient_return_route(request, destination)
        return redirect(route + (f'version={target.pk}' if target else ''))
    if action == 'create_version':
        destination = get_object_or_404(Subdivision, pk=request.POST['return_subdivision']) if request.POST.get('return_subdivision') else None
        form = GlobalCoeffVersionForm(request.POST)
        if form.is_valid():
            try:
                version = create_version(**form.cleaned_data)
            except ValidationError as error:
                for message in error.messages:
                    form.add_error('effective_from', message)
            else:
                messages.success(request, f'Создана версия с {version.effective_from:%d.%m.%Y}. Коэффициенты скопированы.')
                route = coefficient_return_route(request, destination)
                return redirect(route + f'version={version.pk}')
        if request.POST.get('return_directors') in ('global', 'local'):
            messages.error(request, 'Версия не создана: ' + ' '.join(str(error) for errors in form.errors.values() for error in errors))
            return redirect(coefficient_return_route(request, destination))
        return coef_one_sub(request, destination.pk, version_form=form) if destination else gloabal_coeff(request, version_form=form)
    version = get_object_or_404(GlobalCoeffVersion, pk=request.POST.get('version'))
    try:
        with transaction.atomic():
            latest = GlobalCoeffVersion.objects.select_for_update().exclude(status='superseded').order_by('-pk').first()
            if version.status != 'draft' or version.pk != latest.pk:
                raise ValidationError('Историческая версия доступна только для просмотра. Для изменений создайте новую версию.')
            if action == 'sync_references':
                PlanningGroupSales.create_from_dwh()
                GroupGoods.create_from_dwh()
                Brand.create_from_dwh()
                Subdivision.get_or_create()
            elif action == 'fill_coeff':
                variation = VariationCalculate.objects.order_by('pk').first()
                if not variation:
                    raise ValidationError('Сначала добавьте уровень мотивации.')
                for good in Goods.objects.all():
                    for segment in SegmentCoeff.objects.all():
                        for kind in TypeCoeff.objects.all():
                            GlobalCoeff.objects.get_or_create(version=version, goods=good, segment=segment, type_coeff=kind,
                                defaults={'motivation_coeff': 0.001, 'manager_coeff': 1, 'variation_calculate': variation})
            elif action == 'add_product':
                count = SegmentCoeff.objects.count()
                upsert_product(version, request.POST.get('pg_sales', ''), request.POST.get('group', ''), request.POST.get('brand', ''),
                               request.POST.get('select_var_calc'), [request.POST.get(f'k{i}', '') for i in range(count)],
                               [request.POST.get(f's{i}', '') for i in range(count)])
            elif action == 'file_load':
                if 'myfile' not in request.FILES:
                    raise ValidationError('Выберите Excel-файл.')
                df = pd.read_excel(request.FILES['myfile'], skiprows=1)
                count = SegmentCoeff.objects.count()
                variation = VariationCalculate.objects.order_by('pk').first()
                if not variation or len(df.columns) != 4 + 2 * count:
                    raise ValidationError('Файл не соответствует шаблону или не настроены уровни мотивации.')
                for _, row in df.iterrows():
                    values = row.tolist()[1:]
                    upsert_product(version, *values[:3], variation.pk, values[3:3+count], values[3+count:])
            elif action == 'apply_sub':
                subdivisions = Subdivision.objects.filter(pk__in=request.POST.getlist('selected_subdivisions'))
                if not subdivisions.exists():
                    raise ValidationError('Выберите подразделения.')
                from .services.coefficient_groups import selected_groups
                groups = selected_groups(request.POST.getlist('copy_groups'), default=() if request.POST.get('copy_group_selection') == 'yes' else ('policies', 'sales'))
                recipients = None
                employee = None
                if 'directors' in groups:
                    if request.POST.get('branch_employee_selection') == 'yes':
                        recipients = {}
                        for branch in subdivisions:
                            ids = set(request.POST.getlist(f'director_employees_{branch.pk}'))
                            employees = list(DirectorEmployee.objects.filter(pk__in=ids))
                            if len(employees) != len(ids):
                                raise ValidationError('Выбранный сотрудник не найден.')
                            recipients[branch.pk] = employees
                    else:
                        if not request.POST.get('director_employee'):
                            raise ValidationError('Выберите сотрудника-директора.')
                        employee = get_object_or_404(DirectorEmployee, pk=request.POST.get('director_employee'))
                skipped = apply_to_subdivisions(version, subdivisions, groups=groups, employee=employee, employees_by_subdivision=recipients)
                if skipped:
                    messages.warning(request, 'Коэффициенты директоров пропущены в филиалах без сотрудников: ' + ', '.join(skipped) + '. Политики и продажи скопированы, если были выбраны.')
            elif action == 'save_coeff':
                ids = [key.split('=', 1)[1] for key in request.POST if key.startswith('select_var_calc=')]
                deleted = [key.split('=', 1)[1] for key in request.POST if key.startswith('delete_coeff=')]
                # Update existing cells only; never delete cells just because a POST is partial.
                for coeff in version.coefficients.filter(goods_id__in=ids):
                    key = f'global_coeff={coeff.pk}'
                    if key in request.POST:
                        coeff.motivation_coeff = coefficient_number(request.POST[key])
                        coeff.variation_calculate = get_object_or_404(VariationCalculate, pk=request.POST[f'select_var_calc={coeff.goods_id}'])
                        coeff.manager_coeff = 1
                        coeff.save()
                version.coefficients.filter(goods_id__in=deleted).delete()
                from .services.coefficient_groups import save_global_directors
                save_global_directors(version, request.POST, deleted)
                # Keep Goods and previous versions' coefficients intact.
            else:
                raise ValidationError('Неизвестное действие.')
    except (ValidationError, ValueError, TypeError) as error:
        text = ' '.join(error.messages) if isinstance(error, ValidationError) else 'Некорректное число или формат файла.'
        messages.error(request, text)
    except RuntimeError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, 'Изменения сохранены в выбранной версии.')
    return redirect(reverse('global_coeff_admin_motivation') + f'?version={version.pk}')


def coef_one_sub(request, subdivision, version_form=None, employee_form_override=None):
    sub = get_object_or_404(Subdivision, pk=subdivision)
    context = version_context(request, version_form)
    selected = context['selected_version']
    kinds = list(KindManagerCoeff.objects.order_by('pk'))
    existing = {row.kind_id: row for row in SubdivisionManagerCoeff.objects.filter(version=selected, subdivision=sub).select_related('kind')}
    managers = [existing.get(kind.pk) or SubdivisionManagerCoeff(version=selected, subdivision=sub, kind=kind, coeff=1) for kind in kinds]
    from django.db.models import Q
    from .services.coefficient_groups import coefficient_matrix
    from .director_forms import DirectorEmployeeForm
    employees = DirectorEmployee.objects.filter(Q(subdivisions=sub) | Q(subdivisiondirectorcoeff__subdivision=sub,
        subdivisiondirectorcoeff__version=selected)).distinct()
    employee_id = request.GET.get('employee')
    employee = get_object_or_404(employees, pk=employee_id) if employee_id else None
    employee_form = DirectorEmployeeForm(initial={'subdivisions': [sub.pk]})
    if request.GET.get('edit_employee'):
        employee_form = DirectorEmployeeForm(instance=get_object_or_404(DirectorEmployee, pk=request.GET['edit_employee']))
    if employee_form_override is not None:
        employee_form = employee_form_override
    employee_bound = bool(employee and employee.subdivisions.filter(pk=sub.pk).exists())
    example = ExampleFiles.objects.filter(pk=1).first()
    return render(request, 'admin_motivation/coef_one_sub.html', {
        **context, 'sub': sub, 'sub_coef_meneger': managers, 'kinds_mc': kinds, 'director_employees': employees, 'employee': employee,
        'director_editable': bool(context['version_editable'] and employee_bound),
        'employee_form': employee_form, 'all_employees': DirectorEmployee.objects.prefetch_related('subdivisions'),
        'copy_employee_options': sub.directors.exclude(pk=employee.pk) if employee else sub.directors.all(),
        'goods': coefficient_matrix(selected, sub, employee),
        'segments': SegmentCoeff.objects.order_by('pk'), 'types_coeffs': TypeCoeff.objects.order_by('pk'),
        'var_calcs': VariationCalculate.objects.order_by('pk'),
        'gp_sales': PlanningGroupSales.objects.all(), 'groups': GroupGoods.objects.all(), 'brands': Brand.objects.all(),
        'subdivisions': Subdivision.objects.all(), 'templates_file': example.file if example else None,
    })


@require_POST
def sub_act(request, subdivision):
    sub = get_object_or_404(Subdivision, pk=subdivision)
    version = get_object_or_404(GlobalCoeffVersion, pk=request.POST.get('version'))
    action = request.POST.get('action_button')
    employee = None
    try:
        with transaction.atomic():
            latest = GlobalCoeffVersion.objects.select_for_update().exclude(status='superseded').order_by('-pk').first()
            if version.status != 'draft' or version.pk != latest.pk:
                raise ValidationError('Историческая версия доступна только для просмотра.')
            if action == 'update_motive_coeff':
                for kind in KindManagerCoeff.objects.all():
                    key = f'kind_coeff={kind.pk}'
                    if key in request.POST:
                        SubdivisionManagerCoeff.objects.update_or_create(version=version, subdivision=sub, kind=kind,
                            defaults={'coeff': coefficient_number(request.POST[key])})
            elif action == 'save_coeff':
                employee = get_object_or_404(DirectorEmployee, pk=request.POST['employee']) if request.POST.get('employee') else None
                for coeff in SubdivisionCoeff.objects.filter(version=version, subdivision=sub):
                    key = f'sub_coeff={coeff.pk}'
                    if key in request.POST:
                        coeff.motivation_coeff = coefficient_number(request.POST[key])
                        select = f'select_var_calc={coeff.goods_id}'
                        if select in request.POST:
                            coeff.variation_calculate = get_object_or_404(VariationCalculate, pk=request.POST[select])
                        coeff.manager_coeff = 1
                        coeff.save()
                from .services.coefficient_groups import save_global_directors
                save_global_directors(version, request.POST, subdivision=sub, employee=employee)
            elif action == 'copy_to_director':
                from .services.directors import copy_to_employee
                if request.POST.get('confirm_copy') != 'yes':
                    raise ValidationError('Подтвердите замену коэффициентов другого директора.')
                employee = get_object_or_404(DirectorEmployee, pk=request.POST.get('employee'))
                target = get_object_or_404(DirectorEmployee, pk=request.POST.get('copy_to_employee'))
                copy_to_employee(version.pk, sub, employee, target)
                employee = target
            elif action == 'employee_save':
                from .director_forms import DirectorEmployeeForm
                existing = get_object_or_404(DirectorEmployee, pk=request.POST['employee_edit']) if request.POST.get('employee_edit') else None
                form = DirectorEmployeeForm(request.POST, instance=existing)
                if not form.is_valid():
                    return coef_one_sub(request, sub.pk, employee_form_override=form)
                employee = form.save()
                if not employee.subdivisions.filter(pk=sub.pk).exists():
                    employee = None
            elif action == 'copy_from_global':
                from .services.coefficient_groups import selected_groups
                groups = selected_groups(request.POST.getlist('copy_groups'), default=() if request.POST.get('copy_group_selection') == 'yes' else ('policies', 'sales'))
                if 'directors' in groups and not request.POST.get('director_employee'):
                    raise ValidationError('Выберите сотрудника-директора.')
                employee = get_object_or_404(DirectorEmployee, pk=request.POST.get('director_employee')) if 'directors' in groups else None
                apply_to_subdivisions(version, [sub], groups=groups, employee=employee)
                if groups == {'directors'}:
                    messages.success(request, 'Глобальные коэффициенты директора скопированы выбранному сотруднику.')
                    return redirect(reverse('coeff_subdivisions_admin_motivation', args=[sub.pk]) + f'?version={version.pk}&employee={employee.pk}')
            elif action == 'file_load':
                if 'myfile' not in request.FILES:
                    raise ValidationError('Выберите Excel-файл.')
                df = pd.read_excel(request.FILES['myfile'], skiprows=1)
                count = SegmentCoeff.objects.count()
                variation = VariationCalculate.objects.order_by('pk').first()
                if not variation or len(df.columns) != 4 + 2 * count:
                    raise ValidationError('Файл не соответствует шаблону или не настроены уровни мотивации.')
                for _, row in df.iterrows():
                    values = row.tolist()[1:]
                    upsert_product(version, *values[:3], variation.pk, values[3:3+count], values[3+count:], subdivision=sub)
            else:
                raise ValidationError('Неизвестное действие.')
    except (ValidationError, ValueError, TypeError) as error:
        messages.error(request, ' '.join(error.messages) if isinstance(error, ValidationError) else 'Некорректное число или формат файла.')
    else:
        messages.success(request, 'Настройки подразделения сохранены в выбранной версии.')
    chosen = employee.pk if employee else request.POST.get('employee', '')
    allowed = str(chosen).isdigit() and (DirectorEmployee.objects.filter(pk=chosen, subdivisions=sub).exists() or SubdivisionDirectorCoeff.objects.filter(version=version, subdivision=sub, employee_id=chosen).exists())
    suffix = f'&employee={chosen}' if allowed else ''
    return redirect(reverse('coeff_subdivisions_admin_motivation', args=[sub.pk]) + f'?version={version.pk}' + suffix)


from .conns import *


def loader_motive(request):
    from .services.sql_export import export_motivation
    subdivisions = Subdivision.objects.all()
    if request.method == 'POST':
        try:
            versions = export_motivation(connect_database)
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        except Exception as error:
            messages.error(request, f'Не удалось выгрузить настройки в DataWH: {error}')
        else:
            return render(request, 'admin_motivation/loader_complete.html', {'subdivisions': subdivisions, 'exported_versions': versions})
    return render(request, 'admin_motivation/loader.html', {'subdivisions': subdivisions, 'export_versions': GlobalCoeffVersion.objects.filter(status='approved').order_by('effective_from', 'pk')})
