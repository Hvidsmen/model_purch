"""Atomic CEO confirmation of one period across the physical branch directory."""
from decimal import Decimal
from django import forms
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.utils import timezone
from base.access import role_for,ADMIN,DIRECTOR
from ..models import Subdivision,PeriodMotivationCoefficient,PremiumDistribution
from .closing_coefficient import confirm,period_closed
from .navigation_subdivisions import navigation_subdivisions

SALT='motivation-closing-bulk-v1'

class PeriodForm(forms.Form):
    year=forms.IntegerField(min_value=2000,max_value=2100,label='Год',widget=forms.NumberInput(attrs={'onchange':'this.form.requestSubmit()'}))
    period=forms.TypedChoiceField(coerce=int,choices=PremiumDistribution._meta.get_field('period').choices,label='Период',widget=forms.Select(attrs={'onchange':'this.form.requestSubmit()'}))


def records(year,period,selected=None):
    source=navigation_subdivisions()
    if selected:source=source.filter(pk__in=selected)
    branches=list(source)
    values={r.subdivision_id:r for r in PeriodMotivationCoefficient.objects.select_related('confirmed_by').filter(year=year,period=period)}
    locked=set(PremiumDistribution.objects.filter(year=year,period=period,state__in=['draft','completed']).values_list('subdivision_id',flat=True))
    from ..models import FaoPaymentClosure
    locked.update(FaoPaymentClosure.objects.filter(year=year,period__in={period,5}).values_list('subdivision_id',flat=True))
    from .fao_review import readiness
    locked.update(pk for pk,ready in readiness(branches,year,period).items() if not ready)
    return branches,values,locked


class GridForm(forms.Form):
    token=forms.CharField(widget=forms.HiddenInput)

    def __init__(self,*args,branches,values,locked,**kwargs):
        super().__init__(*args,**kwargs)
        self.rows=[]
        for branch in branches:
            record=values.get(branch.pk);name=f'coefficient_{branch.pk}'
            self.fields[name]=forms.DecimalField(min_value=0,max_digits=12,decimal_places=4,initial=record.coefficient if record else Decimal(1),disabled=branch.pk in locked,label=str(branch),widget=forms.TextInput(attrs={'inputmode':'decimal','aria-label':f'Множитель {branch}'}))
            self.rows.append({'branch':branch,'record':record,'locked':branch.pk in locked,'field':self[name]})
        self.main_rows=self.rows


@transaction.atomic
def save(user,token,values,year,period):
    if role_for(user,'motivation') not in (ADMIN,DIRECTOR):raise PermissionDenied
    try:payload=signing.loads(token,salt=SALT,max_age=3600)
    except signing.BadSignature:raise ValidationError('Таблица устарела. Загрузите подразделения заново.')
    if payload['user']!=user.pk:raise PermissionDenied
    if (payload['year'],payload['period'])!=(year,period):raise ValidationError('Период изменился. Загрузите таблицу заново.')
    if not period_closed(year,period):raise ValidationError('Период ещё не закрыт.')
    list(Subdivision.all_objects.select_for_update().order_by('pk'))
    branches,current,locked=records(year,period,payload.get('selected'))
    expected=[[b.pk,current[b.pk].revision if b.pk in current else 0,b.pk in locked] for b in branches]
    if expected!=payload['rows']:raise ValidationError('Подразделения, множители или фонды изменились. Загрузите таблицу заново.')
    count=0
    for branch in branches:
        if branch.pk in locked:continue
        if branch.pk not in values:raise ValidationError('В таблице отсутствует подразделение.')
        record=current.get(branch.pk)
        confirm(user,branch,year,period,values[branch.pk],record.revision if record else 0)
        count+=1
    return count


def workspace(request,can_confirm):
    """Return the selection and grid; the view handles the successful redirect."""
    submitting=request.method=='POST' and request.POST.get('action')=='confirm_coefficients'
    if submitting and not can_confirm:raise PermissionDenied
    today=timezone.localdate()
    data=request.POST if submitting else request.GET
    initial={'year':today.year,'period':max(1,(today.month-1)//3)}
    selection=PeriodForm(data if 'year' in data or 'period' in data else initial)
    context={'closing_selection':selection,'closing_grid':None,'closing_error':'','closing_count':None}
    if not can_confirm or not selection.is_valid():return context
    year,period=selection.cleaned_data['year'],selection.cleaned_data['period']
    selected=list(dict.fromkeys(value for value in data.getlist('branch_filter') if value.isdigit()))
    branches,values,locked=records(year,period,selected)
    payload={'user':request.user.pk,'year':year,'period':period,'selected':selected,'rows':[[b.pk,values[b.pk].revision if b.pk in values else 0,b.pk in locked] for b in branches]}
    grid=GridForm(request.POST if submitting else None,branches=branches,values=values,locked=locked,initial={'token':signing.dumps(payload,salt=SALT,compress=True)})
    context.update(closing_grid=grid,closing_year=year,closing_period=period,closing_closed=period_closed(year,period),closing_editable_count=len(branches)-len(locked))
    if submitting and grid.is_valid():
        try:context['closing_count']=save(request.user,grid.cleaned_data['token'],{b.pk:grid.cleaned_data[f'coefficient_{b.pk}'] for b in branches if b.pk not in locked},year,period)
        except ValidationError as exc:context['closing_error']='; '.join(exc.messages)
    return context
