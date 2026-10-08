from django import forms


class GlobalCoeffVersionForm(forms.Form):
    effective_from = forms.DateField(label='Дата начала действия', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    title = forms.CharField(label='Название версии', max_length=255, required=False)
    source_version = forms.IntegerField(widget=forms.HiddenInput)
