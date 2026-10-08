from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.forms import inlineformset_factory
from .models import PGGoods, ScenarioModel, ScenarioPlanSales, Freight


class PGGoodsCopyForm(forms.Form):
    scenario = forms.ModelChoiceField(
        queryset=ScenarioModel.objects.all(), label='Целевой сценарий',
        error_messages={'required': 'Не выбран целевой сценарий.',
                        'invalid_choice': 'Целевой сценарий не найден.'},
    )
    source_scenario_id = forms.ModelChoiceField(
        queryset=ScenarioModel.objects.all(), label='Сценарий-источник',
        error_messages={'required': 'Не выбран сценарий-источник.',
                        'invalid_choice': 'Сценарий-источник не найден.'},
    )

    def clean(self):
        data = super().clean()
        target = data.get('scenario')
        source = data.get('source_scenario_id')
        if target and source and target.pk == source.pk:
            raise ValidationError('Источник и целевой сценарий совпадают.')
        return data


class PGGoodsEditForm(forms.ModelForm):
    class Meta:
        model = PGGoods
        fields = ['purch', 'planning_sales', 'group_goods', 'brand', 'kind_purch',
                  'volume', 'container_volume', 'duty_rate', 'exw_usd', 'stock_cnt_day']
        labels = {'purch': _('Закупка'), 'planning_sales': _('Группа планов продаж'),
                  'group_goods': _('Группа товаров'), 'brand': _('Бренд'), 'kind_purch': _('Вид закупки'),
                  'volume': _('Объём'), 'container_volume': _('Объём контейнера, м³'),
                  'duty_rate': _('Пошлина, %'), 'exw_usd': _('Цена EXW (USD)'), 'stock_cnt_day': _('Запас в днях')}
        widgets = {
            'purch': forms.TextInput(attrs={'class': 'form-control', 'list': 'purchase-options'}),
            'planning_sales': forms.TextInput(attrs={'class': 'form-control'}),
            'group_goods': forms.TextInput(attrs={'class': 'form-control', 'list': 'goods-group-options'}),
            'brand': forms.TextInput(attrs={'class': 'form-control'}),
            'kind_purch': forms.Select(attrs={'class': 'form-select'}),
            'volume': forms.NumberInput(attrs={'step': 'any', 'min': '0.000001', 'class': 'form-control'}),
            'container_volume': forms.NumberInput(attrs={'step': 'any', 'min': '0.001', 'class': 'form-control'}),
            'duty_rate': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'class': 'form-control'}),
            'exw_usd': forms.NumberInput(attrs={'step': 'any', 'min': '0', 'class': 'form-control'}),
            'stock_cnt_day': forms.NumberInput(attrs={'min': '0', 'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.fields:
            if self.is_bound and self.add_prefix(name) not in self.data:
                self.fields[name].required = False
        self.fields['brand'].required = False
        self.fields['purch'].required = False

    def clean(self):
        cleaned = super().clean()
        for name in self.fields:
            if self.add_prefix(name) not in self.data:
                cleaned[name] = getattr(self.instance, name)
        for name, label, positive in [('volume', 'Объём', True), ('container_volume', 'Объём контейнера', True),
                                      ('exw_usd', 'Цена EXW', False), ('stock_cnt_day', 'Запас в днях', False)]:
            value = cleaned.get(name)
            if value is not None and (value <= 0 if positive else value < 0):
                self.add_error(name, f'{label} должен быть больше нуля.' if positive else f'{label} не может быть отрицательным.')
        if self.add_prefix('duty_rate') in self.data and cleaned.get('duty_rate') is None and 'duty_rate' not in self.errors:
            self.add_error('duty_rate', 'Укажите пошлину; если она не применяется, введите 0.')
        if cleaned.get('group_goods') != self.instance.group_goods and self.add_prefix('duty_rate') not in self.data:
            from .models import GoodsGroup
            from .goods_identity import planning_group_key
            group = GoodsGroup.objects.filter(name_key=planning_group_key(cleaned.get('group_goods') or '')).first()
            cleaned['duty_rate'] = group.duty_rate if group else 0
        return cleaned


# ==========================================
# ФОРМЫ ДЛЯ СЦЕНАРИЕВ
# ==========================================
class ScenarioModelForm(forms.ModelForm):
    class Meta:
        model = ScenarioModel
        fields = ['name', 'date_start_plan', 'date_end_plan', 'overwrite_existing']
        labels = {
            'name': _('Название сценария'),
            'date_start_plan': _('Дата начала'),
            'date_end_plan': _('Дата окончания'),
            'overwrite_existing': _('Перезаписывать существующие данные'),
        }
        widgets = {
            'is_russian': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Введите название сценария'}),
            'date_start_plan': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'date_end_plan': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'overwrite_existing': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        date_start = cleaned_data.get('date_start_plan')
        date_end = cleaned_data.get('date_end_plan')
        if date_start and date_end and date_end < date_start:
            self.add_error('date_end_plan', _('Дата окончания не может быть раньше даты начала.'))
        return cleaned_data

class ScenarioPlanSalesForm(forms.ModelForm):
    class Meta:
        model = ScenarioPlanSales
        fields = ['name', 'flag_order_in_purch']
        labels = {
            'name': _('Название плана'),
            'flag_order_in_purch': _('Заказ в закупке'),
        }
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Название плана продаж'}),
            'flag_order_in_purch': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


ScenarioPlanSalesFormSet = inlineformset_factory(
    ScenarioModel,
    ScenarioPlanSales,
    form=ScenarioPlanSalesForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True
)


from django import forms
from django.forms import inlineformset_factory
from .models import Purch, PurchPay, KindLagPay

from django import forms
from .models import Purch, PurchPay, ScenarioModel, KindLagPay


class PurchForm(forms.ModelForm):
    class Meta:
        model = Purch
        fields = ['name', 'lag_income', 'lage_make', 'is_russian']
        labels = {
            'name': 'Название закупки',
            'lag_income': 'Лаг доставки (дней)',
            'lage_make': 'Лаг производства (дней)',
            'is_russian': 'Поставщик РФ',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Введите название закупки'
            }),
            'lag_income': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0'
            }),
            'lage_make': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0'
            }),
        }


