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


def gloabal_coeff(request, version_form=None):
    latest = latest_version()
    selected = get_object_or_404(GlobalCoeffVersion, pk=request.GET['version']) if request.GET.get('version') else latest
    versions = list(GlobalCoeffVersion.objects.order_by('-effective_from'))
    for i, version in enumerate(versions):
        version.effective_until = versions[i - 1].effective_from - timedelta(days=1) if i else None
        version.coefficient_count = version.coefficients.count()
    example = ExampleFiles.objects.filter(pk=1).first()
    minimum = latest.effective_from + timedelta(days=1) if latest else None
    form = version_form or GlobalCoeffVersionForm(initial={'source_version': latest.pk if latest else None,
         'effective_from': max(today(), minimum) if minimum else '2001-01-01'})
    if minimum:
        form.fields['effective_from'].widget.attrs['min'] = minimum.isoformat()
    return render(request, 'admin_motivation/gloabal_coeff.html', {
        'goods': GlobalCoeff.get_matrix_str(selected) if selected else {},
        'segments': SegmentCoeff.objects.order_by('pk'), 'types_coeffs': TypeCoeff.objects.order_by('pk'),
        'var_calcs': VariationCalculate.objects.order_by('pk'),
        'gp_sales': PlanningGroupSales.objects.all(), 'groups': GroupGoods.objects.all(), 'brands': Brand.objects.all(),
        'subdivisions': Subdivision.objects.all(), 'templates_file': example.file if example else None,
        'versions': versions, 'selected_version': selected, 'latest_version': latest,
        'effective_version': effective_version(), 'version_form': form,
        'version_editable': bool(selected and latest and selected.pk == latest.pk),
    })


def coefficient_number(value):
    text = str(value).strip().replace(',', '.')
    percent = text.endswith('%')
    number = float(text.rstrip('%')) / (100 if percent else 1)
    if not math.isfinite(number) or not -1 <= number <= 1:
        raise ValidationError('Коэффициенты должны быть конечными числами от -1 до 1 (от -100% до 100%).')
    return number


def upsert_product(version, group_sales, group, brand, variation_id, policies, sales):
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
            GlobalCoeff.objects.update_or_create(version=version, goods=good, type_coeff=kind, segment=segment,
                defaults={'variation_calculate': variation, 'motivation_coeff': coefficient_number(value), 'manager_coeff': 1})


@require_POST
def gloabal_coeff_action(request):
    action = request.POST.get('action_button')
    if action == 'create_version':
        form = GlobalCoeffVersionForm(request.POST)
        if form.is_valid():
            try:
                version = create_version(**form.cleaned_data)
            except ValidationError as error:
                for message in error.messages:
                    form.add_error('effective_from', message)
            else:
                messages.success(request, f'Создана версия с {version.effective_from:%d.%m.%Y}. Коэффициенты скопированы.')
                return redirect(reverse('global_coeff_admin_motivation') + f'?version={version.pk}')
        return gloabal_coeff(request, version_form=form)
    version = get_object_or_404(GlobalCoeffVersion, pk=request.POST.get('version'))
    try:
        with transaction.atomic():
            latest = GlobalCoeffVersion.objects.select_for_update().order_by('-effective_from').first()
            if version.pk != latest.pk:
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
                if action == 'apply_sub' and version.effective_from > today():
                    raise ValidationError('Версия ещё не вступила в действие. Её нельзя применять к подразделениям.')
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


def coef_one_sub(request, subdivision):
    sub = Subdivision.objects.get(id=subdivision)
    kinds_mc = KindManagerCoeff.objects.all()
    sub_coef_meneger = SubdivisionManagerCoeff.get_or_create(sub, kinds_mc)

    gp_sales = PlanningGroupSales.create_from_dwh()
    groups = GroupGoods.create_from_dwh()
    brands = Brand.create_from_dwh()
    #
    goods = SubdivisionCoeff.get_matrix_str(subdivision)
    segemnts = SegmentCoeff.objects.all()
    types_coeffs = TypeCoeff.objects.all()
    var_calcs = VariationCalculate.objects.all()
    #
    subdivisions = Subdivision.get_or_create()
    templates_file = ExampleFiles.objects.get(id=1).file
    return render(request, "admin_motivation/coef_one_sub.html",
                  {
                      'sub': sub
                      , 'sub_coef_meneger': sub_coef_meneger
                      , 'kinds_mc': kinds_mc
                      , 'goods': goods
                      , 'segments': segemnts
                      , 'types_coeffs': types_coeffs
                      , 'var_calcs': var_calcs

                      , 'gp_sales': gp_sales
                      , 'groups': groups
                      , 'brands': brands
                      , 'subdivisions': subdivisions
                      ,'templates_file':templates_file
                  }
                  )


