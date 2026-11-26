from copy import deepcopy
from decimal import Decimal as D

from django.template.loader import render_to_string
from django.test import SimpleTestCase

from .services.reports_next_presenter import present


class CoefficientCardsTests(SimpleTestCase):
    def ledger(self):
        rows=[];bases=[]
        for kind,formulas in [('Политики',['Группа планов продаж','Подразделение']),
                              ('Продажи',['Группа планов продаж','Подразделение','Подразделение(Глобально)'])]:
            for code in ['1','2','4','5']:
                period='Год' if code=='5' else code+' квартал'
                rows.append({'subdivision':'Д-Питер','author':'Автор','unallocated':False,
                             'kind':kind,'code':code,'period':period,'currency':'USD',
                             'amount':D(10),'bonus':D(0),'total':D(10),'possible':D(10)})
                for index,formula in enumerate(formulas,1):
                    bases.append({'subdivision':'Д-Питер','year':2026,'kind':kind,
                                  'formula':formula,'scope_author':'Всё подразделение',
                                  'period':period,'plan':D(100*index),'fact':D(50*index),'ratio':D('.5')})
        return {'rows':rows,'total':D(80),'kinds':['Политики','Продажи'],
                'currency':'USD','plan_fact':bases}

    def test_each_kind_has_one_card_per_period_with_all_overlapping_conditions(self):
        ledger=self.ledger();before=deepcopy(ledger)
        result=present('team',ledger,ledger,['1','2','4','5'])
        for section,count in zip(result['team_sections'],[2,3]):
            self.assertEqual(len(section['remaining_targets']),1)
            cards=section['remaining_targets'][0]['display_targets']
            self.assertEqual([card['label'] for card in cards],['1 кв.','2 кв.','4 кв.','Год'])
            for card in cards:
                self.assertEqual(len(card['conditions']),count)
                self.assertIsNone(card['plan'])  # overlapping bases are not added
                self.assertEqual([c['plan'] for c in card['conditions']],
                                 [D(100*i) for i in range(1,count+1)])
        self.assertEqual(len(result['team_target_rows']),4)
        self.assertTrue(all(len(cell['cards'])==1 for line in result['team_target_rows'] for cell in line['cells']))
        html=render_to_string('admin_motivation/reports_next/_team_remaining.html',{'presentation':result})
        self.assertEqual(html.count('<article '),8)
        self.assertEqual(html.count('nr-target-condition'),20)
        self.assertEqual(ledger,before)

    def test_personal_details_keep_their_original_formula_bases(self):
        ledger=self.ledger()
        for kind in ['mine','detail']:
            result=present(kind,ledger,ledger,['1','2','4','5'])
            self.assertEqual(len(result['team_sections'][0]['remaining_targets']),2)
            self.assertEqual(len(result['team_sections'][1]['remaining_targets']),3)

    def test_branches_are_not_combined_and_single_condition_keeps_its_values(self):
        from .services.team_target_layout import coefficient_cards
        target={'label':'1 кв.','plan':D(100),'fact':D(50),'ratio':D('.5'),'remaining':D(50)}
        rows=[{'kind':'Политики','subdivision':branch,'year':2026,'title':'По коэффициентам',
               'formula':'Подразделение','targets':[target]} for branch in ['Д-Питер','Д-Ростов']]
        result=coefficient_cards(rows)
        self.assertEqual(len(result),2)
        self.assertEqual(result[0]['display_targets'][0]['remaining'],D(50))
        self.assertNotIn('conditions',result[0]['display_targets'][0])