class PurchPayForm(forms.ModelForm):
    # Выпадающий список из модели KindLagPay
    kind_lag_pay = forms.ModelChoiceField(
        queryset=KindLagPay.objects.all(),
        label='Вид лага платежа',
        required=False,
        empty_label='— Выберите вид —',
        widget=forms.Select(attrs={
            'class': 'form-select form-select-sm'
        })
    )

    class Meta:
        model = PurchPay
        fields = ['name', 'percent_pay', 'lag_day_pay', 'kind_lag_pay']
        labels = {
            'name': 'Название платежа',
            'percent_pay': '% оплаты',
            'lag_day_pay': 'Лаг платежа (дней)',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control form-control-sm',
                'placeholder': 'Название платежа'
            }),
            'percent_pay': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm',
                'step': '0.01',
                'min': '0',
                'max': '100'
            }),
            'lag_day_pay': forms.NumberInput(attrs={
                'class': 'form-control form-control-sm',
                'min': '0'
            }),
        }


# Formset для графиков платежей
PurchPayFormSet = forms.inlineformset_factory(
    Purch,
    PurchPay,
    form=PurchPayForm,
    extra=1,
    can_delete=True,
    max_num=10,
    validate_max=True
)

class FreightForm(forms.ModelForm):
    class Meta:
        model = Freight
        fields = ['price_per_container', 'customs_rate', 'warehouse_delivery_cost']
        labels = {'price_per_container': 'Цена фрахта за контейнер, USD', 'warehouse_delivery_cost': 'Стоимость доставки до склада, USD'}
        widgets = {
            'price_per_container': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
            'customs_rate': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'max': '100', 'step': '0.01'}),
            'warehouse_delivery_cost': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'step': '0.01'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Older callers can still submit only the freight price without resetting the new settings.
        for name in ('customs_rate', 'warehouse_delivery_cost'):
            self.fields[name].required = False

    def clean_customs_rate(self):
        return self._clean_setting('customs_rate')

    def clean_warehouse_delivery_cost(self):
        return self._clean_setting('warehouse_delivery_cost')

    def _clean_setting(self, name):
        if self.add_prefix(name) not in self.data:
            return getattr(self.instance, name)
        value = self.cleaned_data.get(name)
        if value is None:
            raise ValidationError(_('Укажите значение; если параметр не применяется, введите 0.'))
        return value


class FreightScenarioForm(forms.Form):
    scenario = forms.ModelChoiceField(queryset=ScenarioModel.objects.all())


class FreightCopyForm(PGGoodsCopyForm):
    pass


class ScenarioBulkExportForm(forms.Form):
    scenarios = forms.ModelMultipleChoiceField(
        queryset=ScenarioModel.objects.all(),
        error_messages={'required': 'Выберите хотя бы один сценарий.',
                        'invalid_choice': 'Один из выбранных сценариев не найден. Обновите страницу.'},
    )


class GoodsGroupForm(forms.ModelForm):
    class Meta:
        from .models import GoodsGroup
        model = GoodsGroup
        fields = ['name', 'duty_rate']
        widgets = {'name': forms.TextInput(attrs={'class': 'form-control'}),
                   'duty_rate': forms.NumberInput(attrs={'class': 'form-control', 'min': '0', 'max': '100', 'step': '0.01'})}
