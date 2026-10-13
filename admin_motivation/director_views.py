from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.forms import modelformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import (Subdivision, DirectorEmployee, GlobalDirectorCoeff,
                     SubdivisionDirectorCoeff, Goods)
from .director_forms import (DirectorEmployeeForm, GlobalDirectorForm, LocalDirectorForm,
                             GlobalDirectorAddForm, LocalDirectorAddForm)
from .services.directors import lock_editable, copy_directors
from .services.version_context import version_context


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
