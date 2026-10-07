from modulefinder import Module

from django.db import models

from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from .goods_identity import planning_group_key


class AlgorithmRun(models.Model):
    """Сессия выполнения алгоритма (чтобы можно было запускать несколько раз)"""
    id = models.AutoField(primary_key=True)
    started_at = models.DateTimeField(auto_now_add=True, verbose_name='Начало выполнения')
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name='Завершение')
    status = models.CharField(
        max_length=20,
        choices=[
            ('running', 'Выполняется'),
            ('completed', 'Завершено'),
            ('failed', 'Ошибка'),
            ('cancelled', 'Отменено')
        ],
        default='running',
        verbose_name='Статус'
    )

    def __str__(self):
        return f"Алгоритм #{self.id} ({self.started_at.strftime('%d.%m.%Y %H:%M')})"


class AlgorithmStep(models.Model):
    """Шаг алгоритма с фиксацией выполнения"""
    id = models.AutoField(primary_key=True)
    algorithm_run = models.ForeignKey(AlgorithmRun, on_delete=models.CASCADE, related_name='steps')
    order = models.IntegerField(verbose_name='Порядок выполнения')
    name = models.CharField(max_length=255, verbose_name='Название шага')
    description = models.TextField(blank=True, null=True, verbose_name='Описание')

    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Ожидает'),
            ('running', 'Выполняется'),
            ('completed', 'Завершено'),
            ('failed', 'Ошибка'),
            ('skipped', 'Пропущено')
        ],
        default='pending',
        verbose_name='Статус'
    )

    started_at = models.DateTimeField(null=True, blank=True, verbose_name='Начало выполнения')
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name='Завершение')
    duration_seconds = models.FloatField(null=True, blank=True, verbose_name='Длительность (сек)')

    result = models.TextField(blank=True, null=True, verbose_name='Результат')
    error_message = models.TextField(blank=True, null=True, verbose_name='Сообщение об ошибке')

    class Meta:
        ordering = ['order']
        verbose_name = 'Шаг алгоритма'
        verbose_name_plural = 'Шаги алгоритма'

    def __str__(self):
        return f"{self.order}. {self.name} ({self.get_status_display()})"


class ResultCalc(models.Model):
    id = models.AutoField(primary_key=True)
    name_step = models.CharField(max_length=255)
    value = models.IntegerField(default=0)


# Create your models here.


class ScenarioModel(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255, verbose_name='Название')
    date_start_plan = models.DateField(verbose_name='Дата начала')
    date_end_plan = models.DateField(verbose_name='Дата окончания')

    # НОВОЕ ПОЛЕ: флаг перезаписи
    overwrite_existing = models.BooleanField(
        default=False,
        verbose_name='Перезаписывать существующие данные',
        help_text='Если включено, при сохранении сценария существующие закупки и товары будут обновлены данными из MS SQL. Если выключено — будут добавлены только новые записи.'
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = 'Сценарий'
        verbose_name_plural = 'Сценарии'

class ScenarioPlanSales(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    scenario_model = models.ForeignKey(ScenarioModel, on_delete=models.CASCADE)
    flag_order_in_purch = models.BooleanField()



class KindPurch(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    def __str__(self):
        return self.name

class PGGoodsQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if 'planning_group' in kwargs:
            if not isinstance(kwargs['planning_group'], str):
                raise ValueError('Update planning_group with a string or use instance.save().')
            kwargs['planning_group_key'] = planning_group_key(kwargs['planning_group'])
        return super().update(**kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj.planning_group_key = planning_group_key(obj.planning_group)
        return super().bulk_create(objs, *args, **kwargs)

    def bulk_update(self, objs, fields, *args, **kwargs):
        if 'planning_group' in fields:
            raise ValueError('Use instance.save() to change planning_group.')
        return super().bulk_update(objs, fields, *args, **kwargs)


class PGGoods(models.Model):
    objects = PGGoodsQuerySet.as_manager()
    planning_group_key = models.CharField(max_length=64, editable=False, default='')
    id = models.AutoField(primary_key = True)
    planning_group = models.CharField(max_length=255)
    planning_sales = models.CharField(max_length=255)
    group_goods = models.CharField(max_length=255)
    brand = models.CharField(max_length=255, null=True)
    purch = models.CharField(max_length=255, null=True)

    kind_purch = models.ForeignKey(KindPurch,on_delete=models.CASCADE)

    volume = models.FloatField()
    exw_usd = models.FloatField()
    ddp_usd = models.FloatField()
    kddp = models.FloatField()

    stock_cnt_day = models.IntegerField()
    percent_stock_end = models.FloatField()

    scenario_plan = models.ForeignKey(ScenarioModel, on_delete=models.CASCADE, null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['scenario_plan', 'planning_group_key'], name='unique_pg_scenario_group'),
            models.UniqueConstraint(fields=['planning_group_key'], condition=models.Q(scenario_plan__isnull=True),
                                    name='unique_pg_unassigned_group'),
        ]

    def clean(self):
        super().clean()
        self.planning_group_key = planning_group_key(self.planning_group)
        duplicate = type(self).objects.filter(
            scenario_plan_id=self.scenario_plan_id, planning_group_key=self.planning_group_key,
        ).exclude(pk=self.pk).exists()
        if duplicate:
            raise ValidationError('В этом сценарии уже есть такая плановая группа (без учёта регистра).')

    def save(self, *args, **kwargs):
        self.planning_group_key = planning_group_key(self.planning_group)
        if kwargs.get('update_fields') is not None and 'planning_group' in kwargs['update_fields']:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'planning_group_key'}
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.planning_group

class PGGoodsDuplicateArchive(models.Model):
    original_id = models.PositiveIntegerField()
    scenario_id = models.PositiveIntegerField(null=True)
    kept_id = models.PositiveIntegerField()
    original_data = models.JSONField()
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Архив дублей PGGoods'
        verbose_name_plural = 'Архив дублей PGGoods'


class Purch(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    lag_income = models.IntegerField()
    scenario_plan = models.ForeignKey(ScenarioModel, on_delete=models.CASCADE, null=True)
    lage_make = models.IntegerField(default=0, null=True)
    @property
    def total_percent(self):
        """Сумма процентов всех платежей."""
        from django.db.models import Sum
        result = self.purchpay_set.aggregate(total=Sum('percent_pay'))
        return result['total'] or 0.0

    def __str__(self):
        return self.name

class KindLagPay(models.Model):
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name

class PurchPay(models.Model):
    id = models.AutoField(primary_key=True)
    purch = models.ForeignKey(Purch, on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    percent_pay = models.FloatField()
    lag_day_pay = models.IntegerField()

    kind_lag_pay = models.ForeignKey(KindLagPay, on_delete=models.CASCADE, null=True)

    def __str__(self):
        return f"{self.name} ({self.percent_pay}%)"
