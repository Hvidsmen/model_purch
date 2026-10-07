from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django.forms import inlineformset_factory
from .models import PGGoods, ScenarioModel, ScenarioPlanSales


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
        fields = [
            'volume', 'exw_usd', 'ddp_usd', 'kddp',
            'stock_cnt_day', 'percent_stock_end',
            'brand', 'purch',
        ]
        labels = {
            'volume': _('Объём'),
            'exw_usd': _('Цена EXW (USD)'),
            'ddp_usd': _('Цена DDP (USD)'),
            'kddp': _('Коэффициент KDDP'),
            'stock_cnt_day': _('Запас в днях'),
            'percent_stock_end': _('% запаса на конец'),
            'brand': _('Бренд'),
            'purch': _('Закупка'),
        }
        widgets = {
            'volume': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control'}),
            'exw_usd': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control'}),
            'ddp_usd': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'class': 'form-control'}),
            'kddp': forms.NumberInput(attrs={'step': '0.0001', 'min': '0', 'class': 'form-control', 'readonly': True}),
            'stock_cnt_day': forms.NumberInput(attrs={'min': '0', 'class': 'form-control'}),
            'percent_stock_end': forms.NumberInput(attrs={'step': '0.01', 'min': '0', 'max': '100', 'class': 'form-control'}),
            'brand': forms.TextInput(attrs={'class': 'form-control'}),
            'purch': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def clean_volume(self):
        v = self.cleaned_data.get('volume')
        if v is not None and v <= 0:
            raise ValidationError(_('Объём должен быть больше нуля.'))
        return v

    def clean_exw_usd(self):
        v = self.cleaned_data.get('exw_usd')
        if v is not None and v < 0:
            raise ValidationError(_('Цена EXW не может быть отрицательной.'))
        return v

    def clean_ddp_usd(self):
        v = self.cleaned_data.get('ddp_usd')
        if v is not None and v < 0:
            raise ValidationError(_('Цена DDP не может быть отрицательной.'))
        return v

    def clean_stock_cnt_day(self):
        v = self.cleaned_data.get('stock_cnt_day')
        if v is not None and v < 0:
            raise ValidationError(_('Запас в днях не может быть отрицательным.'))
        return v

    def clean_percent_stock_end(self):
        v = self.cleaned_data.get('percent_stock_end')
        if v is not None and (v < 0 or v > 100):
            raise ValidationError(_('Процент запаса должен быть от 0 до 100.'))
        return v

    def clean(self):
        cleaned_data = super().clean()
        exw = cleaned_data.get('exw_usd')
        ddp = cleaned_data.get('ddp_usd')
        if exw is not None and ddp is not None and ddp < exw:
            self.add_error('ddp_usd', _('Цена DDP обычно не может быть меньше цены EXW.'))
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.exw_usd and instance.exw_usd > 0 and instance.ddp_usd is not None:
            instance.kddp = instance.ddp_usd / instance.exw_usd
        if commit:
            instance.save()
        return instance


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
        # Убрали scenario_plan - он устанавливается во view из URL
        fields = ['name', 'lag_income', 'lage_make']
        labels = {
            'name': 'Название закупки',
            'lag_income': 'Лаг доставки (дней)',
            'lage_make': 'Лаг производства (дней)',
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