from django import forms
from .models import *
from .models_algorithm import *
from .defualt_parametrs import YEAR_PLANNING

class PlanByChanelForm(forms.Form):
    year = forms.IntegerField(label='Год планирования', initial=YEAR_PLANNING)
    sheet_name = forms.CharField(max_length=255, label='Название листа', initial='Загрузка')
    period = forms.ModelChoiceField(queryset=PeriodPlanRef.objects.all(), label='Периодичность')
    option = forms.ModelChoiceField(queryset=OptionPlanRef.objects.all(), label='Сценарий')
    file = forms.FileField(label="Файл плана продаж")

class PercentageSubdivisionForm(forms.Form):
    year = forms.IntegerField(label='Год планирования', initial=YEAR_PLANNING)
    sheet_name = forms.CharField(max_length=255, label='Название листа', initial='Загрузка')
    period = forms.ModelChoiceField(queryset=PeriodPlanRef.objects.all(), label='Периодичность')
    option = forms.ModelChoiceField(queryset=OptionPlanRef.objects.all(), label='Сценарий')
    file = forms.FileField(label="Файл плана продаж")

class SplitModelForm(forms.Form):
    year = forms.IntegerField(label='Год планирования', initial=YEAR_PLANNING)
    sheet_name = forms.CharField(max_length=255, label='Название листа', initial='Загрузка')
    option = forms.ModelChoiceField(queryset=OptionPlanRef.objects.all(), label='Сценарий')
    file = forms.FileField(label="Файл деления")