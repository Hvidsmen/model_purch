from django import forms


class GlobalCoeffVersionForm(forms.Form):
    effective_from = forms.DateField(label='Дата начала действия', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    title = forms.CharField(label='Название версии', max_length=255, required=False)
    source_version = forms.IntegerField(widget=forms.HiddenInput)
    allow_overwrite = forms.BooleanField(required=False, label='Разрешить дату не позже последней версии; замена только при утверждении')


class SalesPlanScenarioForm(forms.Form):
    title = forms.CharField(label='Название сценария', max_length=255)
    source_version = forms.CharField(label='Версия плана в MS SQL', max_length=255,
                                   widget=forms.TextInput(attrs={'list': 'source-versions'}))