def sub_act(request, subdivision):
    print(request.POST)
    sub = Subdivision.objects.get(id=subdivision)
    if request.POST['action_button'] == 'update_motive_coeff':
        kinds_id = [key.split('=')[1] for key in request.POST.keys() if key.find('kind_coeff') != -1]
        kinds = KindManagerCoeff.objects.filter(id__in=kinds_id)
        for kind in kinds:
            if len(SubdivisionManagerCoeff.objects.filter(subdivision=sub, kind=kind)) == 0:
                no = SubdivisionManagerCoeff.objects.create(subdivision=sub, kind=kind, coeff=float(
                    request.POST[f'kind_coeff={kind.id}'].replace('%', ''))) / 100.0
                no.save()
            else:
                no = SubdivisionManagerCoeff.objects.get(subdivision=sub, kind=kind)
                no.coeff = float(request.POST[f'kind_coeff={kind.id}'].replace('%', '')) / 100.0
                no.save()
    if request.POST['action_button'] == 'file_load' and 'myfile' in request.FILES.keys():
        myfile = request.FILES['myfile']
        fs = FileSystemStorage()
        filename = fs.save(myfile.name, myfile)
        df = pd.read_excel('excel/' + filename, skiprows=1)
        for i, row in df.iterrows():
            _, gp_sales, gp, brand, k0, k1, k2, k3, k4, s0, s1, s2, s3, s4 = row
            if len(PlanningGroupSales.objects.filter(name=gp_sales)) == 0 or len(
                    GroupGoods.objects.filter(name=gp)) == 0 or len(Brand.objects.filter(name=gp)) == 0:
                continue

            try:
                good = Goods.objects.get(planning_group_sales=gp_sales, group=gp, brand=brand)
            except:
                good = Goods.objects.create(planning_group_sales=gp_sales, group=gp, brand=brand,
                                            goods_key=gp_sales + gp + brand)

            #             policy
            segments = SegmentCoeff.objects.all()
            type_coeff = TypeCoeff.objects.get(type_coeff_name='Политики')
            var_calcs = VariationCalculate.objects.all()[2]
            list_policy = [k0, k1, k2, k3, k4]
            for seg, val in zip(segments, list_policy):
                try:
                    glb = SubdivisionCoeff.objects.get(goods=good, segment=seg, type_coeff=type_coeff, subdivision=sub)
                    glb.motivation_coeff = val
                    glb.manager_coeff = 1
                    glb.save()
                except:
                    glb = SubdivisionCoeff.objects.create(goods=good, segment=seg, type_coeff=type_coeff,
                                                          variation_calculate=var_calcs, motivation_coeff=val,
                                                          manager_coeff=1, subdivision=sub)
                    glb.save()

            type_coeff = TypeCoeff.objects.get(type_coeff_name='Продажи')
            var_calcs = VariationCalculate.objects.all()[2]
            list_policy = [s0, s1, s2, s3, s4]
            for seg, val in zip(segments, list_policy):
                try:
                    glb = SubdivisionCoeff.objects.get(goods=good, segment=seg, type_coeff=type_coeff, subdivision=sub)
                    glb.motivation_coeff = val
                    glb.manager_coeff = 1
                    glb.save()
                except:
                    glb = SubdivisionCoeff.objects.create(goods=good, segment=seg, type_coeff=type_coeff,
                                                          variation_calculate=var_calcs, motivation_coeff=val,
                                                          manager_coeff=1, subdivision=sub)
                    glb.save()
    if request.POST['action_button'] == 'save_coeff':
        #         Применяем  уровень мотивации
        keys = request.POST.keys()
        select_var_calc_ids = [key.split('=')[1] for key in
                               keys if key.find('select_var_calc') != -1]

        goods = Goods.objects.filter(id__in=select_var_calc_ids)
        gbcfs = SubdivisionCoeff.objects.filter(goods__in=goods, subdivision=sub)
        for gbc in gbcfs:
            gbc.manager_coeff = 1
            if f'sub_coeff={gbc.id}' not in request.POST.keys():
                gbc.delete()
            else:
                gbc.variation_calculate = VariationCalculate.objects.get(
                    id=request.POST[f'select_var_calc={gbc.goods.id}'])
                gbc.motivation_coeff = float(request.POST[f'sub_coeff={gbc.id}'].replace('%', '')) / 100.0

                gbc.save()
    if request.POST['action_button'] == 'copy_from_global':

        subdivisions = [sub]

        glovaL_coeffs = effective_coefficients()
        for sub in subdivisions:
            for gcb in glovaL_coeffs:
                try:
                    gcs = SubdivisionCoeff.objects.get(
                        subdivision=sub
                        , goods=gcb.goods
                        , type_coeff=gcb.type_coeff
                        , segment=gcb.segment

                    )
                    gcs.motivation_coeff = gcb.motivation_coeff
                    gcs.manager_coeff = gcb.manager_coeff
                    gcs.variation_calculate = gcb.variation_calculate
                    gcs.save()
                except:
                    gcs = SubdivisionCoeff.objects.create(
                        subdivision=sub
                        , goods=gcb.goods
                        , type_coeff=gcb.type_coeff
                        , segment=gcb.segment
                        , motivation_coeff=gcb.motivation_coeff
                        , manager_coeff=gcb.manager_coeff

                        , variation_calculate=gcb.variation_calculate
                    )

                    gcs.save()

    return coef_one_sub(request, subdivision)


