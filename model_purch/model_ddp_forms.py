from django import forms
from decimal import Decimal
from .models import ScenarioModel

SUPPLY=(('','Не задан'),('direct','Прямой контракт'),('p2','P2'),('russian','Закупка РФ'))

class CreateForm(forms.Form):
    name=forms.CharField(label='Название расчёта',max_length=200)
    as_of=forms.DateField(label='Дата цен и курсов',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))

class ParametersForm(forms.Form):
    price_per_container=forms.DecimalField(label='Фрахт контейнера, USD',min_value=0,max_digits=18,decimal_places=2,required=False)
    customs_rate=forms.DecimalField(label='Таможенная ставка, %',min_value=0,max_value=100,max_digits=5,decimal_places=2,required=False)
    warehouse_delivery_cost=forms.DecimalField(label='Доставка до склада за контейнер, USD',min_value=0,max_digits=18,decimal_places=2,required=False)
    foreign_delivery_cost=forms.DecimalField(label='Загран доставка без DDP за контейнер, USD',min_value=0,max_digits=18,decimal_places=2,required=False)
    nr_customs_vat_rate=forms.DecimalField(label='НР таможенного НДС, %',min_value=0,max_value=100,max_digits=5,decimal_places=2,required=False)
    usd=forms.DecimalField(label='RUB за 1 USD',min_value=Decimal('.000001'),max_digits=18,decimal_places=6,required=False)
    eur=forms.DecimalField(label='RUB за 1 EUR',min_value=Decimal('.000001'),max_digits=18,decimal_places=6,required=False)
    cny=forms.DecimalField(label='RUB за 1 CNY',min_value=Decimal('.000001'),max_digits=18,decimal_places=6,required=False)

class RowForm(forms.Form):
    def __init__(self,*args,reference_choices=None,**kwargs):
        super().__init__(*args,**kwargs)
        if reference_choices:
            for name,values in reference_choices.items():
                if name not in self.fields or name=='supply_type':continue
                options=set(values)
                current=self.initial.get(name)
                if current:options.add(str(current))
                self.fields[name].widget=forms.Select(choices=[('','— Из источника —')]+[(value,value) for value in sorted(options,key=str.casefold)])

    volume=forms.DecimalField(label='Объём модели, м³',min_value=0,max_digits=24,decimal_places=12,required=False)
    container_volume=forms.DecimalField(label='Контейнер, м³',min_value=Decimal('.000001'),max_digits=24,decimal_places=12,required=False)
    duty_percent=forms.DecimalField(label='Пошлина, %',min_value=0,max_value=10000,max_digits=12,decimal_places=6,required=False)
    supply_type=forms.ChoiceField(label='Тип поставки',choices=SUPPLY,required=False)
    weight_kg=forms.DecimalField(label='Вес модели, кг',min_value=0,max_digits=24,decimal_places=12,required=False)
    planning_sales=forms.CharField(label='Группа планов продаж',max_length=255,required=False)
    group_goods=forms.CharField(label='Группа товаров',max_length=255,required=False)
    brand=forms.CharField(label='Марка',max_length=255,required=False)
    purch=forms.CharField(label='Закупка / поставщик',max_length=255,required=False)
    kind_purch=forms.CharField(label='Вид закупки',max_length=255,required=False)
    price_per_container=forms.DecimalField(label='Фрахт контейнера, USD',min_value=0,max_digits=18,decimal_places=2,required=False)
    customs_rate=forms.DecimalField(label='Таможенная ставка, %',min_value=0,max_value=100,max_digits=5,decimal_places=2,required=False)
    warehouse_delivery_cost=forms.DecimalField(label='Доставка до склада за контейнер, USD',min_value=0,max_digits=18,decimal_places=2,required=False)
    foreign_delivery_cost=forms.DecimalField(label='Загран доставка за контейнер, USD',min_value=0,max_digits=18,decimal_places=2,required=False)
    nr_customs_vat_rate=forms.DecimalField(label='НР таможенного НДС, %',min_value=0,max_value=100,max_digits=5,decimal_places=2,required=False)


class SourceForm(forms.Form):
    source=forms.ModelChoiceField(label='Сценарий закупок',queryset=ScenarioModel.objects.none())
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.fields['source'].queryset=ScenarioModel.objects.order_by('-date_start_plan','-pk')
