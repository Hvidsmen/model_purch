from django import forms
from .models import SplitScenario


class ScenarioForm(forms.Form):
    title = forms.CharField(label='Название сценария', max_length=200)
    start = forms.DateField(label='Дата начала планирования', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    end = forms.DateField(label='Дата окончания планирования', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    stock_date = forms.DateField(label='Дата фактического стока',
        help_text='На эту дату берём исходные остатки. Не позднее начала планирования.',
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))

    copy_source = forms.ModelChoiceField(label='Скопировать из сценария', queryset=SplitScenario.objects.none(), required=False, empty_label='Не копировать', help_text='Настройки и продажи переносятся. При другой дате стока остатки, прогноз и закупки пересчитываются.')

    def __init__(self,*args,actor=None,**kwargs):
        super().__init__(*args,**kwargs)
        if actor is not None:
            from .models import SplitScenario
            from .sales_planning_access import scenarios_for
            self.fields['copy_source'].queryset=scenarios_for(actor,SplitScenario.objects.all()).only('pk','title','version','stock_date','start').order_by('-pk')
            self.fields['copy_source'].label_from_instance=lambda obj:f'{obj.title} · v{obj.version} · сток {obj.stock_date:%d.%m.%Y}'

    def clean(self):
        result = super().clean()
        start, end, stock_date = (result.get(field) for field in ('start', 'end', 'stock_date'))
        if start and end and end < start:
            self.add_error('end', 'Окончание периода не может быть раньше начала.')
        if start and stock_date and stock_date > start:
            self.add_error('stock_date', 'Дата фактического стока не может быть позже начала планирования.')
        source=result.get('copy_source')
        if source and start and source.start>start:self.add_error('copy_source','Начало источника должно быть не позже нового плана.')
        return result
