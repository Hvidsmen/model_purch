"""Administrative updates of the isolated application, never the portal checkout."""
import logging
from django.conf import settings
from django.http import JsonResponse,HttpResponse
from django.shortcuts import render,redirect
from django.urls import reverse
from .access import ADMIN,role_for
from scripts.split_monitoring_runtime import read_config,Unavailable
from scripts.split_monitoring_update import start,status,read,directory

logger=logging.getLogger(__name__)


def updates(request):
    if role_for(request.user,'split_monitoring')!=ADMIN or getattr(request,'portal_preview_user',None):
        return JsonResponse({'error':'Обновление кода доступно администратору мониторинга.'},status=403)
    if request.method not in ('GET','POST'):return HttpResponse(status=405,headers={'Allow':'GET, POST'})
    config_path=settings.SPLIT_MONITORING_CONFIG
    error=''
    if request.method=='POST':
        try:
            job=start(config_path,request.user.pk)
            return redirect(reverse('split_monitoring_updates')+'?run='+job['id'])
        except (OSError,Unavailable):
            logger.exception('Cannot dispatch split-monitoring update')
            error='Не удалось начать обновление. Проверьте установку мониторинга и права записи в его локальный каталог.'
    run_id=request.GET.get('run') or (read(directory(config_path)/'current.json',{}) or {}).get('id')
    job=status(config_path,run_id) if run_id else None
    # Status contains operational progress only. Configuration/control tokens,
    # command output and filesystem paths are never returned to the browser.
    if request.GET.get('status')=='1':
        if not job:return JsonResponse({'error':'Запуск не найден.'},status=404)
        return JsonResponse({key:job.get(key) for key in ('id','state','stage','message','steps','started_at','finished_at','target_commit','previous_commit')})
    try:commit=read_config(config_path).get('git_commit','')
    except Unavailable:commit=''
    return render(request,'base/split_monitoring_updates.html',{'update_job':job,'installed_commit':commit,'update_error':error},status=503 if error else 200)
