from .services.pilot_formula import default_variation
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
from decimal import Decimal
import math
import pandas as pd
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from .forms import GlobalCoeffVersionForm
from .services.versions import latest_version, effective_version, create_version, apply_to_subdivisions, effective_coefficients, today
from base.access import can_access


from .services.version_context import version_context
from .services.editor_feedback import restore_context, recover


def gloabal_coeff(request, version_form=None):
    context = version_context(request, version_form)
    selected = context['selected_version']
    from .services.coefficient_groups import global_matrix
    return render(request, 'admin_motivation/gloabal_coeff.html', {
        **context, **restore_context(request), 'goods': global_matrix(selected), 'director_employees': DirectorEmployee.objects.filter(is_archived=False),
        'segments': SegmentCoeff.objects.order_by('pk'), 'types_coeffs': TypeCoeff.objects.order_by('pk'),
        'var_calcs': VariationCalculate.objects.order_by('pk'),
        'gp_sales': PlanningGroupSales.objects.all(), 'groups': GroupGoods.objects.all(), 'brands': Brand.objects.all(),
        'subdivisions': Subdivision.objects.prefetch_related('directors'),
    })


def coefficient_number(value):
    text = str(value).strip().replace(',', '.')
    percent = text.endswith('%')
    number = float(text.rstrip('%')) / (100 if percent else 1)
    if not math.isfinite(number) or not -1 <= number <= 1:
        raise ValidationError('Коэффициенты должны быть конечными числами от -1 до 1 (от -100% до 100%).')
    return number


