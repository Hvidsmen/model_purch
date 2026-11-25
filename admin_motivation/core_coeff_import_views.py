from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.contrib import messages
from django.shortcuts import get_object_or_404,redirect,render
from django.http import Http404
from django.urls import reverse
from base.access import can_access
from .models import GlobalCoeffVersion,VariationCalculate,SubdivisionCoeff
from .services import core_coeff_import as service

SALT='core-coefficient-matrices-v1'


def targets(plan):return [(r['goods_id'],r['branch_id'],r['kind']) for r in plan['rows']]


def page(request,version):
    if not can_access(request.user,'motivation_editor'):raise PermissionDenied
    if version is None:raise Http404('Выберите версию коэффициентов.')
    data={'selected_version':version,'formulas':VariationCalculate.objects.order_by('pk')}
    try:
        if request.method=='POST':
            if not version.is_editable:raise ValidationError('Загрузка доступна только в последнем черновике.')
            action=request.POST.get('action_button')
            token=request.POST.get('preview_token')
            payload=signing.loads(token,salt=SALT,max_age=1800) if token else None
            if payload and (payload['actor']!=request.user.pk or payload['version']!=version.pk):raise PermissionDenied
            if action=='apply_core_import':
                if not payload or request.POST.get('confirm_import')!='yes':raise ValidationError('Подтвердите замену ставок после предварительного просмотра.')
                current=service.resolve(payload['rows'],payload['mappings'],payload.get('scope'))
                if [list(v) for v in targets(current)]!=payload['targets']:raise ValidationError('Справочники изменились. Повторите предварительный просмотр.')
                result=service.apply(version.pk,payload['revision'],payload['rows'],payload['mappings'],request.POST.get('formula'),scope=payload.get('scope'))
                messages.success(request,f"Импорт завершён: новых ячеек {result['created']}, изменено {result['updated']}. Политики и продажи по подразделениям.")
                return redirect(reverse('global_coeff_admin_motivation')+'?version='+str(version.pk))
            if payload:
                if str(payload['revision'])!=str(version.revision):raise ValidationError('Версия изменилась. Загрузите файл заново.')
                rows=payload['rows'];mappings=payload['mappings'];scope=payload.get('scope',rows)
            else:
                upload=request.FILES.get('coefficient_file') or request.FILES.get('myfile')
                if not upload:raise ValidationError('Выберите файл XLSX.')
                parsed=service.parse(upload,with_scope=True);rows=parsed['rows'];scope=parsed['scope'];mappings={}
            mappings={**mappings,**{k:v for k,v in request.POST.items() if k.startswith(('goods_','branch_')) and v}}
            plan=service.resolve(rows,mappings,scope)
            existing={(r.subdivision_id,r.goods_id,r.type_coeff.type_coeff_name,r.segment.segment_name.upper().replace('O','K')):r.motivation_coeff for r in SubdivisionCoeff.objects.filter(version=version).select_related('type_coeff','segment')}
            for row in plan['rows']:row['before']=[existing.get((row['branch_id'],row['goods_id'],row['kind'],'K'+str(i))) for i in range(5)]
            payload={'actor':request.user.pk,'version':version.pk,'revision':version.revision,'rows':rows,'scope':scope,'mappings':mappings,'targets':targets(plan)}
            data.update(plan,preview_token=signing.dumps(payload,salt=SALT,compress=True),preview=True)
    except (ValidationError,signing.BadSignature,ValueError) as exc:
        data['error']='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Предварительный просмотр устарел или повреждён. Загрузите файл заново.'
    return render(request,'admin_motivation/core_coeff_import.html',data)


def action(request):
    return page(request,get_object_or_404(GlobalCoeffVersion,pk=request.POST.get('version')))
