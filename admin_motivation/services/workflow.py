"""Local workflow state; no SQL connection is made while rendering navigation."""
import hashlib
import json
from django.urls import reverse
from ..models import (GlobalCoeffVersion, MotivationExportLog, MotivationExecution,
                      SubdivisionDirectorCoeff, SubdivisionCoeff, Goods, Subdivision, TypeCoeff,
                      SegmentCoeff, VariationCalculate, KindManagerCoeff)


def export_snapshot():
    versions = list(GlobalCoeffVersion.objects.filter(status='approved').order_by('effective_from','pk'))
    good_ids = set(SubdivisionCoeff.objects.filter(version__in=versions).values_list('goods_id',flat=True))
    good_ids.update(SubdivisionDirectorCoeff.objects.filter(version__in=versions).values_list('goods_id',flat=True))
    good_ids.update(pk for version in versions for pk in version.coefficients.values_list('goods_id',flat=True))
    from .subdivision_aliases import rules
    from .subdivision_scope import POLICY
    payload = {
        'subdivision_policy': POLICY,
        'versions': [{'id':v.pk,'title':v.title,'from':v.effective_from.isoformat(),'revision':v.revision,'fallback_variation':v.fallback_variation} for v in versions],
        'aliases': sorted(rules()),
        'goods': list(Goods.objects.filter(pk__in=good_ids).order_by('pk').values('id','goods_key','planning_group_sales','group','brand')),
        'employees': list(SubdivisionDirectorCoeff.objects.filter(version__in=versions).order_by('employee_id').values('employee_id','employee__employee_code','employee__full_name').distinct()),
        'branches': list(Subdivision.all_objects.order_by('pk').values('id','subdivision_key')),
        'types': list(TypeCoeff.objects.order_by('pk').values()),
        'segments': list(SegmentCoeff.objects.order_by('pk').values()),
        'formulas': list(VariationCalculate.objects.order_by('pk').values()),
        'periods': list(KindManagerCoeff.objects.order_by('pk').values()),
    }
    payload['fingerprint'] = hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    return payload


def context():
    snapshot = export_snapshot()
    exported = MotivationExportLog.objects.order_by('-pk').first()
    execution = MotivationExecution.objects.order_by('-started_at').first()
    current = bool(exported and exported.fingerprint == snapshot['fingerprint'])
    complete = bool(execution and execution.state == 'completed')
    calculated_current = bool(complete and current and execution.export_snapshot.get('fingerprint') == snapshot['fingerprint'])
    if not snapshot['versions']:
        next_label, next_route = 'Настройте и утвердите версию', 'global_coeff_admin_motivation'
    elif not current:
        next_label, next_route = 'Выгрузить утверждённые настройки', 'loader_motive'
    elif not calculated_current:
        next_label, next_route = 'Проверить или запустить расчёт', 'motivation_calculations'
    else:
        next_label, next_route = 'Открыть отчёты', 'motivation_reports'
    return {'exported':exported,'execution':execution,'export_current':current,
            'calculated_current':calculated_current,'has_approved':bool(snapshot['versions']),
            'next_label':next_label,'next_url':reverse(next_route),
            'export_label':'Выгрузка актуальна' if current else 'Нужна выгрузка' if exported else 'Выгрузка не подтверждена',
            'calculation_label':('Расчёт по текущим настройкам' if calculated_current else 'Выполняется' if execution and execution.state=='running' else 'Ошибка расчёта' if execution and execution.state=='failed' else 'Нужен пересчёт' if execution else 'Расчёт не подтверждён')}