def upsert_product(version, group_sales, group, brand, variation_id, policies, sales, subdivision=None, preserve_settings=False):
    labels = [str(label).strip() for label in [group_sales, group, brand]]
    if not all(labels):
        raise ValidationError('Заполните группу планов продаж, группу и марку.')
    segments = list(SegmentCoeff.objects.order_by('pk'))
    if not segments or any(values is not None and len(values) != len(segments) for values in [policies, sales]):
        raise ValidationError('Число коэффициентов должно соответствовать числу сегментов.')
    good, _ = Goods.objects.get_or_create(planning_group_sales=labels[0], group=labels[1], brand=labels[2],
                                          defaults={'goods_key': ''.join(labels)})
    variation = get_object_or_404(VariationCalculate, pk=variation_id)
    for type_name, values in [('Политики', policies), ('Продажи', sales)]:
        if values is None:
            continue
        kind = get_object_or_404(TypeCoeff, type_coeff_name=type_name)
        for segment, value in zip(segments, values):
            if value is None:
                continue
            model = SubdivisionCoeff if subdivision else GlobalCoeff
            extra = {'subdivision': subdivision} if subdivision else {}
            defaults = {'motivation_coeff': coefficient_number(value)}
            settings = {'variation_calculate': variation, 'manager_coeff': 1}
            if not preserve_settings:
                defaults.update(settings)
            model.objects.update_or_create(version=version, **extra, goods=good, type_coeff=kind, segment=segment,
                defaults=defaults, create_defaults={**defaults, **settings})

    return good

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
            if (version.status != 'draft' or version.pk != latest.pk) and not (action == 'apply_sub' and can_access(request.user, 'admin')):
                raise ValidationError('Историческая версия доступна только для просмотра. Для изменений создайте новую версию.')
            if action == 'sync_references':
                PlanningGroupSales.create_from_dwh()
                GroupGoods.create_from_dwh()
                Brand.create_from_dwh()
                Subdivision.get_or_create()
            elif action == 'fill_coeff':
                variation = default_variation()
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
                import_current_coefficients(request.FILES['myfile'], version)
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
                            employees = list(DirectorEmployee.objects.filter(pk__in=ids, is_archived=False))
                            if len(employees) != len(ids):
                                raise ValidationError('Выбранный сотрудник не найден.')
                            recipients[branch.pk] = employees
                    else:
                        if not request.POST.get('director_employee'):
                            raise ValidationError('Выберите сотрудника-директора.')
                        employee = get_object_or_404(DirectorEmployee, pk=request.POST.get('director_employee'), is_archived=False)
                skipped = apply_to_subdivisions(version, subdivisions, groups=groups, employee=employee, employees_by_subdivision=recipients, user=request.user)
                if skipped:
                    messages.warning(request, 'Коэффициенты директоров пропущены в филиалах без сотрудников: ' + ', '.join(skipped) + '. Политики и продажи скопированы, если были выбраны.')
            elif action == 'save_coeff':
                from .services.save_verification import SaveVerification
                verification = SaveVerification(latest, request.POST)
                ids = [key.split('=', 1)[1] for key in request.POST if key.startswith('select_var_calc=')]
                deleted = [key.split('=', 1)[1] for key in request.POST if key.startswith('delete_coeff=')]
                # Update existing cells only; never delete cells just because a POST is partial.
                for coeff in version.coefficients.filter(goods_id__in=ids):
                    key = f'global_coeff={coeff.pk}'
                    if key in request.POST:
                        coeff.motivation_coeff = coefficient_number(request.POST[key])
                        selected_formula = request.POST[f'select_var_calc={coeff.goods_id}']
                        if request.POST.get(f'original_var_calc={coeff.goods_id}') != selected_formula:
                            coeff.variation_calculate = get_object_or_404(VariationCalculate, pk=selected_formula)
                        coeff.manager_coeff = 1
                        coeff.save()
                version.coefficients.filter(goods_id__in=deleted).delete()
                from .services.coefficient_groups import save_global_directors
                save_global_directors(version, request.POST, deleted)
                saved_count = verification.verify()
                # Keep Goods and previous versions' coefficients intact.
            else:
                raise ValidationError('Неизвестное действие.')
    except (ValidationError, ValueError, TypeError) as error:
        text = ' '.join(error.messages) if isinstance(error, ValidationError) else 'Некорректное число или формат файла.'
        messages.error(request, text)
        if action in {'save_coeff','add_product'} and version.is_editable:
            return recover(request, gloabal_coeff, version)
    except RuntimeError as error:
        messages.error(request, str(error))
    else:
        if action == 'save_coeff':
            messages.success(request, f'Сохранение проверено. Изменено полей: {saved_count}. {verification.summary()}. Версия: {version}. Выгрузка в MS SQL выполняется отдельно.' if saved_count else 'Проверено: значения уже сохранены, новых изменений нет.')
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
    from .services.coefficient_groups import coefficient_matrix
    from .director_forms import DirectorEmployeeForm
    employees = DirectorEmployee.objects.filter(is_archived=False).filter(Q(subdivisions=sub) | Q(subdivisiondirectorcoeff__subdivision=sub,
        subdivisiondirectorcoeff__version=selected)).distinct()
    employee_id = request.GET.get('employee')
    employee = (employees.filter(pk=employee_id).first() if str(employee_id).isdigit() else None) if employee_id else employees.first()
    if employee_id and employee is None:
        messages.warning(request, 'Директор удалён или недоступен в этом подразделении. Выберите действующего сотрудника. Прежние коэффициенты сохранены в истории.')
    employee_form = DirectorEmployeeForm(initial={'subdivisions': [sub.pk]})
    if request.GET.get('edit_employee'):
        employee_form = DirectorEmployeeForm(instance=get_object_or_404(DirectorEmployee, pk=request.GET['edit_employee'], is_archived=False))
    if employee_form_override is not None:
        employee_form = employee_form_override
    employee_bound = bool(employee and employee.subdivisions.filter(pk=sub.pk).exists())
    return render(request, 'admin_motivation/coef_one_sub.html', {
        **context, **restore_context(request), 'sub': sub, 'sub_coef_meneger': managers, 'kinds_mc': kinds, 'director_employees': employees, 'employee': employee,
        'director_editable': bool(context['version_editable'] and employee_bound),
        'manager_approved_editable': bool(selected and selected.status == 'approved' and can_access(request.user, 'admin')),
        'manager_correction_history': ManagerCorrectionLog.objects.filter(version=selected, subdivision=sub).select_related('actor').order_by('-pk')[:30],
        'employee_form': employee_form, 'all_employees': DirectorEmployee.objects.filter(is_archived=False).prefetch_related('subdivisions'),
        'copy_employee_options': sub.directors.exclude(pk=employee.pk) if employee else sub.directors.all(),
        'goods': coefficient_matrix(selected, sub, employee),
        'segments': SegmentCoeff.objects.order_by('pk'), 'types_coeffs': TypeCoeff.objects.order_by('pk'),
        'var_calcs': VariationCalculate.objects.order_by('pk'),
        'gp_sales': PlanningGroupSales.objects.all(), 'groups': GroupGoods.objects.all(), 'brands': Brand.objects.all(),
        'subdivisions': Subdivision.objects.all(),
    })


