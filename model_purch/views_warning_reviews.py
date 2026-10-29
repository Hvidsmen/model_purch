from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from .models import PGGoods, GoodsGroup, GoodsWarningReview
from .services.warning_reviews import warning_details


@require_POST
@transaction.atomic
def review_warning(request, pk):
    good = get_object_or_404(PGGoods.objects.select_for_update(), pk=pk, scenario_plan_id=request.POST.get('scenario'))
    threshold = request.session.get('ddp_threshold', 10)
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        threshold = 10
    duties = {group.name_key: group.duty_rate for group in GoodsGroup.objects.all()}
    warning = next((item for item in warning_details(good, threshold, duties)
                    if item['kind'] == request.POST.get('kind') and item['token'] == request.POST.get('token')), None)
    if warning is None:
        messages.warning(request, 'Данные предупреждения изменились. Проверьте его повторно.')
    elif request.POST.get('action') == 'reset':
        GoodsWarningReview.objects.filter(good=good, kind=warning['kind']).delete()
    else:
        GoodsWarningReview.objects.update_or_create(good=good, kind=warning['kind'], defaults={
            'fingerprint': warning['token'], 'reviewed_by': request.user})
    from urllib.parse import parse_qs, urlencode
    params = parse_qs(request.POST.get('query', ''), keep_blank_values=True)
    params['scenario'] = [str(good.scenario_plan_id)]
    return redirect(reverse('pggoods_list') + '?' + urlencode(params, doseq=True))
