from django.conf import settings
from django.db import models
import uuid


class SalesPlanningChannel(models.Model):
    name = models.CharField('Канал продаж', max_length=255, unique=True)
    is_active = models.BooleanField('Присутствует в источнике', default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class SalesPlanningGoods(models.Model):
    source_key = models.CharField('Ссылка DataWH', max_length=255, unique=True)
    name = models.TextField('Наименование')
    product_group = models.TextField('Товарная группа', blank=True)
    planning_group = models.TextField('Группа планирования', blank=True)
    sales_plan_group = models.TextField('Группа планов продаж', blank=True)
    brand = models.TextField('Бренд', blank=True)
    power_class = models.TextField('Мощность', blank=True)
    block_type = models.TextField('Тип блока', blank=True)
    outdoor_block_type = models.TextField('INV / On-Off', blank=True)
    is_active = models.BooleanField('Присутствует в источнике', default=True)

    class Meta:
        ordering = ['name', 'source_key']


class SalesPlanningGroup(models.Model):
    source_key = models.CharField(max_length=64, unique=True)
    name = models.TextField('Группа планирования')
    source_names = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    notes = models.TextField('Примечание Split', blank=True)
    related_groups = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='related_from')
    replacement_groups = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='replacement_for')

    class Meta:
        ordering = ['name', 'pk']

    def __str__(self):
        return self.name


class SalesPlanningBlockPair(models.Model):
    group = models.ForeignKey(SalesPlanningGroup, on_delete=models.CASCADE, related_name='block_pairs')
    power = models.TextField('Мощность')
    indoor = models.ForeignKey(SalesPlanningGoods, on_delete=models.PROTECT, related_name='+')
    outdoor = models.ForeignKey(SalesPlanningGoods, on_delete=models.PROTECT, related_name='+')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'power'], name='unique_split_group_power_pair')]


class SplitScenario(models.Model):
    family = models.UUIDField(default=uuid.uuid4, editable=False)
    version = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=200)
    year = models.PositiveIntegerField()
    start = models.DateField()
    end = models.DateField()
    stock_date = models.DateField()
    entry_mode = models.CharField(max_length=10, default='period')
    status = models.CharField(max_length=10, default='draft')
    revision = models.PositiveIntegerField(default=1)
    state = models.JSONField(default=dict)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['family', 'version'], name='unique_split_scenario_version'),
                       models.UniqueConstraint(fields=['family'], condition=models.Q(status='draft'), name='one_split_family_draft')]

    def save(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        if self.pk and type(self).objects.filter(pk=self.pk, status='approved').exists():
            raise ValidationError('Утверждённая версия неизменяема. Создайте корректировку.')
        super().save(*args, **kwargs)


class SplitScenarioEvent(models.Model):
    scenario = models.ForeignKey(SplitScenario, on_delete=models.PROTECT, related_name='events')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    at = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=200)
    revision = models.PositiveIntegerField()


class SalesPlanningDirectoryLoad(models.Model):
    directory = models.CharField(max_length=40, unique=True)
    loaded_at = models.DateTimeField()
    row_count = models.PositiveIntegerField()


class MotivationReportAccess(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='motivation_report_access')
    subdivision = models.ForeignKey('admin_motivation.Subdivision', on_delete=models.PROTECT, verbose_name='Подразделение для отчётов')
    deal_author = models.CharField(max_length=255, verbose_name='Точное ФИО автора сделки', help_text='Выберите ФИО из подсказок или скопируйте точное значение из отчёта. Поиск только по фамилии не используется.')

    class Meta:
        verbose_name = 'Доступ к личным отчётам мотивации'
        verbose_name_plural = 'Доступ к личным отчётам мотивации'


class PortalReportGroup(models.Model):
    portal_role = models.CharField('Роль в портале', max_length=40, default='Политик')
    group = models.OneToOneField('auth.Group', on_delete=models.CASCADE, related_name='portal_scope')
    employee_type = models.ForeignKey('admin_motivation.TypeCoeff', on_delete=models.PROTECT, null=True, blank=True, verbose_name='Вид работника')
    subdivisions = models.ManyToManyField('admin_motivation.Subdivision', verbose_name='Подразделения', related_name='portal_report_groups')

    class Meta:
        verbose_name = 'Группа доступа к отчётам'
        verbose_name_plural = 'Группы доступа к отчётам'

    def __str__(self):
        return self.group.name


class UserProjectAccess(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='project_access')
    project = models.CharField(max_length=20)
    role = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'project'], name='unique_user_project_access')]


class GroupProjectAccess(models.Model):
    group = models.ForeignKey('auth.Group', on_delete=models.CASCADE, related_name='project_access')
    project = models.CharField(max_length=20)
    role = models.CharField(max_length=40, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['group', 'project'], name='unique_group_project_access')]





