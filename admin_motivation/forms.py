from django import forms
from .models import GlobalCoeffVersion


class GlobalCoeffVersionForm(forms.Form):
    effective_from = forms.DateField(label='Дата начала действия', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    title = forms.CharField(label='Название версии', max_length=255, required=False)
    source_version = forms.IntegerField(widget=forms.HiddenInput)
    fallback_variation = forms.ChoiceField(label='Формула без точной настройки', choices=GlobalCoeffVersion._meta.get_field('fallback_variation').choices, required=False, initial='Подразделение(Глобально)')

    def clean_fallback_variation(self):
        return self.cleaned_data.get('fallback_variation') if 'fallback_variation' in self.data else None

    allow_overwrite = forms.BooleanField(required=False, label='Разрешить дату не позже последней версии; замена только при утверждении')


class SalesPlanScenarioForm(forms.Form):
    source_type = forms.ChoiceField(label='Источник плана', choices=[('planning','DataWH.planning.PlanSales'),('erp','DataWH.erp.РегистрыНакопления.ПланыПродаж')], initial='planning', required=False)

    def clean_source_type(self):
        return self.cleaned_data.get('source_type') or 'planning'

    title = forms.CharField(label='Название сценария', max_length=255)
    source_version = forms.CharField(label='Версия плана в MS SQL', max_length=255,
                                   widget=forms.TextInput(attrs={'list': 'source-versions'}))
