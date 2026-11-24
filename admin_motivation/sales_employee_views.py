from django import forms
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.http import require_http_methods
from base.access import role_for,ADMIN,DIRECTOR,BRANCH_DIRECTOR
from .models import SalesEmployee,Subdivision,PremiumDistribution
from .services.premium_distribution import branches,authorize


class EmployeeForm(forms.ModelForm):
    class Meta:
        model=SalesEmployee
        fields=['full_name','personnel_number','subdivision','started_on','ended_on','is_archived','user']
        labels={'full_name':'ФИО','personnel_number':'Табельный номер','subdivision':'Подразделение','started_on':'Дата начала работы','ended_on':'Дата окончания работы','is_archived':'В архиве','user':'Учётная запись портала'}
        widgets={'started_on':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),'ended_on':forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d')}

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        allowed=branches(user,write=True)
        self.fields['subdivision'].queryset=Subdivision.objects.all() if allowed is None else Subdivision.objects.filter(subdivision_key__in=allowed)
        if role_for(user,'motivation')!=ADMIN:self.fields.pop('user')

    def clean(self):
        data=super().clean()
        name=' '.join(data.get('full_name','').split());data['full_name']=name
        if data.get('started_on') and data.get('ended_on') and data['started_on']>data['ended_on']:self.add_error('ended_on','Окончание работы раньше начала.')
        if data.get('subdivision'):
            duplicate=SalesEmployee.objects.filter(subdivision=data['subdivision'],full_name__iexact=name).exclude(pk=self.instance.pk)
            if duplicate.exists():self.add_error('full_name','Такой сотрудник уже есть в подразделении.')
        if self.instance.pk:
            old=SalesEmployee.objects.get(pk=self.instance.pk)
            if name!=old.full_name or data.get('subdivision')!=old.subdivision:
                used=any(any(r.get('employee_id')==old.pk or r.get('author')==old.full_name for r in batch.rows) for batch in PremiumDistribution.objects.filter(subdivision=old.subdivision))
                if used:self.add_error(None,'Сотрудник уже участвует в распределениях: ФИО и подразделение сохраняются для связи с историей. Можно изменить даты, табельный номер и статус.')
        return data


@require_http_methods(['GET','POST'])
def directory(request):
    allowed=branches(request.user)
    can_edit=role_for(request.user,'motivation') in (ADMIN,DIRECTOR,BRANCH_DIRECTOR)
    people=SalesEmployee.objects.select_related('subdivision').all()
    if allowed is not None:people=people.filter(subdivision__subdivision_key__in=allowed)
    employee=get_object_or_404(people,pk=request.GET['employee']) if request.GET.get('employee') else None
    if request.method=='POST' and not can_edit:raise PermissionDenied
    form=EmployeeForm(request.POST or None,user=request.user,instance=employee) if can_edit else None
    if request.method=='POST' and form.is_valid():
        if employee:authorize(request.user,employee.subdivision,write=True)
        form.save()
        return redirect('motivation_sales_employees')
    return render(request,'admin_motivation/distribution/employees.html',{'form':form,'people':people,'can_edit':can_edit})
