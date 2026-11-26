from copy import deepcopy
from decimal import Decimal as D
from django.test import SimpleTestCase
from django.template.loader import render_to_string
from .services.team_target_layout import period_cards
from .services.reports_next_presenter import present


class YearGoalCardsTests(SimpleTestCase):
    def target(self,code,**changes):
        return dict(code=code,label='Год' if code=='5' else '4 кв.',
                    plan=D(100),fact=D(50),ratio=D('.5'),remaining=D(50),**changes)

    def test_combined_and_single_selected_captions(self):
        fourth,year=self.target('4'),self.target('5');before=deepcopy([fourth,year])
        cards=period_cards([fourth,year],True)
        self.assertEqual(len(cards),1)
        self.assertEqual(cards[0]['display_label'],'4 кв. (1–4 кв.) и годовая часть')
        self.assertEqual(period_cards([fourth],True)[0]['display_label'],'4 кв. (1–4 кв.)')
        self.assertEqual(period_cards([year],True)[0]['label'],'Год')
        self.assertEqual([fourth,year],before)

    def test_distinct_or_missing_annual_base_is_not_hidden(self):
        for field,value in [('plan',D(200)),('fact',D(60)),('ratio',D('.6')),('plan',None)]:
            year=self.target('5');year[field]=value
            self.assertEqual(len(period_cards([self.target('4'),year],True)),2)

    def test_department_and_each_type_share_one_row_without_empty_year(self):
        rows=[];bases=[]
        for kind in ['Политики','Продажи','Директора']:
            for code in ['4','5']:
                rows.append(dict(subdivision='Д-Питер',author='Автор',kind=kind,unallocated=False,
                                 code=code,period='Год' if code=='5' else '4 квартал',amount=D(10),bonus=D(0),total=D(10),possible=D(10)))
                bases.append(dict(subdivision='Д-Питер',year=2026,kind=kind,formula='Подразделение(Глобально)',
                                  scope_author='Всё подразделение',period='Год' if code=='5' else '4 квартал',
                                  plan=D(100),fact=D(50),ratio=D('.5')))
        ledger=dict(rows=rows,total=D(60),currency='USD',kinds=['Политики','Продажи','Директора'],
                    original_rules=True,plan_fact=bases,department_plan_fact=[dict(bases[i],kind='Всё подразделение') for i in [0,1]])
        result=present('team',ledger,ledger,['4','5'])
        self.assertEqual(len(result['team_target_rows']),1)
        self.assertEqual(len(result['team_target_rows'][0]['cells']),4)
        html=render_to_string('admin_motivation/reports_next/_team_remaining.html',dict(presentation=result))
        self.assertEqual(html.count('<article '),4)
        self.assertEqual(html.count('4 кв. (1–4 кв.) и годовая часть'),4)
        self.assertNotIn('Нет данных за этот период',html)
