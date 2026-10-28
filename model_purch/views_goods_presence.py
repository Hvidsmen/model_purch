import logging
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from .models import PGGoods
from .services.goods_presence import check_goods_presence, presence_cache_key
from django.core.cache import cache

logger = logging.getLogger(__name__)


@never_cache
@require_GET
def goods_presence(request, pk):
    good = get_object_or_404(PGGoods.objects.select_related('scenario_plan'), pk=pk,
                             scenario_plan__isnull=False, scenario_plan__archived_at__isnull=True)
    selected = request.GET.get('scenario')
    if selected is not None:
        try:
            if int(selected) != good.scenario_plan_id:
                raise ValueError
        except ValueError:
            raise Http404('Товар не относится к выбранному сценарию.')
    from .services.scenarios import remember_scenario
    remember_scenario(request, good.scenario_plan)
    result, error, from_cache = None, '', False
    include_cost = request.GET.get('detailed') == '1'
    if request.GET.get('check') == '1':
        try:
            key = presence_cache_key(good, include_cost)
            result = None if request.GET.get('fresh') == '1' else cache.get(key)
            from_cache = result is not None
            if result is None:
                result = check_goods_presence(good, include_cost=include_cost)
                cache.set(presence_cache_key(good, include_cost, result['plans']), result, timeout=120)
        except ValidationError as exception:
            error = ' '.join(exception.messages)
        except Exception:
            logger.exception('Goods presence check failed for goods %s', pk)
            error = 'Не удалось проверить источники. Отсутствие товара не подтверждено. Подробности ошибки записаны в журнал сервера.'
    return render(request, 'model_purch/goods_presence.html', {'good': good, 'result': result, 'error': error, 'include_cost': include_cost, 'from_cache': from_cache})
