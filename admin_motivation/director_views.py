from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.forms import modelformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.http import HttpResponse
from django.core.exceptions import PermissionDenied
from django.views.decorators.http import require_http_methods
from .models import (Subdivision, DirectorEmployee, GlobalDirectorCoeff,
                     SubdivisionDirectorCoeff, Goods)
from .director_forms import (DirectorEmployeeForm, GlobalDirectorForm, LocalDirectorForm,
                             GlobalDirectorAddForm, LocalDirectorAddForm)
from .services.directors import lock_editable, copy_directors
from .services.version_context import version_context


@require_http_methods(['GET', 'POST'])
def coefficient_group_copy(request):
    from base.access import can_access
    from .models import GlobalCoeffVersion, CoefficientGroupCopyLog
    from .services.group_copy import copy_group
    from .services.coefficient_groups import GROUP_NAMES
    data = request.POST if request.method == 'POST' else request.GET
    version = get_object_or_404(GlobalCoeffVersion, pk=data.get('version'))
    sub = get_object_or_404(Subdivision, pk=data['subdivision']) if data.get('subdivision') else None
    employee = get_object_or_404(DirectorEmployee, pk=data['employee']) if data.get('employee') else None
    if not can_access(request.user, 'admin') and not version.is_editable:
        raise PermissionDenied
    if request.method == 'POST':
        try:
            if data.get('confirm') != 'yes':
                raise ValidationError('Подтвердите замену коэффициентов.')
            count = copy_group(request.user, version.pk, data.get('revision'), data.get('source'), data.get('target'), sub, employee)
            messages.success(request, f'Коэффициенты скопированы. Изменено записей: {count}.')
            route, params = director_route(sub, employee)
            params.append(f'version={version.pk}')
            return redirect(route + '?' + '&'.join(params))
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
            version.refresh_from_db()
    history = list(CoefficientGroupCopyLog.objects.filter(version=version, subdivision=sub).order_by('-pk')[:20])
    for log in history:
        log.source_label = {**GROUP_NAMES, 'global': 'Глобальные'}.get(log.source_group, log.source_group)
        log.target_label = {**GROUP_NAMES, 'branch': 'Филиал'}.get(log.target_group, log.target_group)
    return render(request, 'admin_motivation/group_copy.html', {
        'selected_version': version, 'sub': sub, 'employee': employee, 'groups': GROUP_NAMES,
        'subdivisions': Subdivision.objects.all(),
        'copy_history': history,
    })


