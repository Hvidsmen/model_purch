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
from .services import product_removal as service


class ProductChoice(forms.ModelMultipleChoiceField):
    def label_from_instance(self,obj):return ' · '.join((obj.planning_group_sales,obj.group,obj.brand))


class RemovalForm(forms.Form):
    goods=ProductChoice(label='Товары',queryset=Goods.objects.none(),widget=forms.CheckboxSelectMultiple)
    include_local=forms.BooleanField(label='Удалить также во всех подразделениях текущего черновика',required=False)
    def __init__(self,version,*args,orphan_only=False,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['goods'].queryset=service.available(version,orphan_only)


@require_http_methods(['GET','POST'])
def page(request):
    if not can_access(request.user,'motivation_editor'):raise PermissionDenied
    version=get_object_or_404(versions_for(request.user),pk=request.POST.get('version') or request.GET.get('version'))
    orphan_only=(request.POST.get('orphan_only') if request.method=='POST' else request.GET.get('orphan_only'))=='1'
    form=RemovalForm(version,request.POST if request.method=='POST' else None,orphan_only=orphan_only,
                     initial={'goods':request.GET.getlist('goods'),'include_local':orphan_only})
    plan=None;token=None
    if request.method=='POST' and form.is_valid():
        try:
            if not version.is_editable:raise ValidationError('Изменять можно только текущий черновик.')
            ids=sorted(form.cleaned_data['goods'].values_list('pk',flat=True));local=form.cleaned_data['include_local']
            params=[version.pk,version.revision,ids,local]
            if request.POST.get('action')=='apply':
                try:confirmed=signing.loads(request.POST.get('preview_token',''),salt='product-removal',max_age=3600)
                except signing.BadSignature:raise ValidationError('Сначала выполните предварительный просмотр.')
                if confirmed!=params:raise ValidationError('Параметры или версия изменились. Повторите просмотр.')
                plan=service.apply(request.user,version.pk,version.revision,ids,local)
                messages.success(request,f'Удалено ставок: {plan["total"]}. Исторические версии сохранены.')
                return redirect(reverse('motivation_product_removal')+'?version='+str(version.pk)+('&orphan_only=1' if orphan_only else ''))
            if str(version.revision)!=request.POST.get('revision'):raise ValidationError('Версия изменилась. Обновите страницу.')
            plan=service.preview(version,ids,local)
            token=signing.dumps(params,salt='product-removal')
        except ValidationError as error:form.add_error(None,error)
    return render(request,'admin_motivation/product_removal.html',dict(selected_version=version,form=form,plan=plan,preview_token=token,orphan_only=orphan_only))
