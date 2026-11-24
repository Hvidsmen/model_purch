from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import FileResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from base.access import can_access,role_for,ADMIN,MODELER,DIRECTOR,FINANCIER,MOTIVATION_VIEWER
from .models import CuratorEmployee,CuratorCoefficient,Subdivision,GlobalCoeffVersion,Goods
from .services import curators


class CuratorForm(forms.ModelForm):
    class Meta:
        model=CuratorEmployee
        fields=('full_name','user','subdivisions','is_archived')
        labels={'user':'Учётная запись личного кабинета'}
        help_texts={'user':'В доступах этой учётной записи назначьте роль «Куратор». Личный отчёт использует только закреплённые здесь подразделения.'}

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['subdivisions'].queryset=Subdivision.all_objects.order_by('subdivision_key')
        if self.instance.pk:self.initial['subdivisions']=list(self.instance.assigned_subdivisions().values_list('pk',flat=True))

    def save(self,commit=True):
        instance=super().save(commit=False)
        if not commit:raise ValueError('CuratorForm requires atomic complete save')
        instance.save()
        through=CuratorEmployee.subdivisions.through
        through.objects.filter(curatoremployee=instance).delete()
        through.objects.bulk_create([through(curatoremployee=instance,subdivision=sub) for sub in self.cleaned_data['subdivisions']])
        return instance


@require_http_methods(['GET','POST'])
def directory(request):
    if role_for(request.user,'motivation') not in (ADMIN,MODELER,DIRECTOR,FINANCIER,MOTIVATION_VIEWER):raise PermissionDenied
    if request.method=='POST' and not can_access(request.user,'motivation_editor'):raise PermissionDenied
    employee=get_object_or_404(CuratorEmployee,pk=request.GET['employee']) if request.GET.get('employee') else None
    form=CuratorForm(request.POST or None,instance=employee)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():form.save()
        messages.success(request,'Назначения куратора сохранены. Для новых сумм выгрузите настройки и запустите расчёт.')
        return redirect('motivation_curators')
    return render(request,'admin_motivation/curators.html',{'can_edit':can_access(request.user,'motivation_editor'),'form':form,'employees':CuratorEmployee.objects.prefetch_related(Prefetch('subdivisions',queryset=Subdivision.all_objects.order_by('subdivision_key'))),'employee':employee})


@require_http_methods(['GET','POST'])
def rates(request,pk):
    if role_for(request.user,'motivation') not in (ADMIN,MODELER,DIRECTOR,FINANCIER,MOTIVATION_VIEWER):raise PermissionDenied
    if request.method=='POST' and not can_access(request.user,'motivation_editor'):raise PermissionDenied
    employee=get_object_or_404(CuratorEmployee,pk=pk)
    from .services.version_context import version_context
    context=version_context(request);version=context['selected_version']
    subdivision=get_object_or_404(employee.assigned_subdivisions(),pk=request.GET['subdivision']) if request.GET.get('subdivision') else employee.assigned_subdivisions().first()
    if request.GET.get('template') and subdivision and version:return FileResponse(curators.workbook(employee,subdivision,version),as_attachment=True,filename='curator-coefficients.xlsx')
    if request.method=='POST':
        try:
            if not version or not subdivision:raise ValidationError('Выберите версию и подразделение.')
            rows=curators.read_upload(request.FILES['file']) if request.FILES.get('file') else [{'goods':request.POST.get('goods'),**{key:request.POST.get(key) for key in curators.KEYS}}]
            count=curators.save_rates(version.pk,employee,subdivision,rows,request.POST.get('revision'))
            messages.success(request,f'Сохранено строк: {count}.')
            return redirect(reverse('motivation_curator_rates',args=[pk])+f'?version={version.pk}&subdivision={subdivision.pk}')
        except ValidationError as error:messages.error(request,' '.join(error.messages))
    editing=CuratorCoefficient.objects.filter(version=version,employee=employee,subdivision=subdivision,goods_id=request.GET.get('goods')).first() if str(request.GET.get('goods','')).isdigit() else None
    context.update(curators.global_context(version))
    context.update(editing=editing,employee=employee,subdivision=subdivision,goods=Goods.objects.order_by('pk'),rates=CuratorCoefficient.objects.filter(version=version,employee=employee,subdivision=subdivision).select_related('goods'),keys=[{'name':key,'value':getattr(editing,key)*100 if editing else ''} for key in curators.KEYS])
    return render(request,'admin_motivation/curator_rates.html',context)


@require_http_methods(['GET'])
def coefficient_workspace(request):
    if role_for(request.user,'motivation') not in (ADMIN,MODELER,DIRECTOR,FINANCIER,MOTIVATION_VIEWER):raise PermissionDenied
    from .services.version_context import version_context
    context=version_context(request)
    employees=CuratorEmployee.objects.filter(is_archived=False).prefetch_related(
        Prefetch('subdivisions',queryset=Subdivision.all_objects.order_by('subdivision_key'))).order_by('full_name')
    context.update(employees=employees)
    return render(request,'admin_motivation/curator_coefficients.html',context)


@require_http_methods(['POST'])
def global_rates(request):
    if not can_access(request.user,'motivation_editor'):raise PermissionDenied
    from .services.version_context import version_context
    version=version_context(request)['selected_version']
    try:
        if not version:raise ValidationError('Выберите версию.')
        curators.save_global_rates(version.pk,request.POST.get('goods'),{k:request.POST.get(k) for k in curators.KEYS},request.POST.get('variation'),request.POST.get('revision'))
        messages.success(request,'Глобальные коэффициенты кураторов сохранены.')
    except ValidationError as error:messages.error(request,' '.join(error.messages))
    return redirect(reverse('global_coeff_admin_motivation')+('?version='+str(version.pk) if version else '')+'#global-curators')


@require_http_methods(['POST'])
def copy_global(request):
    if not can_access(request.user,'motivation_editor'):raise PermissionDenied
    from .services.version_context import version_context
    version=version_context(request)['selected_version']
    target=reverse('global_coeff_admin_motivation')+('?version='+str(version.pk) if version else '')+'#global-curators'
    try:
        if not version:raise ValidationError('Выберите версию.')
        pairs=request.POST.getlist('targets')
        if len(pairs)>200:raise ValidationError('Выберите не более 200 получателей.')
        targets=[]
        for pair in pairs:
            pieces=pair.split(':')
            if len(pieces)!=2 or any(not p.isdigit() for p in pieces):raise ValidationError('Некорректный получатель.')
            employee=get_object_or_404(CuratorEmployee,pk=pieces[0],is_archived=False)
            subdivision=get_object_or_404(employee.assigned_subdivisions(),pk=pieces[1])
            targets.append((employee,subdivision))
        goods=request.POST.getlist('goods_ids')
        if len(goods)>5000 or any(not g.isdigit() for g in goods):raise ValidationError('Некорректный список товаров.')
        result=curators.copy_global_rates(version.pk,targets,request.POST.get('revision'),goods_ids=[int(g) for g in goods],replace=request.POST.get('replace')=='on')
        messages.success(request,f"Скопировано из глобальных: добавлено {result['created']}, заменено {result['updated']}, сохранено существующих {result['skipped']}.")
        if request.POST.get('return_to')=='curator' and len(targets)==1:
            employee,subdivision=targets[0]
            target=reverse('motivation_curator_rates',args=[employee.pk])+f'?version={version.pk}&subdivision={subdivision.pk}'
    except ValidationError as error:messages.error(request,' '.join(error.messages))
    return redirect(target)
