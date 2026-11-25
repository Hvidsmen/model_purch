"""One confirmed closing multiplier per physical subdivision, year and period."""
from decimal import Decimal, InvalidOperation
from contextvars import ContextVar
import re

correction_mode=ContextVar('manager_correction_mode',default=None)
from datetime import date
from calendar import monthrange
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from base.access import role_for, ADMIN, DIRECTOR
from ..models import PeriodMotivationCoefficient, PremiumDistribution


def period_closed(year, period):
    month = 12 if period == 5 else period * 3
    if not 2000 <= year <= 2100 or period not in (1,2,3,4,5):
        raise ValidationError('Выберите год и период.')
    return date(year,month,monthrange(year,month)[1]) < timezone.localdate()


@transaction.atomic
def confirm(user, subdivision, year, period, coefficient, revision=0, *, legacy_distribution=None):
    if role_for(user,'motivation') not in (ADMIN,DIRECTOR):
        raise PermissionDenied
    if not period_closed(year,period):raise ValidationError('Период ещё не закрыт.')
    try:
        value=Decimal(str(coefficient).replace(',','.'))
        if not value.is_finite() or not 0 <= value <= Decimal('99999999.9999') or value != value.quantize(Decimal('.0001')):raise ValueError
    except (InvalidOperation,ValueError,TypeError):raise ValidationError('Множитель: неотрицательное число, до четырёх знаков после запятой.')
    # Lock the shared branch before the coefficient and allocation to serialize creation.
    type(subdivision).all_objects.select_for_update().get(pk=subdivision.pk)
    existing=PremiumDistribution.objects.filter(subdivision=subdivision,year=year,period=period,state__in=['draft','completed'])
    if legacy_distribution and legacy_distribution.subdivision_id==subdivision.pk and legacy_distribution.year==year and legacy_distribution.period==period and legacy_distribution.state=='draft' and not legacy_distribution.source.get('workflow'):
        existing=existing.exclude(pk=legacy_distribution.pk)
    if existing.exists():
        raise ValidationError('Фонды уже зафиксированы. Множитель этого периода менять нельзя.')
    row=PeriodMotivationCoefficient.objects.select_for_update().filter(subdivision=subdivision,year=year,period=period).first()
    if row and revision != row.revision or not row and revision != 0:
        raise ValidationError('Множитель изменился. Обновите страницу.')
    entry={'coefficient':str(value),'actor':user.pk,'actor_label':user.get_full_name() or user.get_username(),'at':timezone.now().isoformat()}
    if row:
        row.history=[*row.history,entry]
        row.revision+=1;row.coefficient=value;row.confirmed_by=user;row.save()
    else:
        row=PeriodMotivationCoefficient.objects.create(subdivision=subdivision,year=year,period=period,coefficient=value,confirmed_by=user,history=[entry])
    return row


class ReportMultipliers:
    def __init__(self, *, physical=False, mode=None, variant='main'):
        self.values=None
        self.physical=physical
        self.mode=mode if mode is not None else correction_mode.get()
        self.variant=variant
        self.templates=[]

    def template(self, branch, year, period):
        try:
            year=int(year);period=int(period)
            if period not in (1,2,3,4,5):return Decimal(1)
            month=12 if period==5 else period*3
            end=date(year,month,monthrange(year,month)[1])
        except (ValueError,TypeError):return Decimal(1)
        for effective,sub,code,value in self.templates:
            if sub==branch and code==period and effective<=end:return value
        return Decimal(1)

    def multiplier(self, branch, year, code):
        template=self.template(branch,year,code)
        confirmed=self.values.get((branch,year,code))
        if self.mode=='period-unified-v1':return confirmed if confirmed is not None else template
        if confirmed is not None:
            raise ValidationError('Этот запуск уже содержит прежнюю поправку руководителя. Сохраните задание расчёта и запустите его заново для применения единого коэффициента без повторного умножения.')
        return confirmed if confirmed is not None else Decimal(1)

    def apply(self, rows):
        if not rows:return rows
        if self.values is None:
            self.values={(branch.casefold(),str(year),str(period)):value for branch,year,period,value in
                PeriodMotivationCoefficient.objects.values_list('subdivision__subdivision_key','year','period','coefficient')}
            from ..models import SubdivisionManagerCoeff, MotivationExecution
            if self.mode is None:
                from .calculation_variants import main_runs
                runs=main_runs() if self.variant=='main' else MotivationExecution.objects.filter(parameters__variant=self.variant)
                execution=runs.filter(state='completed').order_by('-started_at','-pk').first()
                self.mode=(execution.parameters.get('manager_correction_mode','legacy') if execution else 'legacy')
            from .subdivision_aliases import coefficient_rows
            source=SubdivisionManagerCoeff.objects.filter(version__status='approved').order_by('-version__effective_from','-version_id').values_list('version__effective_from','subdivision__subdivision_key','kind__name','coeff','version_id')
            source=source if self.physical else coefficient_rows(source,1,(0,1,2,4))
            for effective,sub,name,value,version_id in source:
                name=str(name or '').strip().casefold()
                match=re.match(r'^([1-4])\s*кв',name)
                code=int(match[1]) if match else 5 if name in ('год','year') else None
                if code:self.templates.append((effective,sub.casefold(),code,Decimal(str(value))))
        if not self.values and (self.mode!='period-unified-v1' or not self.templates):return rows
        from .reports_next import period_code
        from .manager_detail import MONEY_FIELDS
        result=[]
        for original in rows:
            row=dict(original)
            if row.get('_closing_multiplier_applied'):
                result.append(row);continue
            branch=str((row.get('_source_subdivision') if self.physical else None) or row.get('Подразделение') or '').casefold();year=str(row.get('Год') or '')
            code=period_code(row.get('КварталМотивации'))
            value=self.multiplier(branch,year,code)
            for field in (*MONEY_FIELDS,'PotentialCatchupUSD','PotentialCatchupRUB'):
                if row.get(field) is not None:row[field]=Decimal(str(row[field]))*value
            annual=self.multiplier(branch,year,'5')
            for field in ('AnnualUSD','AnnualRUB','AnnualAccrual'):
                if row.get(field) is not None:row[field]=Decimal(str(row[field]))*annual
            for field in row:
                if field.startswith('СуммаМотивации') and field not in MONEY_FIELDS and field.endswith(('Q','Q_Y','Y_Y')) and row[field] is not None:
                    row[field]=Decimal(str(row[field]))*(annual if field.endswith(('Q_Y','Y_Y')) else value)
            row['_closing_multiplier_applied']=True
            result.append(row)
        return result
