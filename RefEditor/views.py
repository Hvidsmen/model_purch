# RefEditor/views.py
import logging
from django.contrib import messages
from django.shortcuts import render

from .forms import (
    StoreGroupFilterForm,
    StoreGroupFileForm,
    StoreGroupAddForm,
    GroupOZPCreateForm,
    SubGroupOZPCreateForm,
)
from .models import StoreGroupOZP, Subdivision, GroupOZP, SubGroupOZP, Reference
from .services import (
    StoreGroupImportService,
    StoreGroupImportError,
    ReferenceUpsertService,
)

logger = logging.getLogger(__name__)

ACTION_HANDLERS = {}


def action(name):
    def decorator(func):
        ACTION_HANDLERS[name] = func
        return func
    return decorator


# ==================== ОБРАБОТЧИКИ POST-ДЕЙСТВИЙ ====================

@action('upload file')
def _handle_upload(request, context):
    form = StoreGroupFileForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, 'Неверный формат файла')
        return
    try:
        count = StoreGroupImportService.import_from_excel(request.FILES['file'])
        messages.success(request, f'Успешно импортировано записей: {count}')
        StoreGroupOZP.transfer_dwh()
    except StoreGroupImportError as e:
        messages.error(request, f'Ошибка импорта: {e}')


@action('add group ozp')
def _handle_add_group(request, context):
    form = GroupOZPCreateForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Ошибка валидации формы группы ОЗП')
        return
    try:
        ReferenceUpsertService.upsert_group_ozp(form.cleaned_data['group_ozp_name'])
        messages.success(request, 'Группа ОЗП добавлена')
    except ValueError as e:
        messages.error(request, str(e))


@action('add sub group ozp')
def _handle_add_sub_group(request, context):
    form = SubGroupOZPCreateForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Ошибка валидации формы подгруппы ОЗП')
        return
    try:
        ReferenceUpsertService.upsert_sub_group_ozp(
            form.cleaned_data['sub_group_ozp_name']
        )
        messages.success(request, 'Подгруппа ОЗП добавлена')
    except ValueError as e:
        messages.error(request, str(e))


@action('add')
def _handle_add_store(request, context):
    form = StoreGroupAddForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Ошибка валидации формы склада')
        return
    try:
        d = form.cleaned_data
        ReferenceUpsertService.upsert_store(
            store_name=d['store_name'],
            subdivision_id=d['subdivision_id'],
            group_ozp_id=d['group_ozp_id'],
            sub_group_ozp_id=d['sub_group_ozp_id'],
        )
        messages.success(request, 'Склад добавлен/обновлён')
        StoreGroupOZP.transfer_dwh()
    except Exception as e:
        messages.error(request, f'Ошибка сохранения: {e}')


@action('edit')
def _handle_edit(request, context):
    updated = deleted = 0
    for store in StoreGroupOZP.objects.all():
        store_key = f'store_id = {store.id}'
        if store_key not in request.POST:
            continue
        try:
            if f'delete {store_key}' in request.POST:
                store.delete()
                deleted += 1
                continue

            store.subdivision_id = request.POST[f'subdivision_id {store_key}']
            store.group_ozp_id = request.POST[f'group_ozp_id {store_key}']
            store.sub_group_ozp_id = request.POST[f'sub_group_ozp_id {store_key}']
            store.save()
            updated += 1
        except Exception as e:
            messages.error(request, f'Ошибка обновления склада #{store.id}: {e}')

    messages.success(request, f'Обновлено: {updated}, удалено: {deleted}')
    StoreGroupOZP.transfer_dwh()


# ==================== ПРИМЕНЕНИЕ ФИЛЬТРОВ (GET) ====================

def _apply_filters(request) -> tuple:
    """
    Применяет фильтры из request.GET.
    Возвращает (queryset, bound_form).
    """
    form = StoreGroupFilterForm(request.GET or None)

    # Если параметров нет вообще — считаем форму несвязанной
    if not request.GET:
        return StoreGroupOZP.objects.all(), StoreGroupFilterForm()

    if not form.is_valid():
        # При ошибке валидации игнорируем фильтры, но показываем форму с ошибками
        return StoreGroupOZP.objects.all(), form

    d = form.cleaned_data
    qs = StoreGroupOZP.objects.all()

    if d.get('store_name'):
        qs = qs.filter(store_name__icontains=d['store_name'])
    if d.get('subdivision_id'):
        qs = qs.filter(subdivision_id=d['subdivision_id'])
    if d.get('group_ozp_id'):
        qs = qs.filter(group_ozp_id=d['group_ozp_id'])
    if d.get('sub_group_ozp_id'):
        qs = qs.filter(sub_group_ozp_id=d['sub_group_ozp_id'])

    return qs, form

