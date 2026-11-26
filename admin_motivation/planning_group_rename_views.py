from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from base.access import can_access
from .coverage_views import versions_for
from .models import Goods
from .services import planning_group_rename as service


class RenameForm(forms.Form):
    source=forms.ChoiceField(label='Исходная группа')
    target=forms.CharField(label='Правильное название',max_length=255,strip=True)
    def __init__(self,version,*args,**kwargs):
        super().__init__(*args,**kwargs)
        ids=set()
        for model in service.MODELS:ids.update(model.objects.filter(version=version).values_list('goods_id',flat=True))
        names=set(Goods.objects.filter(pk__in=ids).values_list('planning_group_sales',flat=True))
        names.update(row['planning'] for row in version.planning_rules)
        self.fields['source'].choices=[('', 'Выберите группу'),*[(name,name) for name in sorted(names)]]


@require_http_methods(['GET','POST'])
def page(request):
    if not can_access(request.user,'motivation_editor'):raise PermissionDenied
    versions=versions_for(request.user)
    version=get_object_or_404(versions,pk=request.POST.get('version') or request.GET.get('version'))
    form=RenameForm(version,request.POST if request.method=='POST' else None)
    plan=None;token=None
    if request.method=='POST' and form.is_valid():
        try:
            if not version.is_editable:raise ValidationError('Изменять можно только текущий черновик.')
            source,target=form.cleaned_data['source'],form.cleaned_data['target']
            if request.POST.get('action')=='apply':
                try:confirmed=signing.loads(request.POST.get('preview_token',''),salt='planning-group-rename',max_age=3600)
                except signing.BadSignature:raise ValidationError('Подтвердите замену через предварительный просмотр.')
                if confirmed!=[version.pk,version.revision,source,target]:raise ValidationError('Параметры или версия изменились. Повторите просмотр.')
                result=service.apply(request.user,version.pk,confirmed[1],source,target)
                messages.success(request,f'Группа переименована. Перенесено ставок: {result["total"]}, правил: {result["rule_count"]}.')
                return redirect(reverse('motivation_planning_group_rules')+'?version='+str(version.pk))
            if str(version.revision)!=request.POST.get('revision'):raise ValidationError('Версия изменилась. Обновите страницу.')
            plan=service.preview(version,source,target)
            token=signing.dumps([version.pk,version.revision,source,target],salt='planning-group-rename')
        except ValidationError as error:form.add_error(None,error)
    return render(request,'admin_motivation/planning_group_rename.html',dict(selected_version=version,form=form,plan=plan,preview_token=token))
