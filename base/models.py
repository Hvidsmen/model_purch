from django.conf import settings
from django.db import models


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
