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


def gloabal_coeff(request):
    templates_file = ExampleFiles.objects.get(id=1).file
    gp_sales = PlanningGroupSales.create_from_dwh()
    groups = GroupGoods.create_from_dwh()
    brands = Brand.create_from_dwh()

    goods = GlobalCoeff.get_matrix_str()
    segemnts = SegmentCoeff.objects.all()
    types_coeffs = TypeCoeff.objects.all()
    var_calcs = VariationCalculate.objects.all()

    subdivisions = Subdivision.get_or_create()

    return render(request, "admin_motivation/gloabal_coeff.html",
                  {
                      'goods': goods
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


import pandas as pd


def gloabal_coeff_action(request):
    if request.method == 'POST':
        if request.POST['action_button'] == 'fill_coeff':
            goods = Goods.objects.all()
            segments = SegmentCoeff.objects.all()
            type_coeffs = TypeCoeff.objects.all()
            var_calcs = [VariationCalculate.objects.all()[2]]
            for good in goods:
                for seg in segments:
                    for tp in type_coeffs:
                        for vc in var_calcs:
                            if len(GlobalCoeff.objects.filter(goods=good, segment=seg, type_coeff=tp,
                                                              variation_calculate=vc)) == 0:
                                gb = GlobalCoeff.objects.create(
                                    goods=good
                                    , type_coeff=tp
                                    , segment=seg
                                    , motivation_coeff=0.001
                                    , manager_coeff=1
                                    , variation_calculate=vc
                                )
                                gb.save()
        if request.POST['action_button'] == 'file_load' and 'myfile' in request.FILES.keys():
            myfile = request.FILES['myfile']
            fs = FileSystemStorage()
            filename = fs.save(myfile.name, myfile)
            df = pd.read_excel('excel/' + filename, skiprows=1)
            for i, row in df.iterrows():
                _, gp_sales, gp, brand, k0, k1, k2, k3, k4, s0, s1, s2, s3, s4 = row
                print(gp_sales, ' ' ,gp, ' ', brand)


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
                        glb = GlobalCoeff.objects.get(goods=good, segment=seg, type_coeff=type_coeff, )
                        glb.motivation_coeff = val
                        glb.manager_coeff = 1
                        glb.save()
                    except:
                        glb = GlobalCoeff.objects.create(goods=good, segment=seg, type_coeff=type_coeff,
                                                         variation_calculate=var_calcs, motivation_coeff=val,
                                                         manager_coeff=1)
                        glb.save()

                type_coeff = TypeCoeff.objects.get(type_coeff_name='Продажи')
                var_calcs = VariationCalculate.objects.all()[2]
                list_policy = [s0, s1, s2, s3, s4]

                for seg, val in zip(segments, list_policy):


                    try:
                        glb = GlobalCoeff.objects.get(goods=good, segment=seg, type_coeff=type_coeff)
                        glb.motivation_coeff = val
                        glb.manager_coeff = 1
                        glb.save()
                    except:
                        glb = GlobalCoeff.objects.create(goods=good, segment=seg, type_coeff=type_coeff,
                                                         variation_calculate=var_calcs, motivation_coeff=val,
                                                         manager_coeff=1)
                        glb.save()
        if request.POST['action_button'] == 'add_product':
            gp_sales = request.POST['pg_sales']
            gp = request.POST['group']
            br = request.POST['brand']
            k0 = request.POST['k0']
            k1 = request.POST['k1']
            k2 = request.POST['k2']
            k3 = request.POST['k3']
            k4 = request.POST['k4']
            s0 = request.POST['s0']
            s1 = request.POST['s1']
            s2 = request.POST['s2']
            s3 = request.POST['s3']
            s4 = request.POST['s4']
            vc_name = request.POST['select_var_calc']
            try:
                good = Goods.objects.get(planning_group_sales=gp_sales, group=gp, brand=br)
            except:
                good = Goods.objects.create(planning_group_sales=gp_sales, group=gp, brand=br,
                                            goods_key=gp_sales + gp + br)
            segments = SegmentCoeff.objects.all()
            type_coeff = TypeCoeff.objects.get(type_coeff_name='Политики')
            var_calcs = VariationCalculate.objects.get(id=vc_name)
            list_policy = [k0, k1, k2, k3, k4]

            for seg, val in zip(segments, list_policy):
                try:
                    glb = GlobalCoeff.objects.get(goods=good, segment=seg, type_coeff=type_coeff,
                                                  variation_calculate=var_calcs)
                    glb.motivation_coeff = val
                    glb.manager_coeff = 1
                    glb.save
                except:
                    glb = GlobalCoeff.objects.create(goods=good, segment=seg, type_coeff=type_coeff,
                                                     variation_calculate=var_calcs, motivation_coeff=val,
                                                     manager_coeff=1)
                    glb.save()

            type_coeff = TypeCoeff.objects.get(type_coeff_name='Продажи')
            list_policy = [s0, s1, s2, s3, s4]
            for seg, val in zip(segments, list_policy):
                try:
                    glb = GlobalCoeff.objects.get(goods=good, segment=seg, type_coeff=type_coeff,
                                                  variation_calculate=var_calcs)
                    glb.motivation_coeff = val
                    glb.manager_coeff = 1
                    glb.save
                except:
                    glb = GlobalCoeff.objects.create(goods=good, segment=seg, type_coeff=type_coeff,
                                                     variation_calculate=var_calcs, motivation_coeff=val,
                                                     manager_coeff=1)
                    glb.save()
        if request.POST['action_button'] == 'save_coeff' or request.POST['action_button'] == 'apply_sub':
            #         Применяем  уровень мотивации
            keys = request.POST.keys()
            select_var_calc_ids = [key.split('=')[1] for key in
                                   keys if key.find('select_var_calc') != -1]

            goods = Goods.objects.filter(id__in=select_var_calc_ids)
            gbcfs = GlobalCoeff.objects.filter(goods__in=goods)
            for gbc in gbcfs:
                gbc.manager_coeff = 1
                if f'global_coeff={gbc.id}' not in request.POST.keys():
                    gbc.delete()
                else:
                    gbc.variation_calculate = VariationCalculate.objects.get(
                        id=request.POST[f'select_var_calc={gbc.goods.id}'])
                    gbc.motivation_coeff = float(request.POST[f'global_coeff={gbc.id}'].replace('%', '')) / 100.0

                    gbc.save()

            delete_coeffs = [key.split('=')[1] for key in
                             keys if key.find('delete_coeff') != -1]

            goods = Goods.objects.filter(id__in=delete_coeffs)
            gbcfs = GlobalCoeff.objects.filter(goods__in=goods)
            for gbc in gbcfs:
                gbc.delete()
            for good in goods:
                good.delete()
        if request.POST['action_button'] == 'apply_sub':
            sub_ids = request.POST.getlist('selected_subdivisions')
            subdivisions = Subdivision.objects.filter(id__in=sub_ids)
            smcs = KindManagerCoeff.objects.all()
            glovaL_coeffs = GlobalCoeff.objects.all()
            for sub in subdivisions:
                print(sub)
                for smc in smcs:
                    try:
                        gcs = SubdivisionManagerCoeff.objects.get(
                            subdivision=sub
                            , kind=smc

                        )


                        gcs.save()
                    except:
                        gcs = SubdivisionManagerCoeff.objects.create(
                            subdivision=sub
                            , kind=smc
                            , coeff=1
                        )

                        gcs.save()
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
    return gloabal_coeff(request)


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

        glovaL_coeffs = GlobalCoeff.objects.all()
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