@require_http_methods(['GET', 'POST'])
def director_directory(request):
    from .services.director_import import template, import_directors
    if request.method == 'POST' and request.POST.get('action') == 'delete':
        from .operation_views import admin_required
        admin_required(request)
        employee = get_object_or_404(DirectorEmployee, pk=request.POST.get('employee'))
        if request.POST.get('confirm') != 'yes':
            messages.error(request, 'Подтвердите удаление директора.')
        else:
            try:
                with transaction.atomic():
                    employee = DirectorEmployee.objects.select_for_update().get(pk=employee.pk)
                    if employee.subdivisiondirectorcoeff_set.exists():
                        employee.is_archived = True
                        employee.save(update_fields=['is_archived'])
                        employee.subdivisions.clear()
                    else:
                        employee.delete()
                messages.success(request, 'Директор удалён из справочника. Он больше не доступен для новых настроек. Прежние коэффициенты сохранены для истории расчётов.' if employee.is_archived else 'Директор удалён из справочника.')
            except DirectorEmployee.DoesNotExist:
                messages.info(request, 'Директор уже удалён.')
        return redirect('motivation_director_directory')
    if request.method == 'GET' and request.GET.get('template'):
        response = HttpResponse(template(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="directors.xlsx"'
        return response
    if request.method == 'POST' and request.POST.get('action') == 'import':
        try:
            count = import_directors(request.FILES.get('file'))
            messages.success(request, f'Загружено директоров: {count}.')
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
        return redirect('motivation_director_directory')
    data = request.POST if request.method == 'POST' else request.GET
    employee_id = data.get('edit_employee')
    employee = get_object_or_404(DirectorEmployee, pk=employee_id, is_archived=False) if employee_id else None
    form = DirectorEmployeeForm(request.POST if request.method == 'POST' else None, instance=employee)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Директор сохранён.')
        return redirect('motivation_director_directory')
    name_query = request.GET.get('name', '').strip()
    branch_id = request.GET.get('branch', '')
    branches = list(Subdivision.objects.all())
    selected_branch = next((branch for branch in branches if str(branch.pk) == branch_id), None)
    show_archived = request.GET.get('archived') == '1'
    employees = DirectorEmployee.objects.filter(is_archived=show_archived).order_by('full_name').prefetch_related('subdivisions')
    if selected_branch:
        employees = employees.filter(Q(subdivisions=selected_branch) | Q(subdivisiondirectorcoeff__subdivision=selected_branch)).distinct() if show_archived else employees.filter(subdivisions=selected_branch).distinct()
    # SQLite does not implement case-insensitive matching for Cyrillic.
    employees = [item for item in employees if name_query.casefold() in item.full_name.casefold()]
    return render(request, 'admin_motivation/director_directory.html', {
        'employee_form': form,
        'all_employees': employees, 'show_archived': show_archived,
        'subdivisions': branches,
        'name_query': name_query,
        'selected_branch': selected_branch,
        'directory_filtered': bool(name_query or selected_branch),
    })


def director_route(subdivision=None, employee=None):
    route = reverse('coeff_subdivisions_admin_motivation', args=[subdivision.pk]) if subdivision else reverse('global_coeff_admin_motivation')
    params = []
    if employee:
        params.append(f'employee={employee.pk}')
    return route, params


@require_http_methods(['GET', 'POST'])
def director_coefficients(request, local=False, version_form=None):
    if not local and request.method == 'GET':
        route = reverse('global_coeff_admin_motivation')
        query = request.GET.urlencode()
        return redirect(route + ('?' + query if query else ''))
    if local and request.method == 'GET':
        if request.GET.get('subdivision'):
            sub = get_object_or_404(Subdivision, pk=request.GET['subdivision'])
            query = request.GET.copy()
            query.pop('subdivision', None)
            return redirect(reverse('coeff_subdivisions_admin_motivation', args=[sub.pk]) + ('?' + query.urlencode() if query else ''))
        sub = Subdivision.objects.order_by('pk').first()
        return redirect(reverse('coeff_subdivisions_admin_motivation', args=[sub.pk]) if sub else reverse('global_coeff_admin_motivation'))
    context = version_context(request, version_form)
    version = context['selected_version']
    params = request.POST if request.method == 'POST' else request.GET
    sub_id = params.get('subdivision') if local else None
    sub = get_object_or_404(Subdivision, pk=sub_id) if sub_id else None
    employee_id = params.get('employee') if local else None
    employee = get_object_or_404(DirectorEmployee, pk=employee_id) if employee_id else None
    if employee and not sub:
        employee = None
    employees = DirectorEmployee.objects.none()
    if sub:
        employees = DirectorEmployee.objects.filter(Q(subdivisions=sub) | Q(subdivisiondirectorcoeff__subdivision=sub,
            subdivisiondirectorcoeff__version=version)).distinct()
        if employee and not employees.filter(pk=employee.pk).exists():
            employee = None
    model = SubdivisionDirectorCoeff if local else GlobalDirectorCoeff
    scope = {'subdivision': sub, 'employee': employee} if local else {}
    rows = model.objects.filter(version=version, **scope).select_related('goods').order_by(
        'goods__planning_group_sales', 'goods__group', 'goods__brand') if not local or (sub and employee) else model.objects.none()
    form_type = LocalDirectorForm if local else GlobalDirectorForm
    add_type = LocalDirectorAddForm if local else GlobalDirectorAddForm
    formset_type = modelformset_factory(model, form=form_type, extra=0, can_delete=True)
    formset = formset_type(queryset=rows, prefix='coeff')
    employee_form = DirectorEmployeeForm(initial={'subdivisions': [sub.pk] if sub else []})
    add_form = add_type(prefix='add')
    search = request.GET.get('search', '').strip()
    route, redirect_params = director_route(sub if local else None, employee)
    if local and not sub:
        route = reverse('motivation_directors_local')
    if version:
        redirect_params.append(f'version={version.pk}')
    target = route + ('?' + '&'.join(redirect_params) if redirect_params else '')
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if request.POST.get('version') != str(version.pk):
                raise ValidationError('Версия изменилась. Обновите страницу.')
            with transaction.atomic():
                lock_editable(version.pk)
                if action == 'employee_save':
                    existing = get_object_or_404(DirectorEmployee, pk=request.POST['employee_edit']) if request.POST.get('employee_edit') else None
                    employee_form = DirectorEmployeeForm(request.POST, instance=existing)
                    if employee_form.is_valid():
                        employee_form.save()
                        messages.success(request, 'Сотрудник и его привязки к филиалам сохранены.')
                        return redirect(target)
                elif local and (not sub or not employee):
                    raise ValidationError('Выберите филиал и сотрудника.')
                elif local and not employee.subdivisions.filter(pk=sub.pk).exists():
                    raise ValidationError('Для изменения сотрудник должен быть привязан к выбранному филиалу.')
                elif action == 'seed_matrix' and not local:
                    existing_goods = GlobalDirectorCoeff.objects.filter(version=version).values_list('goods_id', flat=True)
                    missing = list(Goods.objects.exclude(pk__in=existing_goods).values_list('pk', flat=True))
                    GlobalDirectorCoeff.objects.bulk_create([GlobalDirectorCoeff(version=version, goods_id=pk) for pk in missing])
                    messages.success(request, f'Добавлено строк товарной матрицы: {len(missing)}. Существующие коэффициенты сохранены.')
                    return redirect(target)
                elif action == 'save':
                    formset = formset_type(request.POST, queryset=rows, prefix='coeff')
                    if any(not form.instance.pk for form in formset.forms):
                        raise ValidationError('В таблице есть строка другого сотрудника или версии. Обновите страницу.')
                    if formset.is_valid():
                        formset.save()
                        messages.success(request, 'Коэффициенты директоров сохранены.')
                        return redirect(target)
                elif action == 'add':
                    instance = model(version=version, **scope)
                    add_form = add_type(request.POST, prefix='add', instance=instance)
                    if add_form.is_valid():
                        if rows.filter(goods=add_form.cleaned_data['goods']).exists():
                            add_form.add_error('goods', 'Эта строка уже есть в таблице.')
                        else:
                            add_form.save()
                            messages.success(request, 'Строка товарной матрицы добавлена.')
                            return redirect(target)
                elif action in ('copy_global', 'copy_version'):
                    if request.POST.get('confirm_copy') != 'yes':
                        raise ValidationError('Подтвердите замену текущих коэффициентов.')
                    source = int(request.POST.get('source_version', '')) if action == 'copy_version' else None
                    count = copy_directors(version.pk, source, sub, employee, from_global=action == 'copy_global')
                    messages.success(request, f'Скопировано строк: {count}. Дата версии сохранена.')
                    return redirect(target)
                else:
                    raise ValidationError('Неизвестное действие.')
        except (ValidationError, ValueError) as error:
            messages.error(request, ' '.join(error.messages) if isinstance(error, ValidationError) else 'Проверьте выбранную версию.')
            return redirect(target)
    edit_employee = request.GET.get('edit_employee')
    if edit_employee:
        employee_form = DirectorEmployeeForm(instance=get_object_or_404(DirectorEmployee, pk=edit_employee))
    # Search hides rows in the browser; keep the complete formset for partial-safe saves.
    return render(request, 'admin_motivation/director_coefficients.html', {
        **context, 'director_page': True, 'local': local, 'sub': sub, 'employee': employee,
        'employees': employees, 'all_employees': DirectorEmployee.objects.prefetch_related('subdivisions'),
        'subdivisions': Subdivision.objects.all(), 'employee_form': employee_form,
        'formset': formset, 'add_form': add_form, 'search': search,
    })


@require_http_methods(['GET', 'POST'])
def director_coeff_import(request):
    from .models import GlobalCoeffVersion
    from .services.director_coeff_import import import_director_coefficients
    values = request.POST if request.method == 'POST' else request.GET
    try:
        version_id = int(values.get('version', ''))
    except (ValueError, TypeError):
        from django.http import Http404
        raise Http404('Выберите версию коэффициентов.')
    version = get_object_or_404(GlobalCoeffVersion, pk=version_id)
    errors = []
    if request.method == 'POST':
        try:
            result = import_director_coefficients(request.FILES.get('file'), version.pk)
        except ValidationError as error:
            errors = error.messages
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Director coefficient import failed for version %s', version.pk)
            errors = ['Не удалось сохранить коэффициенты. Изменения отменены. Проверьте журнал сервера.']
        else:
            messages.success(request, f'Коэффициенты директоров загружены в «{version}». Директоров: {result["directors"]}; создано: {result["created"]}; обновлено: {result["updated"]}; без изменений: {result["unchanged"]}.')
            return redirect(reverse('global_coeff_admin_motivation') + f'?version={version.pk}')
    return render(request, 'admin_motivation/director_coeff_import.html', {'version': version, 'errors': errors})
