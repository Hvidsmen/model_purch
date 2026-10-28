import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import ScenarioModel
from .services.goods_directory import refresh_goods_directory

logger = logging.getLogger(__name__)


@require_POST
def refresh_directory(request, scenario_id):
    scenario = get_object_or_404(ScenarioModel, pk=scenario_id)
    try:
        counts = refresh_goods_directory(scenario)
        messages.success(request, (
            f'Справочник товаров сценария «{scenario.name}» обновлён. '
            f'Добавлено: {counts["added"]}; Изменено: {counts["updated"]}; без изменений: {counts["unchanged"]}; '
            f'не найдено в источнике: {counts["missing"]}.'
        ))
        if counts['added']:
            messages.info(request, 'У новых товаров EXW = 0: заполните цену и проверьте поставщика, объём, пошлину и запас в днях. Затем обновите параметры из плана и повторите экспорт сценария в MS SQL.')
        from .services.erp_plan_parameters import attached_sources
        if not attached_sources(scenario):
            messages.info(request, 'Новые товары не добавлялись: в настройках сценария не выбраны планы продаж. Классификация существующих товаров обновлена.')
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
    except Exception:
        logger.exception('Goods directory refresh failed for scenario %s', scenario.pk)
        messages.error(request, 'Не удалось обновить справочник товаров. Проверьте подключение MS_SQL_CONN_STR и журнал сервера. Изменения не сохранены.')
    return redirect(reverse('pggoods_list') + f'?scenario={scenario.pk}')
