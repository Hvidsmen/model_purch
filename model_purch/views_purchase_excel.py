import logging
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_POST
from .services.purchase_excel import export_workbook, import_workbook

logger = logging.getLogger(__name__)


@require_GET
def export_purchases(request):
    response = HttpResponse(export_workbook(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="purchases.xlsx"'
    return response


@require_POST
def import_purchases(request):
    try:
        if request.POST.get('confirm') != 'yes':
            raise ValidationError('Подтвердите обновление общего справочника закупок и графиков платежей.')
        count, invalid = import_workbook(request.FILES.get('file'))
        messages.success(request, f'Загружено закупок: {count}. Графики платежей заменены по файлу. Подготовьте изменённые данные на странице расчёта.')
        if invalid:
            messages.warning(request, f'Не пересчитаны товары с некорректными исходными данными: {invalid}.')
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
    except Exception:
        logger.exception('Purchase Excel import failed')
        messages.error(request, 'Не удалось загрузить Excel. Изменения не сохранены. Проверьте формат файла и журнал сервера.')
    return redirect('purch_list')
