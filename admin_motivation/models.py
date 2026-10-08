import pandas as pd
from django.db import models, transaction
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


class VersionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        from django.core.exceptions import ValidationError
        if set(kwargs) - {'revision'}:
            raise ValidationError('Изменение версии выполняется через создание или утверждение с сохранением истории.')
        return super().update(**kwargs)

    def bulk_create(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('Создавайте версии через сервис создания черновика.')

    def delete(self):
        from django.core.exceptions import ValidationError
        raise ValidationError('История версий не удаляется.')


class GlobalCoeffVersion(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Черновик'
        APPROVED = 'approved', 'Утверждена'
        SUPERSEDED = 'superseded', 'Заменена'

    objects = VersionQuerySet.as_manager()
    effective_from = models.DateField('Дата начала действия')
    title = models.CharField('Название', max_length=255, blank=True)
    created_at = models.DateTimeField('Создана', auto_now_add=True)
    status = models.CharField('Статус', max_length=16, choices=Status.choices, default=Status.DRAFT)
    approved_at = models.DateTimeField('Утверждена', null=True, blank=True)
    replaced_by = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='replaced_versions')
    revision = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ['effective_from', 'pk']
        verbose_name = 'Версия коэффициентов мотивации'
        verbose_name_plural = 'Версии коэффициентов мотивации'
        constraints = [models.UniqueConstraint(fields=['effective_from'], condition=models.Q(status='approved'), name='unique_approved_motivation_start')]

    def __str__(self):
        return f'{self.title or "Версия"} с {self.effective_from:%d.%m.%Y}'

    @property
    def is_editable(self):
        latest = type(self).objects.exclude(status=self.Status.SUPERSEDED).order_by('-pk').first()
        return self.status == self.Status.DRAFT and latest is not None and latest.pk == self.pk

    def clean(self):
        from datetime import date
        from django.core.exceptions import ValidationError
        super().clean()
        if self.effective_from is None or self.effective_from < date(2001, 1, 1):
            raise ValidationError({'effective_from': 'Дата версии не может быть раньше 01.01.2001.'})
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            self.revision = original.revision
            if original.effective_from != self.effective_from:
                raise ValidationError({'effective_from': 'Дата сохранённой версии не изменяется. Создайте копию с другой датой.'})
            if any(getattr(original, name) != getattr(self, name) for name in ['status', 'approved_at', 'replaced_by_id']):
                raise ValidationError('Для изменения статуса используйте утверждение версии.')
            if original.status != self.Status.DRAFT and original.title != self.title:
                raise ValidationError('Утверждённая или заменённая версия доступна только для просмотра.')
        else:
            if self.status != self.Status.DRAFT:
                raise ValidationError('Новая версия создаётся как черновик.')
            latest = type(self).objects.exclude(status=self.Status.SUPERSEDED).order_by('-pk').first()
            if not latest and self.effective_from != date(2001, 1, 1):
                raise ValidationError({'effective_from': 'Первая версия должна начинаться с 01.01.2001.'})
            if latest and self.effective_from <= latest.effective_from and not getattr(self, '_allow_overwrite', False):
                raise ValidationError({'effective_from': 'Новая версия должна начинаться позже последней версии либо подтвердите возможность замены.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('История версий не удаляется.')


def touch_version(version_id):
    GlobalCoeffVersion.objects.filter(pk=version_id).update(revision=models.F('revision') + 1)


class GlobalCoeffQuerySet(models.QuerySet):
    def check_editable(self):
        from django.core.exceptions import ValidationError
        latest = GlobalCoeffVersion.objects.select_for_update().exclude(status='superseded').order_by('-pk').first()
        if self.exists() and (not latest or latest.status != 'draft' or self.exclude(version=latest).exists()):
            raise ValidationError('Историческая версия доступна только для просмотра.')

    @transaction.atomic
    def update(self, **kwargs):
        from django.core.exceptions import ValidationError
        self.check_editable()
        if 'version' in kwargs or 'version_id' in kwargs:
            raise ValidationError('Нельзя переносить коэффициенты между версиями.')
        version_ids = list(self.values_list('version_id', flat=True).distinct())
        result = super().update(**kwargs)
        for version_id in version_ids:
            touch_version(version_id)
        return result

    @transaction.atomic
    def delete(self):
        self.check_editable()
        version_ids = list(self.values_list('version_id', flat=True).distinct())
        result = super().delete()
        for version_id in version_ids:
            touch_version(version_id)
        return result

    @transaction.atomic
    def bulk_create(self, objs, *args, **kwargs):
        from django.core.exceptions import ValidationError
        objs = list(objs)
        latest = GlobalCoeffVersion.objects.select_for_update().exclude(status='superseded').order_by('-pk').first()
        if any(not latest or latest.status != 'draft' or obj.version_id != latest.pk for obj in objs):
            raise ValidationError('Нельзя добавлять строки в историческую версию.')
        result = super().bulk_create(objs, *args, **kwargs)
        if objs:
            touch_version(latest.pk)
        return result


class GlobalCoeff(models.Model):
    objects = GlobalCoeffQuerySet.as_manager()
    id = models.AutoField(primary_key=True)
    version = models.ForeignKey(GlobalCoeffVersion, on_delete=models.PROTECT, related_name='coefficients')
    goods = models.ForeignKey(Goods, on_delete=models.PROTECT)
    type_coeff = models.ForeignKey(TypeCoeff, on_delete=models.PROTECT)
    segment = models.ForeignKey(SegmentCoeff, on_delete=models.PROTECT)
    motivation_coeff = models.FloatField()
    manager_coeff = models.FloatField()
    variation_calculate = models.ForeignKey(VariationCalculate, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['version', 'goods', 'type_coeff', 'segment'],
                                               name='unique_global_coeff_version_cell')]

    def __str__(self):
        return f'{self.version}: {self.goods} - {self.segment} = {self.type_coeff}'

    def check_editable(self):
        from django.core.exceptions import ValidationError
        latest = GlobalCoeffVersion.objects.select_for_update().exclude(status='superseded').order_by('-pk').first()
        if not latest or latest.status != 'draft' or self.version_id != latest.pk:
            raise ValidationError('Историческая версия доступна только для просмотра.')
        if self.pk and type(self).objects.get(pk=self.pk).version_id != self.version_id:
            raise ValidationError('Нельзя переносить коэффициент между версиями.')

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.check_editable()
        result = super().save(*args, **kwargs)
        touch_version(self.version_id)
        return result

    @transaction.atomic
    def delete(self, *args, **kwargs):
        self.check_editable()
        result = super().delete(*args, **kwargs)
        touch_version(self.version_id)
        return result

    @classmethod
    def get_matrix_str(cls, version, subdivision=None):
        queryset = cls.objects.filter(version=version)
        if subdivision is not None:
            queryset = queryset.filter(subdivision=subdivision)
        rows = list(queryset.select_related('goods', 'type_coeff', 'segment', 'variation_calculate'))
        types = list(TypeCoeff.objects.order_by('pk'))
        segments = list(SegmentCoeff.objects.order_by('pk'))
        cells = {(row.goods_id, row.type_coeff_id, row.segment_id): row for row in rows}
        goods = sorted({row.goods for row in rows}, key=lambda g: (g.planning_group_sales, g.group, g.brand, g.pk))
        matrix = {}
        for good in goods:
            matrix.setdefault(good.planning_group_sales, {}).setdefault(good.group, {})[good] = [
                [cells.get((good.pk, kind.pk, segment.pk)) for segment in segments] for kind in types]
        return matrix


class SubdivisionCoeff(models.Model):
    objects = GlobalCoeffQuerySet.as_manager()
    id = models.AutoField(primary_key=True)
    version = models.ForeignKey(GlobalCoeffVersion, on_delete=models.PROTECT, related_name='subdivision_coefficients')
    subdivision = models.ForeignKey(Subdivision, on_delete=models.PROTECT)
    goods = models.ForeignKey(Goods, on_delete=models.PROTECT)
    type_coeff = models.ForeignKey(TypeCoeff, on_delete=models.PROTECT)
    segment = models.ForeignKey(SegmentCoeff, on_delete=models.PROTECT)
    motivation_coeff = models.FloatField()
    manager_coeff = models.FloatField()
    variation_calculate = models.ForeignKey(VariationCalculate, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['version', 'subdivision', 'goods', 'type_coeff', 'segment'],
                                               name='unique_sub_coeff_version_cell')]

    check_editable = GlobalCoeff.check_editable
    @transaction.atomic
    def save(self, *args, **kwargs):
        self.check_editable()
        result = super().save(*args, **kwargs)
        touch_version(self.version_id)
        return result

    @transaction.atomic
    def delete(self, *args, **kwargs):
        self.check_editable()
        result = super().delete(*args, **kwargs)
        touch_version(self.version_id)
        return result

    @classmethod
    def get_matrix_str(cls, subdivision, version):
        return GlobalCoeff.get_matrix_str.__func__(cls, version, subdivision)


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
    objects = GlobalCoeffQuerySet.as_manager()
    id = models.AutoField(primary_key=True)
    version = models.ForeignKey(GlobalCoeffVersion, on_delete=models.PROTECT, related_name='manager_coefficients')
    subdivision = models.ForeignKey(Subdivision, on_delete=models.PROTECT)
    coeff = models.FloatField()
    kind = models.ForeignKey(KindManagerCoeff, on_delete=models.PROTECT, null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['version', 'subdivision', 'kind'], name='unique_manager_coeff_version_kind')]

    check_editable = GlobalCoeff.check_editable

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.check_editable()
        result = super().save(*args, **kwargs)
        touch_version(self.version_id)
        return result

    @transaction.atomic
    def delete(self, *args, **kwargs):
        self.check_editable()
        result = super().delete(*args, **kwargs)
        touch_version(self.version_id)
        return result