@require_POST
def sub_act(request, subdivision):
    sub = get_object_or_404(Subdivision, pk=subdivision)
    version = get_object_or_404(GlobalCoeffVersion, pk=request.POST.get('version'))
    action = request.POST.get('action_button')
    employee = None
    if action == 'update_approved_manager':
        from .services.approved_manager_correction import update
        try:
            if request.POST.get('confirm_manager_change') != 'yes':
                raise ValidationError('Подтвердите изменение поправки в утверждённой версии.')
            changes = {key.split('=', 1)[1]: value for key, value in request.POST.items() if key.startswith('kind_coeff=')}
            update(request.user, version.pk, sub, request.POST.get('revision'), request.POST.get('manager_reason'), changes)
            messages.success(request, 'Поправочные коэффициенты сохранены с журналом изменений. Выгрузите в MS SQL и выполните пересчёт для применения.')
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        return redirect(reverse('coeff_subdivisions_admin_motivation', args=[sub.pk]) + f'?version={version.pk}')
    try:
        with transaction.atomic():
            latest = GlobalCoeffVersion.objects.select_for_update().exclude(status='superseded').order_by('-pk').first()
            if (version.status != 'draft' or version.pk != latest.pk) and not (action == 'copy_from_global' and can_access(request.user, 'admin')):
                raise ValidationError('Историческая версия доступна только для просмотра.')
            if action == 'update_motive_coeff':
                from .services.save_verification import SaveVerification
                verification = SaveVerification(latest, request.POST, subdivision=sub, manager=True)
                for kind in KindManagerCoeff.objects.all():
                    key = f'kind_coeff={kind.pk}'
                    if key in request.POST:
                        SubdivisionManagerCoeff.objects.update_or_create(version=version, subdivision=sub, kind=kind,
                            defaults={'coeff': coefficient_number(request.POST[key])})
                saved_count = verification.verify()
            elif action == 'save_coeff':
                employee = DirectorEmployee.objects.filter(pk=request.POST['employee'], is_archived=False).first() if request.POST.get('employee') else None
                if request.POST.get('employee') and employee is None:
                    raise ValidationError('Директор удалён. Изменения не сохранены. Выберите действующего сотрудника.')
                from .services.save_verification import SaveVerification
                verification = SaveVerification(latest, request.POST, subdivision=sub, employee=employee)
                for coeff in SubdivisionCoeff.objects.filter(version=version, subdivision=sub):
                    key = f'sub_coeff={coeff.pk}'
                    if key in request.POST:
                        coeff.motivation_coeff = coefficient_number(request.POST[key])
                        select = f'select_var_calc={coeff.goods_id}'
                        if select in request.POST and request.POST.get(f'original_var_calc={coeff.goods_id}') != request.POST[select]:
                            coeff.variation_calculate = get_object_or_404(VariationCalculate, pk=request.POST[select])
                        coeff.manager_coeff = 1
                        coeff.save()
                from .services.coefficient_groups import save_global_directors
                save_global_directors(version, request.POST, subdivision=sub, employee=employee)
                saved_count = verification.verify()
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
                employee = get_object_or_404(DirectorEmployee, pk=request.POST.get('director_employee'), is_archived=False) if 'directors' in groups else None
                apply_to_subdivisions(version, [sub], groups=groups, employee=employee, user=request.user)
                if groups == {'directors'}:
                    messages.success(request, 'Глобальные коэффициенты директора скопированы выбранному сотруднику.')
                    return redirect(reverse('coeff_subdivisions_admin_motivation', args=[sub.pk]) + f'?version={version.pk}&employee={employee.pk}')
            elif action == 'file_load':
                if 'myfile' not in request.FILES:
                    raise ValidationError('Выберите Excel-файл.')
                employee = get_object_or_404(DirectorEmployee, pk=request.POST['employee']) if request.POST.get('employee') else None
                import_current_coefficients(request.FILES['myfile'], version, sub, employee)
            else:
                raise ValidationError('Неизвестное действие.')
    except (ValidationError, ValueError, TypeError) as error:
        messages.error(request, ' '.join(error.messages) if isinstance(error, ValidationError) else 'Некорректное число или формат файла.')
        if action in {'save_coeff','update_motive_coeff'} and version.is_editable:
            return recover(request, coef_one_sub, version, sub.pk)
    else:
        if action in {'save_coeff', 'update_motive_coeff'}:
            messages.success(request, f'Сохранение проверено. Изменено полей: {saved_count}. {verification.summary()}. Подразделение: {sub}. Версия: {version}. Выгрузка в MS SQL выполняется отдельно.' if saved_count else 'Проверено: значения уже сохранены, новых изменений нет.')
        else:
            messages.success(request, 'Настройки подразделения сохранены в выбранной версии.')
    chosen = employee.pk if employee else request.POST.get('employee', '')
    allowed = str(chosen).isdigit() and (DirectorEmployee.objects.filter(pk=chosen, is_archived=False).filter(Q(subdivisions=sub) | Q(subdivisiondirectorcoeff__version=version, subdivisiondirectorcoeff__subdivision=sub)).exists())
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
            return render(request, 'admin_motivation/loader_complete.html', {'subdivisions': subdivisions, 'exported_versions': versions, 'exported_discount_segments': any(version.discount_segments.exists() for version in versions)})
    return render(request, 'admin_motivation/loader.html', {'subdivisions': subdivisions, 'export_versions': GlobalCoeffVersion.objects.filter(status='approved').order_by('effective_from', 'pk')})


