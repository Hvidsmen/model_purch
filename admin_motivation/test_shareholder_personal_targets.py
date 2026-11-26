from copy import deepcopy
from decimal import Decimal as D
from django.http import QueryDict
from django.test import SimpleTestCase,TestCase
from .services.shareholder_personal_targets import targets
from .services.reports_next_presenter import personal_details
from .services import shareholder_reports as reports,shareholder_regions as regions,shareholder_runner as runner
from .models import ShareholderSnapshot
from . import test_shareholder_original_rules as fixtures


def source_rows():
    rows=[]
    for quarter in range(1,5):
        for brand,fraction,formula in [('A',D('.25'),'Группа планов продаж'),('B',D('.75'),'Подразделение(Глобально)')]:
            row=fixtures.row(str(quarter),100,100,branch='Д-Питер',brand=brand)
            row.update(VariationCalculate=formula,СуммаПланUSDКвартал=100*quarter*fraction,
                       СуммаОтгрузокUSDКвартал=50*quarter*fraction,СуммаПланUSD=9999999,СуммаОтгрузокUSD=9999999)
            rows.append(row)
    for brand,formula in [('A','Группа планов продаж'),('B','Подразделение(Глобально)')]:
        row=fixtures.row('5',100,100,branch='Д-Питер',brand=brand)
        row.update(VariationCalculate=formula,СуммаПланUSDКвартал=9999999,СуммаОтгрузокUSDКвартал=9999999)
        rows.append(row)
    return rows


class PersonalTargetCoverageTests(SimpleTestCase):
    def test_actual_quarter_inputs_ignore_overlapping_rule_windows_and_annual_copies(self):
        rows=source_rows();before=deepcopy(rows)
        result=targets(rows,list('12345'))
        self.assertEqual([result[code]['plan'] for code in '12345'],list(map(D,[100,300,300,1000,1000])))
        self.assertEqual([result[code]['fact'] for code in '12345'],list(map(D,[50,150,150,500,500])))
        self.assertEqual({cell['ratio'] for cell in result.values()},{D('.5')})
        self.assertEqual(rows,before)

    def test_missing_inputs_are_not_invented_as_zero(self):
        rows=source_rows();rows[0]['СуммаПланUSDКвартал']=None
        result=targets(rows,list('12345'))
        self.assertEqual(set(result),{'3'})


class RegionalPersonalTargetReportTests(TestCase):
    def setUp(self):
        fixtures.OriginalSnapshotTests.setUp(self)
        self.run.configuration['regional_rule']=regions.RULE
        self.run.save(update_fields=['configuration'])
        self.run.chunks.all().delete()
        rows=source_rows()
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='report_pol',sequence=0,payload=runner.serial(rows))
        ShareholderSnapshot.objects.create(calculation=self.run,mode='combined',dataset='eligibility_pol',sequence=0,
            payload=[fixtures.gate(row,True) for row in rows if row['КварталМотивации']=='Год'])

    def test_mixed_formulas_do_not_hide_the_allocated_employee_target(self):
        query=QueryDict('year=2026&type=Политики&author=Иванов&subdivision=Д-Питер&period=2&period=5')
        data=reports.data_for(self.run,query)
        self.assertIsNone(data['sections'][0]['data']['detail_totals'][0][14])
        display=personal_details(data,query,period_codes=['2','5'])
        self.assertEqual([goal['plan'] for goal in display['goals']],[D(300),D(1000)])
        self.assertEqual([goal['remaining'] for goal in display['goals']],[D(150),D(500)])
        self.assertTrue(all(goal['personal_plan'] and not goal['global_scope'] for goal in display['goals']))
        self.assertEqual(display['goal_total']['plan'],D(1000))
        self.assertEqual(display['goal_total']['remaining'],D(500))

    def test_product_filter_does_not_reduce_the_overall_personal_plan(self):
        query=QueryDict('year=2026&type=Политики&author=Иванов&subdivision=Д-Питер&period=5&brand=A')
        data=reports.data_for(self.run,query)
        display=personal_details(data,query,period_codes=['5'])
        self.assertEqual(display['goals'][0]['plan'],D(1000))
        self.assertEqual(display['goals'][0]['fact'],D(500))
        self.assertEqual({row['Марка'] for row in data['sections'][0]['data']['_source_rows']},{'A'})

    def test_first_quarter_keeps_allocated_plan_visible_without_changing_unconditional_payment(self):
        from django.template.loader import render_to_string
        query=QueryDict('year=2026&type=Политики&author=Иванов&subdivision=Д-Питер&period=1')
        data=reports.data_for(self.run,query)
        display=personal_details(data,query,period_codes=['1'])
        goal=display['goals'][0]
        self.assertEqual((goal['plan'],goal['fact'],goal['ratio']),(D(100),D(50),D('.5')))
        self.assertTrue(goal['first_quarter'])
        html=render_to_string('admin_motivation/reports_next/_my_goals.html',{'presentation':display,'ledger':data,'shareholder_report':True})
        self.assertIn('I квартал выплачивается без проверки выполнения личного плана.',html)
        self.assertNotIn('План первого квартала равен факту',html)
        self.assertNotIn('Цель не определена',html)