def ref_store_group(request):
    context = _build_base_context()

    if request.method == 'POST':
        action_name = request.POST.get('action')
        handler = ACTION_HANDLERS.get(action_name)
        if handler:
            handler(request, context)

        # Восстанавливаем фильтры из сессии
        filter_get = request.session.get('store_filters', {})
        stores, filter_form = _apply_filters_from_dict(filter_get)
        context['stores'] = stores
        context['form_filter'] = filter_form

    else:
        # Сохраняем текущие GET-параметры в сессию
        if request.GET:
            request.session['store_filters'] = request.GET.dict()
        stores, filter_form = _apply_filters(request)
        context['stores'] = stores
        context['form_filter'] = filter_form

    return render(request, 'RefEditor/ref_store_ozp.html', context)


def _apply_filters_from_dict(data: dict) -> tuple:
    """Вариант _apply_filters, принимающий словарь вместо QueryDict."""
    form = StoreGroupFilterForm(data or None)
    if not data:
        return StoreGroupOZP.objects.all(), StoreGroupFilterForm()
    if not form.is_valid():
        return StoreGroupOZP.objects.all(), form

    d = form.cleaned_data
    qs = StoreGroupOZP.objects.all()
    if d.get('store_name'):
        qs = qs.filter(store_name__icontains=d['store_name'])
    if d.get('subdivision_id'):
        qs = qs.filter(subdivision_id=d['subdivision_id'])
    if d.get('group_ozp_id'):
        qs = qs.filter(group_ozp_id=d['group_ozp_id'])
    if d.get('sub_group_ozp_id'):
        qs = qs.filter(sub_group_ozp_id=d['sub_group_ozp_id'])
    return qs, form

def _build_base_context() -> dict:
    reference = Reference.objects.filter(id=1).first()
    return {
        'forms_file': StoreGroupFileForm(),
        'forms_add': StoreGroupAddForm(),
        'forms_add_group_ozp': GroupOZPCreateForm(),
        'forms_add_sub_group_ozp': SubGroupOZPCreateForm(),
        'subdivisions': Subdivision.objects.all(),
        'sub_group_ozps': SubGroupOZP.objects.all(),
        'group_ozps': GroupOZP.objects.all(),
        'templates_file': reference.example_file if reference else None,
    }


# RefEditor/views.py
import pandas as pd
from django.http import HttpResponse
from django.db.models import F


def export_stores_to_excel(request):
    """Экспорт складов в Excel с учётом фильтров."""
    # Применяем фильтры (аналогично _apply_filters)
    from .forms import StoreGroupFilterForm
    from .models import StoreGroupOZP

    form = StoreGroupFilterForm(request.GET or None)

    if not request.GET:
        stores = StoreGroupOZP.objects.all()
    elif form.is_valid():
        d = form.cleaned_data
        stores = StoreGroupOZP.objects.all()

        if d.get('store_name'):
            stores = stores.filter(store_name__icontains=d['store_name'])
        if d.get('subdivision_id'):
            stores = stores.filter(subdivision_id=d['subdivision_id'])
        if d.get('group_ozp_id'):
            stores = stores.filter(group_ozp_id=d['group_ozp_id'])
        if d.get('sub_group_ozp_id'):
            stores = stores.filter(sub_group_ozp_id=d['sub_group_ozp_id'])
    else:
        stores = StoreGroupOZP.objects.all()

    # Создаём DataFrame
    df_data = []
    for store in stores:
        df_data.append({
            'Подразделение': store.subdivision.subdivision_name if store.subdivision else '',
            'Склад': store.store_name,
            'Группа ОЗП': store.group_ozp.group_ozp_name if store.group_ozp else '',
            'Подгруппа ОЗП': store.sub_group_ozp.sub_group_ozp_name if store.sub_group_ozp else '',
        })

    df = pd.DataFrame(df_data)

    # Создаём Excel файл в памяти
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = 'attachment; filename=stores_export.xlsx'

    with pd.ExcelWriter(response, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Загрузка', index=False)

    return response