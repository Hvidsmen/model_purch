import pandas as pd
from django.db import models
from django.utils.translation.template import dot_re
from sqlalchemy import ForeignKey

from .conns import *


# Create your models here.


class Chanel(models.Model):
    id = models.AutoField(primary_key=True)
    chanel_name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.chanel_name


class Subdivision(models.Model):
    id = models.AutoField(primary_key=True)
    subdivision_key = models.CharField(max_length=255, unique=True)
    subdivision_name = models.CharField(max_length=255)
    subdivision_global = models.CharField(max_length=255)
    chanel = models.ForeignKey(Chanel, on_delete=models.CASCADE)

    def __str__(self):
        return self.subdivision_key

    @classmethod
    def get_or_create(cls):
        engine = create_engine('vm-dwh', 'DataWH')
        df = pd.read_sql("""
                    SELECT DISTINCT
                        SubdivisionName
                        ,SubdivisionName
                        ,SubdivisionName
                        ,Chanel
                    from 
                        DataWH.dbo.Subdivisions
                    WHERE
                        Chanel = 'Дилерский'
                        AND SubdivisionName NOT IN( 'Прочее','БУФЕР','Буфер дилерский канал','Дилерский канал')
                    """, con=engine)
        for i, row in df.iterrows():
            subdivision_key, subdivision_name, subdivision_global, chanel = row
            chanel = Chanel.objects.get(chanel_name=chanel)
            if len(cls.objects.filter(subdivision_key=subdivision_key)) == 0:
                no = cls.objects.create(subdivision_key=subdivision_key,subdivision_name=subdivision_name,subdivision_global=subdivision_global,chanel=chanel)
                no.save()
            else:
                no = cls.objects.get(subdivision_key=subdivision_key)
                no.subdivision_name=subdivision_name
                no.subdivision_global=subdivision_global
                no.chanel=chanel
                no.save()
        return cls.objects.all()


class Goods(models.Model):
    id = models.AutoField(primary_key=True)
    goods_key = models.CharField(max_length=255, unique=True)
    planning_group_sales = models.CharField(max_length=255)
    group = models.CharField(max_length=255)
    brand = models.CharField(max_length=255)

    def __str__(self):
        return self.goods_key

    @classmethod
    def get_matrix_str(cls):

        pg_sales = Goods.objects.values_list('planning_group_sales').distinct()
        pg_dict = {}
        for pg in pg_sales:
            pg = pg[0]
            pg_dict[pg] = {}
            groups = Goods.objects.filter(planning_group_sales=pg).values_list('group').distinct()
            gp_dict = {}
            for gr in groups:
                gr = gr[0]
                goods = Goods.objects.filter(planning_group_sales=pg, group=gr)
                gp_dict[gr] = goods
            pg_dict[pg] = gp_dict
        return pg_dict


class TypeCoeff(models.Model):
    id = models.AutoField(primary_key=True)
    type_coeff_name = models.CharField(max_length=255)

    def __str__(self):
        return self.type_coeff_name


class SegmentCoeff(models.Model):
    id = models.AutoField(primary_key=True)
    segment_name = models.CharField(max_length=255)

    def __str__(self):
        return self.segment_name


class VariationCalculate(models.Model):
    id = models.AutoField(primary_key=True)
    variation_name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.variation_name


class GlobalCoeff(models.Model):
    id = models.AutoField(primary_key=True)
    goods = models.ForeignKey(Goods, on_delete=models.CASCADE)
    type_coeff = models.ForeignKey(TypeCoeff, on_delete=models.CASCADE)
    segment = models.ForeignKey(SegmentCoeff, on_delete=models.CASCADE)
    motivation_coeff = models.FloatField()
    manager_coeff = models.FloatField()

    variation_calculate = models.ForeignKey(VariationCalculate, on_delete=models.CASCADE)

    def __str__(self):
        return f'{self.goods} - {self.segment} = {self.type_coeff}'

    @classmethod
    def get_matrix_str(cls):

        pg_sales = Goods.objects.values_list('planning_group_sales').distinct()
        pg_dict = {}
        for pg in pg_sales:
            pg = pg[0]
            pg_dict[pg] = {}
            groups = Goods.objects.filter(planning_group_sales=pg).values_list('group').distinct()
            gp_dict = {}
            for gr in groups:
                gr = gr[0]
                types_coeff = TypeCoeff.objects.all()
                segments = SegmentCoeff.objects.all()
                goods = [id for id in Goods.objects.filter(planning_group_sales=pg, group=gr)]

                good_dict = {}

                for good in goods:
                    good_dict[good] = []
                    for i_tp, tp in enumerate(types_coeff):
                        tp_list = []
                        for seg in segments:

                            try:
                                gb = GlobalCoeff.objects.get(goods=good, type_coeff=tp, segment=seg)
                            except:
                                gb = GlobalCoeff.objects.create(goods=good, type_coeff=tp, segment=seg,
                                                                motivation_coeff=0, manager_coeff=1,
                                                                variation_calculate=VariationCalculate.objects.get(
                                                                    id=1))
                                gb.save()
                            tp_list.append(gb)
                        good_dict[good].append(tp_list)

                gp_dict[gr] = good_dict
            pg_dict[pg] = gp_dict
        return pg_dict


