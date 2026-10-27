from django.core.exceptions import PermissionDenied
from django.contrib.auth.models import User
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from .access import can_access
from . import portal_updates
from scripts import portal_update_state as protocol


@never_cache
@require_GET
def dashboard(request):
    if not can_access(request.user,'admin'):
        raise PermissionDenied
    from admin_motivation.models import MotivationCalculationSchedule, MotivationExecution, GlobalCoeffVersion
    from django.db.models import OuterRef,Subquery
    recent=MotivationExecution.objects.filter(job_key=OuterRef('key')).order_by('-started_at','-pk')
    jobs=list(MotivationCalculationSchedule.objects.annotate(last_observed_state=Subquery(recent.values('state')[:1])).order_by('pk'))
    executions=list(MotivationExecution.objects.order_by('-started_at')[:10])
    update_error=''
    try:
        update=protocol.observe(portal_updates.root(),protocol.active(portal_updates.root()))
        history=protocol.history(portal_updates.root())
        latest_update=protocol.observe(portal_updates.root(),history[0]) if history else None
    except (OSError,ValueError):
        update=None;latest_update=None;update_error='История обновлений недоступна. Откройте обслуживание портала.'
    from django.utils.dateparse import parse_datetime
    if latest_update:
        latest_update={**latest_update,'started_display':parse_datetime(latest_update.get('started_at',''))}
    return render(request,'base/administration.html',{'jobs':jobs,'executions':executions,'update':update,'latest_update':latest_update,'update_error':update_error,'users_count':User.objects.filter(is_active=True).count(),'attention_jobs':[job for job in jobs if job.error or not job.synced or job.last_observed_state=='failed'],'pending_versions':GlobalCoeffVersion.objects.filter(status__in=['finance_review','director_review']).count()})
