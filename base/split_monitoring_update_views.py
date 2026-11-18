"""Administrative updates of the isolated application, never the portal checkout."""
import logging
from django import forms
from scripts.split_monitoring_package import MAX_UPLOAD_BYTES,PackageError
from django.conf import settings
from django.http import JsonResponse,HttpResponse
from django.shortcuts import render,redirect
from django.urls import reverse
from .access import ADMIN,role_for
from scripts.split_monitoring_runtime import read_config,Unavailable
from scripts.split_monitoring_update import start,status,read,directory

logger=logging.getLogger(__name__)


class PackageUpdateForm(forms.Form):
    package=forms.FileField(label='Полный приватный ZIP',widget=forms.ClearableFileInput(attrs={'accept':'.zip'}))
    sha256=forms.RegexField(regex=r'^[0-9a-fA-F]{64}$',required=False,label='SHA-256 ZIP (если передан автором)',max_length=64)

    def clean_package(self):
        uploaded=self.cleaned_data['package']
        if not uploaded.name.lower().endswith('.zip'):raise forms.ValidationError('Выберите ZIP-пакет.')
        if uploaded.size>MAX_UPLOAD_BYTES:raise forms.ValidationError('Размер ZIP превышает 64 МБ.')
        return uploaded


def updates(request):
    if role_for(request.user,'split_monitoring')!=ADMIN or getattr(request,'portal_preview_user',None):
        return JsonResponse({'error':'Обновление кода доступно администратору мониторинга.'},status=403)
    if request.method not in ('GET','POST'):return HttpResponse(status=405,headers={'Allow':'GET, POST'})
    config_path=settings.SPLIT_MONITORING_CONFIG
    error='';response_status=200;package_form=PackageUpdateForm()
    if request.method=='POST':
        try:
            if request.POST.get('source')=='package':
                package_form=PackageUpdateForm(request.POST,request.FILES)
                if package_form.is_valid():
                    job=start(config_path,request.user.pk,uploaded_file=package_form.cleaned_data['package'],expected_sha256=package_form.cleaned_data['sha256'])
                else:job=None;response_status=400
            else:job=start(config_path,request.user.pk)
            if job:return redirect(reverse('split_monitoring_updates')+'?run='+job['id'])
        except PackageError as exc:
            error=str(exc);response_status=400
        except (OSError,Unavailable):
            logger.exception('Cannot dispatch split-monitoring update')
            response_status=503
            error='Не удалось начать обновление. Проверьте установку мониторинга и права записи в его локальный каталог.'
    run_id=request.GET.get('run') or (read(directory(config_path)/'current.json',{}) or {}).get('id')
    job=status(config_path,run_id) if run_id else None
    # Status contains operational progress only. Configuration/control tokens,
    # command output and filesystem paths are never returned to the browser.
    if request.GET.get('status')=='1':
        if not job:return JsonResponse({'error':'Запуск не найден.'},status=404)
        return JsonResponse({key:job.get(key) for key in ('id','state','stage','message','steps','started_at','finished_at','target_commit','previous_commit','source','package_sha256','package_created_at')})
    try:
        config=read_config(config_path);commit=config.get('git_commit') or 'ZIP SHA-256: '+config['package_sha256']
    except Unavailable:commit=''
    return render(request,'base/split_monitoring_updates.html',{'update_job':job,'installed_commit':commit,'update_error':error,'package_form':package_form},status=response_status)