class SubdivisionCoeff(models.Model):
    id = models.AutoField(primary_key=True)
    subdivision = models.ForeignKey(Subdivision, on_delete=models.CASCADE)
    goods = models.ForeignKey(Goods, on_delete=models.CASCADE)
    type_coeff = models.ForeignKey(TypeCoeff, on_delete=models.CASCADE)
    segment = models.ForeignKey(SegmentCoeff, on_delete=models.CASCADE)
    motivation_coeff = models.FloatField()
    manager_coeff = models.FloatField()

    variation_calculate = models.ForeignKey(VariationCalculate, on_delete=models.CASCADE)

    @classmethod
    def get_matrix_str(cls, subdivision):

        pg_sales = Goods.objects.values_list('planning_group_sales').distinct()
        pg_dict = {}
        for pg in pg_sales:
            pg = pg[0]
            pg_dict[pg] = {}
            groups = Goods.objects.filter(planning_group_sales=pg).values_list('group').distinct()
            gp_dict = {}
            for gr in groups:
                gr = gr[0]
                types_coeff = TypeCoeff.objects.all()
                segments = SegmentCoeff.objects.all()
                goods = [id for id in Goods.objects.filter(planning_group_sales=pg, group=gr)]

                good_dict = {}

                for good in goods:
                    good_dict[good] = []
                    for i_tp, tp in enumerate(types_coeff):
                        tp_list = []
                        for seg in segments:

                            try:
                                gb = SubdivisionCoeff.objects.get(goods=good, type_coeff=tp, segment=seg,
                                                                  subdivision=subdivision)
                            except:
                                gb = SubdivisionCoeff.objects.create(goods=good, type_coeff=tp, segment=seg,
                                                                motivation_coeff=0, manager_coeff=1,
                                                                variation_calculate=VariationCalculate.objects.get(
                                                                    id=1,subdivision=subdivision))
                            tp_list.append(gb)
                        good_dict[good].append(tp_list)

                gp_dict[gr] = good_dict
            pg_dict[pg] = gp_dict
        return pg_dict


class PlanningGroupSales(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name

    @classmethod
    def create_from_dwh(cls):
        engine = create_engine('vm-dwh', 'DataWH')
        df = pd.read_sql("""
        SELECT DISTINCT
            PlanningGroupSalesErp
        FROM 
            DataWH.dbo.ModelCodeERP
        where 
            PlanningGroupSalesErp != ''
        """, con=engine)
        for i, row in df.iterrows():
            name = row[0]
            if len(cls.objects.filter(name=name)) == 0:
                no = cls.objects.create(name=name)
                no.save()
        return cls.objects.all()


class GroupGoods(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name

    @classmethod
    def create_from_dwh(cls):
        engine = create_engine('vm-dwh', 'DataWH')
        df = pd.read_sql("""
            SELECT DISTINCT
                GroupERP
            FROM 
                DataWH.dbo.ModelCodeERP
            where 
                GroupERP != ''
            """, con=engine)
        for i, row in df.iterrows():
            name = row[0]
            if len(cls.objects.filter(name=name)) == 0:
                no = cls.objects.create(name=name)
                no.save()
        return cls.objects.all()


class Brand(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255, unique=True)

    def __str__(self):
        return self.name

    @classmethod
    def create_from_dwh(cls):
        engine = create_engine('vm-dwh', 'DataWH')
        df = pd.read_sql("""
                SELECT DISTINCT
                    BrandName
                FROM 
                    DataWH.dbo.ModelCodeERP
                where 
                    BrandName != ''
                """, con=engine)
        for i, row in df.iterrows():
            name = row[0]
            if len(cls.objects.filter(name=name)) == 0:
                no = cls.objects.create(name=name)
                no.save()

        return cls.objects.all()


class YearMotivation(models.Model):
    id = models.AutoField(primary_key=True)
    year = models.IntegerField()


class QuarterApplyCoeff(models.Model):
    id = models.AutoField(primary_key=True)
    quarter = models.IntegerField()


class KindManagerCoeff(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)


class SubdivisionManagerCoeff(models.Model):
    id = models.AutoField(primary_key=True)
    subdivision = models.ForeignKey(Subdivision, on_delete=models.CASCADE)
    coeff = models.FloatField()
    kind = models.ForeignKey(KindManagerCoeff, on_delete=models.CASCADE, null=True)

    @classmethod
    def get_or_create(cls, sub, kind_list):
        for kind in kind_list:
            if len(cls.objects.filter(subdivision=sub, kind=kind)) == 0:
                no = cls.objects.create(subdivision=sub, kind=kind, coeff=1)
                no.save()
        return cls.objects.filter(subdivision=sub)


class ExampleFiles(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    file = models.FileField(null=True, upload_to='excel')