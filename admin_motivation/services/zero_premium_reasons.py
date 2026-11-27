"""Explain zero shareholder premiums using frozen payout inputs, never a forecast."""
from collections import defaultdict
from decimal import Decimal
from .premium_forecast import number
from .reports_next import period_code
from .coefficient_totals import performance
from .personal_execution import NAMES

ZERO=Decimal(0)
FORMULAS={'Марка(Бренд)':('ГруппаПлановПродаж','ГруппаТоваров','Марка'),
          'Группа товаров':('ГруппаПлановПродаж','ГруппаТоваров'),
          'Группа планов продаж':('ГруппаПлановПродаж',),'Подразделение':()}


def percent(value,digits=2):
    text=format(value*100,'.'+str(digits)+'f')
    return (text.rstrip('0').rstrip('.') if digits!=2 else text.removesuffix('.00')).replace('.',',')+'%'


def owner(row):
    return (row.get('Подразделение') or '—',row.get('АвторСделки') or '—',str(row.get('Год')))


def unique_ratio(rows,zero=False):
    values=set()
    for row in rows:
        plan,fact=number(row.get('plan')),number(row.get('fact'))
        if plan is None or fact is None:return None
        value=fact/plan if plan else ZERO if zero else None
        if value is None:return None
        values.add(value)
    return next(iter(values)) if len(values)==1 else None


class Reasons:
    def __init__(self,run,kind,raw,selected,department,kind_base,currency='USD'):
        self.currency=currency
        self.run=run;self.kind=kind;self.raw=defaultdict(list);self.selected=defaultdict(list)
        self.department=department;self.kind_base=kind_base;self.personal={};self.threshold=Decimal(run.configuration['payout_threshold'])
        self.regional=(kind=='Политики' and run.metadata.get('regional_rule')=='rostov-piter-policy-plans-2026-v1')
        for r in raw:self.raw[owner(r)].append(r)
        for r in selected:self.selected[(*owner(r),period_code(r.get('КварталМотивации')))].append(r)

    def get(self,branch,author,year,code,paid):
        if paid!=0:return []
        key=(branch,author,str(year));source=self.selected.get((*key,code),[])
        suffix='RUR' if self.currency=='RUB' else 'USD'
        possible=sum((number(r.get('СуммаМотивации'+suffix)) or ZERO for r in source),ZERO)
        raw_paid=sum((number(r.get('СуммаМотивации'+suffix+'УчетПроцента')) or ZERO for r in source),ZERO)
        prefix=self.kind+' · '+branch+' · '+author+': '
        if raw_paid and source:return [prefix+'применённая поправка руководителя обнулила премию.']
        if not possible:
            if any((number(r.get('СуммаМотивацииUSD')) or ZERO)>0 for r in source) and any((number(r.get('СуммаМотивацииUSD')) or ZERO)<0 for r in source):
                return [prefix+'положительные и отрицательные начисления взаимно компенсируются.']
            return [prefix+'нет расчётной премии на текущих отгрузках; непройденный порог не установлен.']
        if code=='1':return [prefix+'I квартал выплачивается без проверки плана; нулевая сумма не связана с порогом.']
        regional=self.regional and branch in ('Д-Питер','Д-Ростов')
        result=[]
        # Separate Q1–Q3 projections cannot reconstruct a combined Q4/year
        # personal/type base for Moscow+TT. Do not attribute a failure to it.
        native_base_known=not (getattr(self.run,'scenario','combined')=='separate' and code in ('4','5') and branch in ('Д-Москва','ТЕРМОТРЕЙД'))
        def add(label,value,personal=False):
            if value is not None and value<self.threshold:
                actual=percent(value,6) if percent(value)==percent(self.threshold) else percent(value)
                context=prefix if personal else self.kind+' · '+branch+': '
                result.append(context+'не пройден '+label+': '+actual+' при требуемых '+percent(self.threshold)+'.')
        def matching(rows):return [r for r in rows if r.get('subdivision')==branch and str(r.get('year'))==str(year) and period_code(r.get('period'))==code]
        department=unique_ratio(matching(self.department),zero=regional)
        kind_base=unique_ratio(matching(self.kind_base),zero=regional)
        if regional:add('порог подразделения',department)
        if native_base_known and (not regional or self.run.metadata.get('regional_gate_version')=='four-checks-v1'):
            add('порог '+NAMES.get(self.kind,self.kind.casefold()),kind_base)
        if (key,code) not in self.personal:
            original=self.run.configuration.get('rule')=='shareholder-original-2026-v2'
            base=performance(self.raw[key],self.kind,[code],original_rules=original) if native_base_known else []
            self.personal[key,code]=unique_ratio([r for r in base if period_code(r['period'])==code],zero=regional)
        add('порог личного плана',self.personal[key,code],True)
        scopes=defaultdict(list)
        for r in source:
            # A zero rate/no shipment cannot prove a failed threshold.
            if not (number(r.get('СуммаМотивацииUSD')) or ZERO):continue
            formula=r.get('VariationCalculate') or ''
            if formula in FORMULAS:
                scope=tuple(r.get(f) or '—' for f in FORMULAS[formula]);scopes[formula,scope].append(r)
            elif formula=='Подразделение(Глобально)':
                add('порог формулы «Подразделение(Глобально)»',department if regional else kind_base if native_base_known else None,True)
        for (formula,scope),items in scopes.items():
            values=[{'plan':r.get('СуммаПланUSD'),'fact':r.get('СуммаОтгрузокUSD')} for r in items]
            add('порог формулы «'+formula+'»'+(' ('+' / '.join(scope)+')' if scope else ''),unique_ratio(values,zero=regional),True)
        return list(dict.fromkeys(result)) or [prefix+'в сохранённом снимке недостаточно данных для определения непройденной проверки.']


def enrich(display,ledger):
    if not ledger.get('shareholder_forecast'):return
    def collect(rows):
        rows=list(rows)
        messages=list(dict.fromkeys(message for row in rows if row['amount']==0 for message in row.get('zero_reasons',[])))
        if not messages and any(r['amount']>0 for r in rows) and any(r['amount']<0 for r in rows):
            messages=['Положительные и отрицательные премии взаимно компенсируются.']
        return messages
    for card in display['period_cards']:
        selected=[r for r in ledger['rows'] if r['code']==card['code']]
        card['zero_reasons']=collect(selected) if card['amount']==0 else []
        for item in card['type_totals']:
            item['zero_reasons']=collect(r for r in selected if r['kind']==item['kind']) if item['total']==0 else []
    display['zero_reasons']=collect(ledger['rows']) if display['total']==0 else []
