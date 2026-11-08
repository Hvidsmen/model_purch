from datetime import date, timedelta
from django import forms
from .models import SalesPlanningChannel, SalesPlanningGroup
from .sales_planning_scope import split_groups


class ScenarioForm(forms.Form):
    title = forms.CharField(label='Название сценария', max_length=200)
    year = forms.IntegerField(label='Год', min_value=2000, max_value=2100)
    mode = forms.ChoiceField(label='Период', choices=[('year', 'Новый год целиком'), ('remaining', 'Остаток года')])
    start = forms.DateField(label='Дата начала для остатка года', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    stock_date = forms.DateField(label='Дата исторического остатка', required=False,
        help_text='Если не указана — день перед началом плана. Остатки вводятся вручную на эту дату.', widget=forms.DateInput(attrs={'type': 'date'}))
    entry_mode = forms.ChoiceField(label='Объём', choices=[('period', 'На плановый период'), ('annual', 'Годовая цель минус состоявшийся факт')])
    groups = forms.ModelMultipleChoiceField(label='Группы планирования', queryset=SalesPlanningGroup.objects.none())
    channels = forms.ModelMultipleChoiceField(label='Каналы продаж', queryset=SalesPlanningChannel.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['groups'].queryset = split_groups().filter(is_active=True)
        self.fields['channels'].queryset = SalesPlanningChannel.objects.filter(is_active=True)
        for field in ('groups', 'channels'):
            self.fields[field].help_text = 'Можно выбрать несколько значений (Ctrl / Cmd).'

    def clean(self):
        result = super().clean()
        year = result.get('year')
        if year:
            start = date(year, 1, 1) if result.get('mode') == 'year' else result.get('start')
            if start is None or start.year != year:
                self.add_error('start', 'Укажите дату начала в выбранном году.')
            else:
                result['start'], result['end'] = start, date(year, 12, 31)
                stock_date = result.get('stock_date') or start - timedelta(days=1)
                if stock_date >= start or (start - stock_date).days > 1096:
                    self.add_error('stock_date', 'Дата остатка должна быть до начала плана, не более чем за три года.')
                result['stock_date'] = stock_date
        return result
