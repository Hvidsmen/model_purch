from django.conf import settings
from django.db import models


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
    parent_key = models.CharField('Родитель DataWH', max_length=255, blank=True)
    planning_group = models.TextField('Группа планирования', blank=True)
    sales_plan_group = models.TextField('Группа планов продаж', blank=True)
    brand = models.TextField('Марка', blank=True)
    power_class = models.TextField('Класс мощности', blank=True)
    block_type = models.TextField('Тип блока', blank=True)
    outdoor_block_type = models.TextField('Тип наружного блока', blank=True)
    is_active = models.BooleanField('Присутствует в источнике', default=True)

    class Meta:
        ordering = ['name', 'source_key']


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


