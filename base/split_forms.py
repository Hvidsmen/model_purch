from django import forms


class ScenarioForm(forms.Form):
    title = forms.CharField(label='Название сценария', max_length=200)
    start = forms.DateField(label='Дата начала планирования', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    end = forms.DateField(label='Дата окончания планирования', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    stock_date = forms.DateField(label='Дата фактического стока',
        help_text='На эту дату берём исходные остатки. Не позднее начала планирования.',
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))

    def clean(self):
        result = super().clean()
        start, end, stock_date = (result.get(field) for field in ('start', 'end', 'stock_date'))
        if start and end and end < start:
            self.add_error('end', 'Окончание периода не может быть раньше начала.')
        if start and stock_date and stock_date > start:
            self.add_error('stock_date', 'Дата фактического стока не может быть позже начала планирования.')
        return result