from .conns import *


def loader_motive(request):
    subdivisions = Subdivision.get_or_create()
    if request.method == 'POST':
        conn, cursor = connect_database('vm-dwh', 'DataWH')
        print(request.POST)
        year = request.POST['year']
        quarter = int(request.POST['quarter'])
        if quarter == 1:
            date = f'01.01.{year}'
        elif quarter == 2:
            date = f'01.04.{year}'
        elif quarter == 3:
            date = f'01.07.{year}'
        else:
            date = f'01.10.{year}'
        print(date)
        sub_magager = SubdivisionManagerCoeff.objects.all()
        sql_delte = f"""
            DELETE FROM DataWH.motivation.SubdivisionManagerCoeff
            WHERE 
                Date_>='{date}'
        """
        conn.execute(sql_delte)
        cursor.commit()
        for sm in sub_magager:
            sql = f"""
            INSERT INTO DataWH.motivation.SubdivisionManagerCoeff(
            Date_ 
	        ,Subdivision 
	        ,Kind_		
	        ,Coeff_ 
            )
            VALUES('{date}','{sm.subdivision.subdivision_key}','{sm.kind.name}', {sm.coeff})
            """
            conn.execute(sql)
            cursor.commit()

        sub_coeff = SubdivisionCoeff.objects.all()

        sql_delte = f"""
                    DELETE FROM DataWH.motivation.SubdivisionMotiveCoeff
                    WHERE 
                        Date_>='{date}'
                """
        conn.execute(sql_delte)
        cursor.commit()
        for sm in sub_coeff:
            sql = f"""
                    INSERT INTO DataWH.motivation.SubdivisionMotiveCoeff(
                    Date_ 
	                ,Subdivision
	                ,GoodsKey
	                ,TypeCoeff
	                ,Segment
	                ,VariationCalculate
	                ,Coeff_ 
                    )
                    VALUES(
                    '{date}'
                    ,'{sm.subdivision.subdivision_key}'
                    ,'{sm.goods.goods_key}'
                    ,'{sm.type_coeff.type_coeff_name}'
                    , '{sm.segment.segment_name}'
                    , '{sm.variation_calculate.variation_name}'
                    ,{sm.motivation_coeff}
                    )
                    """
            conn.execute(sql)
            cursor.commit()

        goods = Goods.objects.all()

        sql_delte = f"""
                            DELETE FROM DataWH.motivation.GoodsMotivation
                            WHERE 
                                Date_>='{date}'
                        """
        conn.execute(sql_delte)
        cursor.commit()
        for sm in goods:
            sql = f"""
                            INSERT INTO DataWH.motivation.GoodsMotivation(
                            Date_
	                        ,GoodsKey 
	                        ,PGSales
	                        ,Group_
	                        ,Brand
                            )
                            VALUES(
                            '{date}'
                            ,'{sm.goods_key}'
                            ,'{sm.planning_group_sales}'
                            ,'{sm.group}'
                            , '{sm.brand}'
                           
                            )
                            """
            conn.execute(sql)
            cursor.commit()
        return render(request, "admin_motivation/loader_complete.html", {
            'subdivisions': subdivisions

        })

    return render(request, "admin_motivation/loader.html", {
        'subdivisions': subdivisions

    })
