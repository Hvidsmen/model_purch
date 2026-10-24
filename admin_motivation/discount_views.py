from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms import BaseModelFormSet, modelformset_factory
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from .models import DiscountSegment
from .operation_views import admin_required
from .services.directors import lock_editable
from .services.version_context import version_context


class SegmentForm(forms.ModelForm):
    class Meta:
        model = DiscountSegment
        fields = ['type_discount', 'o0', 'o1', 'o2', 'o3', 'o4']
        widgets = {name: forms.NumberInput(attrs={'step': '.0001', 'min': '0', 'max': '100', 'placeholder': 'NULL'}) for name in ['o0', 'o1', 'o2', 'o3', 'o4']}


class SegmentFormSet(BaseModelFormSet):
    def clean(self):
        super().clean()
        names = []
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get('DELETE'):
                continue
            name = form.cleaned_data.get('type_discount', '').strip().casefold()
            if name:
                names.append(name)
        if not names:
            raise ValidationError('Оставьте хотя бы один тип скидки. Строки со всеми пустыми порогами допустимы.')
        if len(names) != len(set(names)):
            raise ValidationError('Типы скидок в версии не должны повторяться.')


@require_http_methods(['GET', 'POST'])
def discount_segments(request):
    admin_required(request)
    context = version_context(request)
    version = context['selected_version']
    if version is None and not context['versions']:
        return render(request, 'admin_motivation/approved_empty.html', context)
    editable = context['version_editable']
    factory = modelformset_factory(DiscountSegment, form=SegmentForm, formset=SegmentFormSet, extra=1 if editable else 0, can_delete=editable)
    kwargs = {'queryset': DiscountSegment.objects.filter(version=version), 'prefix': 'segments'}
    if request.method == 'POST' and request.POST.get('action') == 'import':
        from .conns import connect_database
        from .services.discount_segments import import_missing_versions
        try:
            count, rows = import_missing_versions(connect_database)
            messages.success(request, f'Загружено версий: {count}, типов скидок: {rows}. Заполненные версии сохранены без изменений.')
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Discount segment import failed')
            messages.error(request, 'Не удалось загрузить сегменты. Проверьте подключение к DataWH и права чтения SegmentMotivation.')
        return redirect(reverse('motivation_discount_segments') + (f'?version={version.pk}' if version else ''))
    formset = factory(request.POST if request.method == 'POST' else None, **kwargs)
    for form in formset:
        form.instance.version = version
    if request.method == 'POST':
        try:
            with transaction.atomic():
                locked = lock_editable(version.pk)
                if str(locked.revision) != request.POST.get('revision'):
                    raise ValidationError('Версия изменилась. Обновите страницу перед сохранением.')
                if formset.is_valid():
                    formset.save()
                    messages.success(request, 'Сегменты скидок сохранены в черновике. Для выгрузки утвердите общую версию коэффициентов.')
                    return redirect(reverse('motivation_discount_segments') + f'?version={version.pk}')
        except ValidationError as error:
            messages.error(request, ' '.join(error.messages))
    return render(request, 'admin_motivation/discount_segments.html', {**context, 'formset': formset})
