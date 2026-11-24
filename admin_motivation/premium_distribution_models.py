"""Period-close allocations are independent of replaceable DWH result tables."""
from django.db import models


class PremiumDistribution(models.Model):
    subdivision = models.ForeignKey('admin_motivation.Subdivision', on_delete=models.PROTECT)
    execution = models.ForeignKey('admin_motivation.MotivationExecution', on_delete=models.PROTECT)
    year = models.PositiveIntegerField()
    period = models.PositiveSmallIntegerField(choices=[(1,'1 квартал'),(2,'2 квартал'),(3,'3 квартал'),(4,'4 квартал'),(5,'Закрытие года')])
    currency = models.CharField(max_length=3, choices=[('USD','USD'),('RUB','RUB')])
    state = models.CharField(max_length=12, default='draft', choices=[('draft','В работе'),('completed','Завершено')])
    revision = models.PositiveIntegerField(default=1)
    funds = models.JSONField(default=list)
    rows = models.JSONField(default=list)
    source = models.JSONField(default=dict)
    created_by = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL, related_name='+')
    completed_by = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['subdivision','year','period'], name='unique_distribution_period')]
        ordering = ['-year','-period','subdivision_id']


class PremiumDistributionEvent(models.Model):
    distribution = models.ForeignKey(PremiumDistribution, on_delete=models.PROTECT, related_name='events')
    revision = models.PositiveIntegerField()
    action = models.CharField(max_length=16)
    actor = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    actor_label = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    comment = models.TextField(blank=True)
    snapshot = models.JSONField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['distribution','revision'], name='unique_distribution_revision')]
        ordering = ['-revision']

    def save(self, *args, **kwargs):
        if not self._state.adding:
            from django.core.exceptions import ValidationError
            raise ValidationError('История распределения не изменяется.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError
        raise ValidationError('История распределения не удаляется.')


class SalesEmployee(models.Model):
    full_name = models.CharField(max_length=255)
    personnel_number = models.CharField(max_length=64, blank=True)
    subdivision = models.ForeignKey('admin_motivation.Subdivision', on_delete=models.PROTECT)
    started_on = models.DateField(null=True, blank=True)
    ended_on = models.DateField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    user = models.OneToOneField('auth.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='motivation_sales_employee')

    class Meta:
        ordering=['full_name','pk']
        constraints=[models.UniqueConstraint(fields=['subdivision','full_name'],name='unique_sales_employee_name')]

    def __str__(self):return self.full_name


class PeriodMotivationCoefficient(models.Model):
    subdivision = models.ForeignKey('admin_motivation.Subdivision', on_delete=models.PROTECT)
    year = models.PositiveIntegerField()
    period = models.PositiveSmallIntegerField(choices=PremiumDistribution._meta.get_field('period').choices)
    coefficient = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    history = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=1)
    confirmed_by = models.ForeignKey('auth.User', null=True, on_delete=models.SET_NULL)
    confirmed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['subdivision','year','period'], name='unique_period_motivation_coefficient'),
                       models.CheckConstraint(condition=models.Q(coefficient__gte=0), name='period_motivation_coefficient_nonnegative')]
