from django import forms
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.contrib import messages
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from base.access import can_access
from .models import GlobalCoeffVersion,SalesPlanScenario
from .services import global_from_branches as service

SALT='global-from-branches-v1'

class SelectionForm(forms.Form):
    plan=forms.ModelChoiceField(queryset=SalesPlanScenario.objects.filter(is_deleted=False),label='План продаж для весов')
    groups=forms.MultipleChoiceField(choices=list(service.GROUPS.items()),initial=['policies','sales','curators'],widget=forms.CheckboxSelectMultiple,label='Виды коэффициентов')
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['groups'].choices=[(key,label) for key,label in service.GROUPS.items()]


@require_http_methods(['GET','POST'])
def page(request):
    if not can_access(request.user,'motivation_editor'):raise PermissionDenied
    version=get_object_or_404(GlobalCoeffVersion,pk=request.POST.get('version') or request.GET.get('version'))
    form=SelectionForm(request.POST if request.method=='POST' else None)
    context={'selected_version':version,'selection':form}
    try:
        if not version.is_editable:raise ValidationError('Сборка доступна только в последнем черновике. Создайте новую версию для следующей итерации.')
        if request.method=='POST' and form.is_valid():
            scenario=form.cleaned_data['plan'];groups=form.cleaned_data['groups']
            if request.POST.get('action')=='apply':
                token=signing.loads(request.POST.get('preview_token',''),salt=SALT,max_age=1800)
                if token['actor']!=request.user.pk or token['version']!=version.pk:raise PermissionDenied
                if token['plan']!=scenario.pk or token['groups']!=groups:raise ValidationError('Параметры изменились. Повторите предварительный просмотр.')
                if request.POST.get('confirm')!='yes':raise ValidationError('Подтвердите замену глобальных ставок.')
                count=service.apply(request.user,version.pk,scenario.pk,groups,token['revision'],token['digest'])
                messages.success(request,f'Глобальные коэффициенты собраны из подразделений: {count} ячеек.')
                return redirect(reverse('global_coeff_admin_motivation')+'?version='+str(version.pk))
            result=service.preview(version,scenario,groups)
            context.update(result,preview=True,plan=scenario)
            context['preview_token']=signing.dumps({'actor':request.user.pk,'version':version.pk,'revision':version.revision,'plan':scenario.pk,'groups':groups,'digest':result['digest']},salt=SALT,compress=True)
    except (ValidationError,signing.BadSignature) as exc:
        context['error']='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Просмотр устарел. Рассчитайте его заново.'
    return render(request,'admin_motivation/global_from_branches.html',context)
