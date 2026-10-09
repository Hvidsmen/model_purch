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
    return render(request, 'admin_motivation/gloabal_coeff.html', {
        **context, 'goods': GlobalCoeff.get_matrix_str(selected) if selected else {},
        'segments': SegmentCoeff.objects.order_by('pk'), 'types_coeffs': TypeCoeff.objects.order_by('pk'),
        'var_calcs': VariationCalculate.objects.order_by('pk'),
        'gp_sales': PlanningGroupSales.objects.all(), 'groups': GroupGoods.objects.all(), 'brands': Brand.objects.all(),
        'subdivisions': Subdivision.objects.all(), 'templates_file': example.file if example else None,
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


@require_POST
def gloabal_coeff_action(request):
    action = request.POST.get('action_button')
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
        route = reverse('coeff_subdivisions_admin_motivation', args=[destination.pk]) if destination else reverse('global_coeff_admin_motivation')
        return redirect(route + (f'?version={target.pk}' if target else ''))
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
                route = reverse('coeff_subdivisions_admin_motivation', args=[destination.pk]) if destination else reverse('global_coeff_admin_motivation')
                return redirect(route + f'?version={version.pk}')
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
            elif action in {'save_coeff', 'apply_sub'}:
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
                # Keep Goods and previous versions' coefficients intact.
                if action == 'apply_sub':
                    subdivisions = Subdivision.objects.filter(pk__in=request.POST.getlist('selected_subdivisions'))
                    if not subdivisions.exists():
                        raise ValidationError('Выберите подразделения.')
                    apply_to_subdivisions(version, subdivisions)
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


def coef_one_sub(request, subdivision, version_form=None):
    sub = get_object_or_404(Subdivision, pk=subdivision)
    context = version_context(request, version_form)
    selected = context['selected_version']
    kinds = list(KindManagerCoeff.objects.order_by('pk'))
    existing = {row.kind_id: row for row in SubdivisionManagerCoeff.objects.filter(version=selected, subdivision=sub).select_related('kind')}
    managers = [existing.get(kind.pk) or SubdivisionManagerCoeff(version=selected, subdivision=sub, kind=kind, coeff=1) for kind in kinds]
    example = ExampleFiles.objects.filter(pk=1).first()
    return render(request, 'admin_motivation/coef_one_sub.html', {
        **context, 'sub': sub, 'sub_coef_meneger': managers, 'kinds_mc': kinds,
        'goods': SubdivisionCoeff.get_matrix_str(sub, selected),
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
                for coeff in SubdivisionCoeff.objects.filter(version=version, subdivision=sub):
                    key = f'sub_coeff={coeff.pk}'
                    if key in request.POST:
                        coeff.motivation_coeff = coefficient_number(request.POST[key])
                        select = f'select_var_calc={coeff.goods_id}'
                        if select in request.POST:
                            coeff.variation_calculate = get_object_or_404(VariationCalculate, pk=request.POST[select])
                        coeff.manager_coeff = 1
                        coeff.save()
            elif action == 'copy_from_global':
                apply_to_subdivisions(version, [sub])
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
    return redirect(reverse('coeff_subdivisions_admin_motivation', args=[sub.pk]) + f'?version={version.pk}')


from .conns import *


def loader_motive(request):
    from datetime import date
    from .services.sql_export import export_motivation
    subdivisions = Subdivision.objects.all()
    if request.method == 'POST':
        try:
            year, quarter = int(request.POST.get('year', '')), int(request.POST.get('quarter', ''))
            if not 2020 <= year <= 2099 or not 1 <= quarter <= 4:
                raise ValueError('Неверный период')
            start_date = date(year, 3 * quarter - 2, 1)
            versions = export_motivation(start_date, connect_database)
        except (ValidationError, ValueError) as error:
            messages.error(request, ' '.join(error.messages) if isinstance(error, ValidationError) else 'Выберите год от 2020 до 2099 и квартал от 1 до 4.')
        except Exception as error:
            messages.error(request, f'Не удалось выгрузить настройки в DataWH: {error}')
        else:
            return render(request, 'admin_motivation/loader_complete.html', {'subdivisions': subdivisions, 'exported_versions': versions})
    return render(request, 'admin_motivation/loader.html', {'subdivisions': subdivisions})
