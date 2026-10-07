from django import forms
from .models import *


class StoreGroupAddForm(forms.Form):
    store_name = forms.CharField(max_length=255, label='Склад')
    subdivision_id = forms.ModelChoiceField(queryset=Subdivision.objects.all(), label='Подразделение')
    group_ozp_id = forms.ModelChoiceField(queryset=GroupOZP.objects.all(), label='Группа ОЗП')
    sub_group_ozp_id = forms.ModelChoiceField(queryset=SubGroupOZP.objects.all(), label='Подгруппа ОЗП')


class StoreGroupFileForm(forms.Form):
    file = forms.FileField(label='Файл обновления')


class GroupOZPCreateForm(forms.Form):
    group_ozp_name = forms.CharField(max_length=255, label='Группа ОЗП')


class SubGroupOZPCreateForm(forms.Form):
    sub_group_ozp_name = forms.CharField(max_length=255, label='Подгруппа ОЗП')


class StoreGroupFilterForm(forms.Form):
    store_name = forms.CharField(
        required=False,
        label='Название склада',
        widget=forms.TextInput(attrs={'placeholder': 'Поиск по названию...'})
    )
    subdivision_id = forms.ModelChoiceField(
        required=False,
        queryset=Subdivision.objects.all(),
        label='Подразделение',
        empty_label='— все —'
    )
    group_ozp_id = forms.ModelChoiceField(
        required=False,
        queryset=GroupOZP.objects.all(),
        label='Группа ОЗП',
        empty_label='— все —'
    )
    sub_group_ozp_id = forms.ModelChoiceField(
        required=False,
        queryset=SubGroupOZP.objects.all(),
        label='Подгруппа ОЗП',
        empty_label='— все —'
    )
