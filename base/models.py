from django.conf import settings
from django.db import models


class MotivationReportAccess(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='motivation_report_access')
    subdivision = models.ForeignKey('admin_motivation.Subdivision', on_delete=models.PROTECT, verbose_name='Подразделение для отчётов')
    deal_author = models.CharField(max_length=255, verbose_name='Точное ФИО автора сделки', help_text='Выберите ФИО из подсказок или скопируйте точное значение из отчёта. Поиск только по фамилии не используется.')

    class Meta:
        verbose_name = 'Доступ к личным отчётам мотивации'
        verbose_name_plural = 'Доступ к личным отчётам мотивации'
