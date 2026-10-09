from modulefinder import Module

from django.db import models

from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from .goods_identity import planning_group_key
from decimal import Decimal
from django.core.validators import MinValueValidator, MaxValueValidator


class AlgorithmRun(models.Model):
    """Сессия выполнения алгоритма (чтобы можно было запускать несколько раз)"""
    id = models.AutoField(primary_key=True)
    scenario = models.ForeignKey('ScenarioModel', null=True, blank=True, on_delete=models.SET_NULL, related_name='algorithm_runs')
    scenario_export = models.ForeignKey('ScenarioExport', null=True, blank=True, on_delete=models.SET_NULL)
    parameters = models.JSONField(default=dict, blank=True)
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

class GoodsGroup(models.Model):
    name = models.CharField('Группа товаров', max_length=255)
    name_key = models.CharField(max_length=64, unique=True, editable=False, default='')
    duty_rate = models.DecimalField('Пошлина, %', max_digits=5, decimal_places=2, default=0,
                                    validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))])

    class Meta:
        verbose_name = 'Группа товаров'
        verbose_name_plural = 'Группы товаров'
        ordering = ['name']
        constraints = [models.CheckConstraint(condition=models.Q(duty_rate__gte=0, duty_rate__lte=100), name='goods_group_duty_range')]

    def clean(self):
        super().clean()
        self.name = self.name.strip()
        self.name_key = planning_group_key(self.name)
        if not self.name:
            raise ValidationError({'name': 'Введите название группы.'})
        if type(self).objects.filter(name_key=self.name_key).exclude(pk=self.pk).exists():
            raise ValidationError({'name': 'Группа с таким названием уже существует.'})

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.name_key = planning_group_key(self.name)
        return super().save(*args, **kwargs)

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
    container_volume = models.FloatField('Объём контейнера, м³', default=65, validators=[MinValueValidator(0.001)])
    duty_rate = models.DecimalField('Пошлина, %', max_digits=5, decimal_places=2, default=0,
                                    validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))])
    exw_usd = models.FloatField()
    freight_usd = models.FloatField('Фрахт за товар', default=0, editable=False)
    cif_usd = models.FloatField('CIF', default=0, editable=False)
    customs_payment_usd = models.FloatField('Таможенный платёж', default=0, editable=False)
    warehouse_delivery_usd = models.FloatField('Доставка за товар', default=0, editable=False)
    ddp_usd = models.FloatField(default=0, editable=False)
    kddp = models.FloatField(default=1, editable=False)

    stock_cnt_day = models.IntegerField()
    percent_stock_end = models.FloatField()

    scenario_plan = models.ForeignKey(ScenarioModel, on_delete=models.CASCADE, null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(duty_rate__gte=0, duty_rate__lte=100), name='pggoods_duty_rate_range'),
            models.UniqueConstraint(fields=['scenario_plan', 'planning_group_key'], name='unique_pg_scenario_group'),
            models.UniqueConstraint(fields=['planning_group_key'], condition=models.Q(scenario_plan__isnull=True),
                                    name='unique_pg_unassigned_group'),
        ]

    def __init__(self, *args, **kwargs):
        self._duty_supplied = 'duty_rate' in kwargs or bool(args)
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        self.planning_group_key = planning_group_key(self.planning_group)
        if not (self.planning_group or '').strip():
            raise ValidationError('Плановая группа должна быть задана при загрузке товара.')
        from .services.pricing import number
        errors = {}
        for name, label, positive in [('volume', 'Объём', True), ('container_volume', 'Объём контейнера', True),
                                      ('exw_usd', 'EXW', False), ('stock_cnt_day', 'Запас в днях', False)]:
            try:
                number(getattr(self, name), label, positive=positive)
            except ValidationError as error:
                errors[name] = error.messages
        if errors:
            raise ValidationError(errors)
        duplicate = type(self).objects.filter(
            scenario_plan_id=self.scenario_plan_id, planning_group_key=self.planning_group_key,
        ).exclude(pk=self.pk).exists()
        if duplicate:
            raise ValidationError('В этом сценарии уже есть такая плановая группа (без учёта регистра).')

    def save(self, *args, **kwargs):
        self.planning_group_key = planning_group_key(self.planning_group)
        if kwargs.get('update_fields') is not None and 'planning_group' in kwargs['update_fields']:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'planning_group_key'}
        group_name = (self.group_goods or '').strip()
        if group_name:
            group, _ = GoodsGroup.objects.get_or_create(name_key=planning_group_key(group_name), defaults={'name': group_name})
            if self._state.adding and not self._duty_supplied:
                self.duty_rate = group.duty_rate
        from .services.pricing import calculate_instance, CALCULATED_FIELDS
        calculate_instance(self)
        if kwargs.get('update_fields') is not None:
            kwargs['update_fields'] = set(kwargs['update_fields']) | set(CALCULATED_FIELDS)
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


class PurchQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if 'name' in kwargs:
            if not isinstance(kwargs['name'], str):
                raise ValueError('Use instance.save() to change the purchase name.')
            kwargs['name'] = kwargs['name'].strip()
            kwargs['name_key'] = planning_group_key(kwargs['name'])
        return super().update(**kwargs)

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        for obj in objs:
            obj.name = obj.name.strip()
            obj.name_key = planning_group_key(obj.name)
        return super().bulk_create(objs, *args, **kwargs)

    def bulk_update(self, objs, fields, *args, **kwargs):
        if 'name' in fields:
            raise ValueError('Use instance.save() to change the purchase name.')
        return super().bulk_update(objs, fields, *args, **kwargs)


class Purch(models.Model):
    objects = PurchQuerySet.as_manager()
    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=255)
    lag_income = models.IntegerField()
    name_key = models.CharField(max_length=64, editable=False, unique=True, default='')
    lage_make = models.IntegerField(default=0, null=True)
    is_russian = models.BooleanField('Поставщик РФ', default=False)

    def clean(self):
        super().clean()
        self.name = self.name.strip()
        self.name_key = planning_group_key(self.name)
        if not self.name:
            raise ValidationError({'name': 'Введите название закупки.'})
        if type(self).objects.filter(name_key=self.name_key).exclude(pk=self.pk).exists():
            raise ValidationError({'name': 'Закупка с таким названием уже существует.'})

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.name_key = planning_group_key(self.name)
        if kwargs.get('update_fields') is not None and 'name' in kwargs['update_fields']:
            kwargs['update_fields'] = set(kwargs['update_fields']) | {'name_key'}
        return super().save(*args, **kwargs)

    @property
    def total_percent(self):
        """Сумма процентов всех платежей."""
        from django.db.models import Sum
        result = self.purchpay_set.aggregate(total=Sum('percent_pay'))
        return result['total'] or 0.0

    def __str__(self):
        return self.name

class PurchScenarioArchive(models.Model):
    """Original scenario-specific settings retained when creating the shared directory."""
    original_id = models.PositiveIntegerField()
    kept_id = models.PositiveIntegerField()
    original_data = models.JSONField()
    payments = models.JSONField()
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Архив сценарных закупок'
        verbose_name_plural = 'Архив сценарных закупок'


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


class Freight(models.Model):
    scenario = models.OneToOneField(ScenarioModel, on_delete=models.CASCADE, related_name='freight', verbose_name='Сценарий')
    price_per_container = models.DecimalField('Цена за контейнер', max_digits=18, decimal_places=2,
                                              validators=[MinValueValidator(Decimal('0'))])
    customs_rate = models.DecimalField('Таможенная ставка, %', max_digits=5, decimal_places=2, default=0,
                                       validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))])
    warehouse_delivery_cost = models.DecimalField('Стоимость доставки до склада', max_digits=18, decimal_places=2,
                                                 default=0, validators=[MinValueValidator(Decimal('0'))])
    foreign_delivery_cost = models.DecimalField('Заграничная доставка без DDP, USD за контейнер', max_digits=18, decimal_places=2,
                                                default=1100, validators=[MinValueValidator(Decimal('0'))])
    # Retain the legacy value for existing SQL consumers; product volumes are edited on PGGoods.
    volume_per_container = models.DecimalField('Архивный объём контейнера, м³', max_digits=12, decimal_places=3, default=65, editable=False,
                                               validators=[MinValueValidator(Decimal('0.001'))])

    class Meta:
        verbose_name = 'Фрахт'
        verbose_name_plural = 'Фрахт по сценариям'
        constraints = [
            models.CheckConstraint(condition=models.Q(price_per_container__gte=0), name='freight_price_nonnegative'),
            models.CheckConstraint(condition=models.Q(volume_per_container__gt=0), name='freight_volume_positive'),
            models.CheckConstraint(condition=models.Q(customs_rate__gte=0, customs_rate__lte=100), name='freight_customs_rate_range'),
            models.CheckConstraint(condition=models.Q(warehouse_delivery_cost__gte=0), name='freight_delivery_nonnegative'),
            models.CheckConstraint(condition=models.Q(foreign_delivery_cost__gte=0), name='freight_foreign_delivery_nonnegative'),
        ]

    def __str__(self):
        return f'Фрахт: {self.scenario}'


class ScenarioExport(models.Model):
    scenario = models.ForeignKey(ScenarioModel, on_delete=models.CASCADE, related_name='exports')
    exported_at = models.DateTimeField(auto_now_add=True)
    fingerprint = models.CharField(max_length=64)
    parameters = models.JSONField()

    class Meta:
        ordering = ['-exported_at', '-pk']
        verbose_name = 'Экспорт сценария'
        verbose_name_plural = 'История экспорта сценариев'