class ExampleFiles(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    file = models.FileField(null=True, upload_to='excel')

class SalesPlanScenario(models.Model):
    title = models.CharField('Название сценария', max_length=255)
    source_version = models.CharField('Версия плана в MS SQL', max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    loaded_at = models.DateTimeField(null=True, blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-pk']

    def __str__(self):
        return self.title


class SalesPlanLine(models.Model):
    scenario = models.ForeignKey(SalesPlanScenario, on_delete=models.CASCADE, related_name='lines')
    plan_date = models.DateField('Дата плана')
    subdivision = models.CharField(max_length=255)
    planning_group_sales = models.CharField(max_length=255)
    group = models.CharField(max_length=255)
    brand = models.CharField(max_length=255)
    amount_usd = models.DecimalField(max_digits=24, decimal_places=6)
    segment_amounts = models.JSONField(default=dict)
    coefficient_version = models.ForeignKey(GlobalCoeffVersion, on_delete=models.PROTECT, null=True, blank=True)
    calculation = models.JSONField(default=dict)
    policies_usd = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    sales_usd = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    total_usd = models.DecimalField(max_digits=24, decimal_places=6, null=True)

    class Meta:
        ordering = ['plan_date', 'subdivision', 'planning_group_sales', 'group', 'brand']
        constraints = [models.UniqueConstraint(fields=['scenario', 'plan_date', 'subdivision', 'planning_group_sales', 'group', 'brand'], name='unique_motivation_sales_plan_line')]


class ApprovalQuerySet(models.QuerySet):
    def update(self, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('История утверждений доступна только для просмотра.')

    def delete(self):
        from django.core.exceptions import ValidationError
        raise ValidationError('История утверждений не удаляется.')


class MotivationApproval(models.Model):
    objects = ApprovalQuerySet.as_manager()
    version = models.OneToOneField(GlobalCoeffVersion, on_delete=models.PROTECT, related_name='approval')
    created_at = models.DateTimeField(auto_now_add=True)
    plan = models.ForeignKey(SalesPlanScenario, on_delete=models.PROTECT, null=True, blank=True)
    baseline = models.ForeignKey(GlobalCoeffVersion, on_delete=models.PROTECT, null=True, blank=True, related_name='baseline_approvals')
    replaced_ids = models.JSONField(default=list)
    summary = models.JSONField(default=dict)

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if not self._state.adding:
            raise ValidationError('История утверждений доступна только для просмотра.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('История утверждений не удаляется.')
