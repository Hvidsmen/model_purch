from copy import deepcopy
from decimal import Decimal as D
from django.test import SimpleTestCase
from .services.reports_next_presenter import summary_performance, performance_matrix, present


class SummaryPerformanceTests(SimpleTestCase):
    def item(self,author='Иван',**changes):
        return {'subdivision':'Д-Питер','year':2026,'kind':'Политики','formula':'Группа планов продаж',
                'scope_author':author,'period':'2 квартал','plan':D(100),'fact':D(90),'ratio':D('.9'),**changes}

    def test_employee_slices_merge_and_ratio_uses_summed_amounts(self):
        rows=[self.item(),self.item('Пётр',plan=D(300),fact=D(150))]
        before=deepcopy(rows)
        merged=summary_performance(rows)
        self.assertEqual(rows,before)
        self.assertEqual(len(merged),1)
        self.assertEqual((merged[0]['plan'],merged[0]['fact'],merged[0]['ratio']),(D(400),D(240),D('.6')))
        self.assertEqual(merged[0]['scope_author'],'Всё подразделение')
        self.assertEqual(len(performance_matrix(merged,[{'code':'2'}])['rows']),1)

    def test_different_formulas_periods_kinds_and_branches_stay_separate(self):
        rows=[self.item(),self.item('Другой',formula='Марка(Бренд)'),self.item('Другой',kind='Продажи'),
              self.item('Другой',period='Год'),self.item('Другой',subdivision='Д-Ростов')]
        self.assertEqual(len(summary_performance(rows)),5)

    def test_global_basis_is_not_added_to_employee_slices_and_missing_values_stay_missing(self):
        global_row=self.item('Всё подразделение',formula='Подразделение(Глобально)')
        rows=summary_performance([global_row,self.item(),self.item('Пётр',plan=None)])
        self.assertEqual(rows[0],global_row)
        self.assertIsNone(rows[1]['plan'])
        self.assertIsNone(rows[1]['ratio'])

    def test_summary_cards_table_and_excel_use_the_same_combined_basis(self):
        rows=[{'subdivision':'Д-Питер','author':author,'unallocated':False,'kind':'Политики',
               'code':'2','period':'2 квартал','currency':'USD','amount':D(10),'bonus':D(0),'total':D(10),'possible':D(10)}
              for author in ('Иван','Пётр')]
        ledger={'rows':rows,'total':D(20),'kinds':['Политики'],'currency':'USD',
                'plan_fact':[self.item(),self.item('Пётр',plan=D(300),fact=D(150))]}
        before=deepcopy(ledger)
        display=present('team',ledger,ledger,['2'])
        self.assertEqual(len(display['team_sections'][0]['performance_matrix']['rows']),1)
        self.assertEqual(len(display['team_sections'][0]['remaining_targets']),1)
        self.assertEqual(display['team_sections'][0]['remaining_targets'][0]['targets'][0]['remaining'],D(160))
        personal=present('mine',ledger,ledger,['2'])
        self.assertEqual(len(personal['performance_matrix']['rows']),2)
        from .services.presentation_excel import workbook
        from openpyxl import load_workbook
        output=workbook('team',ledger,[])
        self.addCleanup(output.close)
        book=load_workbook(output,read_only=True);self.addCleanup(book.close)
        data=[r for r in book['План-факт'].iter_rows(values_only=True) if r and r[0]=='Д-Питер']
        self.assertEqual(len(data),1)
        self.assertEqual(data[0][3],'Всё подразделение')
        self.assertEqual(data[0][-3:],(400,240,.6))
        self.assertEqual(ledger,before)
