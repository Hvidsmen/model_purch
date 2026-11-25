import logging
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from base.access import role_for,ADMIN,LOGISTICIAN
from .models import ScenarioModel
from .services import goods_fill

logger=logging.getLogger(__name__)
SALT='purchase-goods-fill-v1'
LABELS={'physical':'Обновить объём и массу','plan':'Заполнить EXW из плана','fact':'Заполнить EXW из факта'}

@require_http_methods(['GET','POST'])
def fill(request,scenario_id):
    if role_for(request.user,'purchases') not in (ADMIN,LOGISTICIAN):raise PermissionDenied
    scenario=get_object_or_404(ScenarioModel,pk=scenario_id)
    data=request.POST if request.method=='POST' else request.GET
    source=data.get('source','physical');mode=data.get('mode','all');ids=data.getlist('selected_ids')
    context={'current_scenario':scenario,'source':source,'mode':mode,'ids':ids,'title':LABELS.get(source,'Заполнение товаров')}
    try:
        if source not in LABELS or mode not in ('all','visible','missing'):raise ValidationError('Выберите источник и режим.')
        if request.method=='POST' and data.get('action')=='apply':
            payload=signing.loads(data.get('token',''),salt=SALT,max_age=1800)
            if payload['actor']!=request.user.pk or payload['scenario']!=scenario.pk:raise PermissionDenied
            if (payload['source'],payload['mode'])!=(source,mode):raise ValidationError('Режим изменился. Повторите просмотр.')
            if data.get('confirm')!='yes':raise ValidationError('Подтвердите замену значений после просмотра.')
            count=goods_fill.apply(scenario.pk,payload)
            messages.success(request,f'{LABELS[source]}: обновлено товаров {count}.')
            return redirect(reverse('pggoods_list')+f'?scenario={scenario.pk}')
        if request.method=='POST':
            result=goods_fill.preview(scenario,source,mode,ids)
            context.update({key:value for key,value in result.items() if key!='ids'},preview=True)
            payload={key:result[key] for key in ('updates','digest','ids')}
            payload.update(actor=request.user.pk,scenario=scenario.pk,source=source,mode=mode)
            context['token']=signing.dumps(payload,salt=SALT,compress=True)
    except (ValidationError,signing.BadSignature) as exc:
        context['error']='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Просмотр устарел. Рассчитайте его заново.'
    except PermissionDenied:raise
    except Exception:
        logger.exception('Goods parameter fill failed for scenario %s',scenario.pk)
        context['error']='Не удалось подготовить или сохранить заполнение. Изменения не сохранены; проверьте подключение к ERP и журнал сервера.'
    return render(request,'model_purch/goods_fill.html',context)
