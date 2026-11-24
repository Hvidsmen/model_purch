from decimal import Decimal
from django import forms
from django.utils import timezone
from .models import MotivationCalculationSchedule


class CalculationForm(forms.Form):
    name=forms.CharField(label='Название расчёта',max_length=160,initial='Отчёты для акционеров')
    as_of=forms.DateField(label='Реальный факт по дату включительно',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'),initial=timezone.localdate)
    scenario=forms.ChoiceField(label='Сценарий',choices=[('combined','Москва + ТТ объединённо'),('separate','Москва и ТТ раздельно')])
    quarters=forms.MultipleChoiceField(label='Кварталы для долей авторов и отчётов',choices=[(str(q),f'{q} квартал') for q in range(1,5)],initial=list('1234'),widget=forms.CheckboxSelectMultiple)
    threshold_percent=forms.DecimalField(label='Перераспределять авторов с долей оборота ниже, %',initial=1,min_value=0,max_value=Decimal('99.9999'),decimal_places=4,max_digits=7)
    source_job=forms.ModelChoiceField(label='Параметры существующего задания «Все кварталы 2026»',queryset=MotivationCalculationSchedule.objects.none())

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['source_job'].queryset=MotivationCalculationSchedule.objects.filter(variant='all_2026',fact_year=2026).order_by('-created_at')

    def clean_as_of(self):
        value=self.cleaned_data['as_of']
        if value.year!=2026 or value>timezone.localdate():raise forms.ValidationError('Выберите дату 2026 года, не позднее сегодняшней.')
        return value
