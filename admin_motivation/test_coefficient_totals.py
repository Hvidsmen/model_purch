from copy import deepcopy
from decimal import Decimal as D

from django.http import QueryDict
from django.template.loader import render_to_string
from django.test import SimpleTestCase,TestCase

from .services.coefficient_totals import performance
from .services.reports_next_presenter import present
from .services import shareholder_reports as reports,shareholder_runner as runner
from .test_shareholder_personal_targets import source_rows
from . import test_shareholder_original_rules as fixtures
from .models import ShareholderSnapshot


class CoefficientTotalTests(SimpleTestCase):
    def test_sum_quarter_inputs_once_without_global_windows_or_annual_copies(self):
        rows=source_rows();before=deepcopy(rows)
        duplicate={**rows[0],'VariationCalculate':'Подразделение'}
        result=performance([*rows,duplicate],'Политики',list('12345'))
        self.assertEqual([r['plan'] for r in result],list(map(D,[50,300,300,1000,1000])))
        self.assertEqual([r['fact'] for r in result],list(map(D,[50,150,150,500,500])))
        self.assertEqual([r['ratio'] for r in result],[D(1),D('.5'),D('.5'),D('.5'),D('.5')])
        self.assertEqual(rows,before)

    def test_same_product_from_two_employees_is_additive_and_branches_remain_separate(self):
        rows=source_rows()
        result=performance([*rows,*[{**r,'АвторСделки':'Другой'} for r in rows],
                            *[{**r,'Подразделение':'Д-Ростов'} for r in rows]],'Продажи',['2'])
        self.assertEqual([(r['subdivision'],r['plan'],r['fact']) for r in result],
                         [('Д-Питер',D(600),D(300)),('Д-Ростов',D(300),D(150))])

    def test_missing_quarter_inputs_do_not_fall_back_to_overlapping_windows(self):
        rows=source_rows();rows[0]['СуммаПланUSDКвартал']=None
        result=performance(rows,'Политики',['2'])
        self.assertIsNone(result[0]['plan']);self.assertIsNone(result[0]['ratio'])
        self.assertEqual(result[0]['fact'],D(150))


class CoefficientTotalReportTests(TestCase):
    def setUp(self):
        fixtures.OriginalSnapshotTests.setUp(self)
        self.run.chunks.all().delete()
        rows=source_rows()
        for tag in ['pol','sales']:
            ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='report_'+tag,
                                               sequence=0,payload=runner.serial(rows))
            ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='eligibility_'+tag,
                                               sequence=0,payload=[fixtures.gate(row,True) for row in rows if row['КварталМотивации']=='Год'])

    def test_report_cards_and_excel_show_one_additive_total_per_kind(self):
        query=QueryDict('type=Политики&type=Продажи&subdivision=Д-Питер&period=2')
        data=reports.data_for(self.run,query,detailed=False)
        display=present('team',data,data,['2'])
        for section in display['team_sections']:
            targets=section['remaining_targets']
            self.assertEqual(len(targets),1)
            card=targets[0]['display_targets'][0]
            self.assertNotIn('conditions',card)
            self.assertEqual((card['plan'],card['fact'],card['remaining']),(D(300),D(150),D(150)))
            self.assertEqual(len(section['performance_matrix']['rows']),1)
        html=render_to_string('admin_motivation/reports_next/_team_remaining.html',{'presentation':display})
        self.assertEqual(html.count('<article '),2)
        self.assertNotIn('nr-target-condition',html)
        self.assertNotIn('Группа планов продаж',html)
        self.assertNotIn('Подразделение(Глобально)',html)
        from .services.presentation_excel import workbook
        from openpyxl import load_workbook
        output=workbook('team',data,[]);self.addCleanup(output.close)
        book=load_workbook(output,read_only=True);self.addCleanup(book.close)
        rows=[row for row in book['План-факт'].iter_rows(values_only=True) if row and row[0]=='Д-Питер']
        self.assertEqual(len(rows),2)
        self.assertTrue(all(row[-3:]==(300,150,.5) for row in rows))

    def test_product_filters_apply_to_additive_totals(self):
        query=QueryDict('type=Политики&subdivision=Д-Питер&period=2&brand=A')
        data=reports.data_for(self.run,query,detailed=False)
        target=data['coefficient_plan_fact'][0]
        self.assertEqual((target['plan'],target['fact']),(D(75),D('37.5')))
