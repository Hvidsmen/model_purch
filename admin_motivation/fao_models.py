"""Append-only financial corrections, separate from replaceable DWH results."""
from django.db import models
from django.core.exceptions import ValidationError


class FaoWorkflow(models.Model):
    activated_on=models.DateField()


class FaoReview(models.Model):
    subdivision=models.ForeignKey('admin_motivation.Subdivision',on_delete=models.PROTECT)
    year=models.PositiveIntegerField()
    quarter=models.PositiveSmallIntegerField()
    state=models.CharField(max_length=12,default='open',choices=[('open','Корректировка открыта'),('completed','Проверка завершена')])
    revision=models.PositiveIntegerField(default=1)
    reason=models.TextField()
    opened_label=models.CharField(max_length=255,blank=True,default='')
    completed_label=models.CharField(max_length=255,blank=True,default='')
    opened_by=models.ForeignKey('auth.User',null=True,on_delete=models.SET_NULL,related_name='+')
    completed_by=models.ForeignKey('auth.User',null=True,on_delete=models.SET_NULL,related_name='+')
    opened_at=models.DateTimeField(auto_now_add=True)
    completed_at=models.DateTimeField(null=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['subdivision','year','quarter'],condition=models.Q(state='open'),name='unique_open_fao_review'),models.CheckConstraint(condition=models.Q(quarter__gte=1,quarter__lte=4),name='fao_valid_quarter')]
        ordering=['-pk']


class FaoDocument(models.Model):
    subdivision=models.ForeignKey('admin_motivation.Subdivision',on_delete=models.PROTECT)
    number=models.CharField(max_length=255)
    document_date=models.DateField()
    execution=models.ForeignKey('admin_motivation.MotivationExecution',on_delete=models.PROTECT)
    rows=models.JSONField()
    original_amount=models.DecimalField(max_digits=20,decimal_places=2)
    source_hash=models.CharField(max_length=64)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['subdivision','number','document_date'],name='unique_fao_source_document')]

    def save(self,*args,**kwargs):
        if not self._state.adding:raise ValidationError('Снимок исходной ОЗС не изменяется.')
        return super().save(*args,**kwargs)


class FaoCorrection(models.Model):
    review=models.ForeignKey(FaoReview,on_delete=models.PROTECT,related_name='corrections')
    document=models.ForeignKey(FaoDocument,on_delete=models.PROTECT,related_name='corrections')
    previous_amount=models.DecimalField(max_digits=20,decimal_places=2)
    new_amount=models.DecimalField(max_digits=20,decimal_places=2)
    delta=models.DecimalField(max_digits=20,decimal_places=2)
    correction_date=models.DateField()
    excluded=models.BooleanField(default=False)
    payload=models.JSONField()
    actor_label=models.CharField(max_length=255,blank=True,default='')
    actor=models.ForeignKey('auth.User',null=True,on_delete=models.SET_NULL)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering=['pk']

    @property
    def premium_changes(self):
        from decimal import Decimal
        return [dict(kind=kind,usd=sum((Decimal(str(row.get('СуммаМотивацииUSD') or 0)) for row in self.payload if row['ТипКоэф']==kind),Decimal(0)),
            rub=sum((Decimal(str(row.get('AmountRURMotive') or 0)) for row in self.payload if row['ТипКоэф']==kind),Decimal(0)) if all(row.get('AmountRURMotive') is not None for row in self.payload if row['ТипКоэф']==kind) else None) for kind in sorted({row['ТипКоэф'] for row in self.payload})]

    def save(self,*args,**kwargs):
        if not self._state.adding:raise ValidationError('Корректировка не изменяется; создайте новую дельту.')
        return super().save(*args,**kwargs)

    def delete(self,*args,**kwargs):raise ValidationError('История корректировок не удаляется.')
