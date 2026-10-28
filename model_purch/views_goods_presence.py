import logging
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from .models import PGGoods
from .services.goods_presence import check_goods_presence

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
    result, error = None, ''
    if request.GET.get('check') == '1':
        try:
            result = check_goods_presence(good)
        except ValidationError as exception:
            error = ' '.join(exception.messages)
        except Exception:
            logger.exception('Goods presence check failed for goods %s', pk)
            error = 'Не удалось проверить источники. Отсутствие товара не подтверждено. Подробности ошибки записаны в журнал сервера.'
    return render(request, 'model_purch/goods_presence.html', {'good': good, 'result': result, 'error': error})