@require_GET
def download_coefficient_template(request, subdivision=None):
    from django.http import HttpResponse, Http404
    from .services.coefficient_template import coefficient_template
    selected = request.GET.get('version', '')
    if selected and not selected.isdecimal():
        raise Http404('Версия коэффициентов не найдена.')
    version = get_object_or_404(GlobalCoeffVersion, pk=selected) if selected else latest_version()
    sub = get_object_or_404(Subdivision, pk=subdivision) if subdivision is not None else None
    employee = None
    selected_employee = request.GET.get('employee', '')
    if sub and selected_employee:
        if not selected_employee.isdecimal():
            raise Http404('Директор не найден.')
        employee = get_object_or_404(DirectorEmployee.objects.filter(Q(subdivisions=sub) |
            Q(subdivisiondirectorcoeff__version=version, subdivisiondirectorcoeff__subdivision=sub)).distinct(), pk=selected_employee)
    response = HttpResponse(coefficient_template(version, sub, employee),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    scope = f'subdivision-{sub.pk}' if sub else 'global'
    response['Content-Disposition'] = f'attachment; filename="motivation-coefficients-{scope}.xlsx"'
    return response


@transaction.atomic
def import_current_coefficients(upload, version, subdivision=None, employee=None):
    df = pd.read_excel(upload, skiprows=1)
    count = SegmentCoeff.objects.count()
    variation = default_variation()
    if not variation or not count or len(df.columns) not in (4 + 2 * count, 9 + 2 * count, 10 + 2 * count):
        raise ValidationError('Файл не соответствует шаблону или не настроены уровни мотивации.')
    for _, row in df.iterrows():
        if row.isna().all():
            continue
        values = row.tolist()[1:]
        if any(pd.isna(value) for value in values[:3]):
            raise ValidationError('Заполните группу планов продаж, группу и марку.')
        def block(items, allow_missing=False):
            missing = [pd.isna(value) for value in items]
            if all(missing):
                return None
            if any(missing) and not allow_missing:
                raise ValidationError('Заполните все коэффициенты группы или оставьте всю группу пустой.')
            return [None if pd.isna(value) else coefficient_number(value) for value in items]
        policies = block(values[3:3 + count], allow_missing=True)
        sales = block(values[3 + count:3 + 2 * count], allow_missing=True)
        directors = block(values[3 + 2 * count:8 + 2 * count]) if len(df.columns) >= 9 + 2 * count else None
        formula = None
        if len(df.columns) == 10 + 2 * count and not pd.isna(values[-1]) and str(values[-1]).strip():
            formula = VariationCalculate.objects.filter(variation_name=str(values[-1]).strip()).first()
            if formula is None:
                raise ValidationError(f'Неизвестная формула: {values[-1]}. Выберите значение из списка шаблона.')
        if policies is None and sales is None and directors is None and formula is None:
            continue
        if directors is not None and subdivision and (not employee or not employee.subdivisions.filter(pk=subdivision.pk).exists()):
            raise ValidationError('Для загрузки директорских коэффициентов выберите директора этого подразделения.')
        good = upsert_product(version, *values[:3], variation.pk, policies, sales, subdivision, preserve_settings=True)
        if formula is not None:
            model = SubdivisionCoeff if subdivision else GlobalCoeff
            scope = {'subdivision': subdivision} if subdivision else {}
            coefficients = model.objects.filter(version=version, goods=good, **scope)
            current = coefficients.order_by('type_coeff_id', 'segment_id', 'pk').values_list('variation_calculate_id', flat=True).first()
            # An unchanged downloaded selector must preserve any legacy per-cell settings.
            if current != formula.pk:
                coefficients.update(variation_calculate=formula)
        if directors is not None:
            model = SubdivisionDirectorCoeff if subdivision else GlobalDirectorCoeff
            scope = {'subdivision': subdivision, 'employee': employee} if subdivision else {}
            model.objects.update_or_create(version=version, goods=good, **scope,
                defaults={f'k{i}': Decimal(str(value)) for i, value in enumerate(directors)})
