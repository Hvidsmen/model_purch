from datetime import date
from decimal import Decimal
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from .models import SalesPlanLine, GlobalCoeffVersion, Subdivision, SubdivisionCoeff
from .services.subdivision_scope import included, sql_predicate


class ScopeRulesTests(SimpleTestCase):
    def test_exclusion_normalizes_names_and_checks_alias_source_and_target(self):
        for name in ['Вентиляция','вЕнТиЛяЦиЯ',' Вентиляция ']:
            self.assertFalse(included(name))
        self.assertFalse(included('Вентиляция',{'Вентиляция':'A'}))
        self.assertFalse(included('Alias',{'Alias':'Вентиляция'}))
        self.assertTrue(included('A'));self.assertTrue(included(''))
        predicate=sql_predicate('[Подразделение]')
        self.assertIn('COALESCE',predicate)
        self.assertIn("N'Вентиляция'",predicate)
        self.assertIn("N''",predicate)
        self.assertNotIn('{',predicate)


class SubdivisionScopeTests(TestCase):
    def setUp(self):
        from .test_sales_plans import SalesPlanTests
        from base.testing import authorize_test_case
        self.fixture=SalesPlanTests();self.fixture.setUp();self.fixture.load()
        self.plan=self.fixture.scenario;self.version=self.fixture.version
        authorize_test_case(self)

    def legacy_ventilation(self):
        from .services.sales_plans import calculate_plan
        calculate_plan(self.plan.pk)
        local=self.plan.lines.get(subdivision='A');local.pk=None;local.subdivision='Вентиляция';local.save()
        global_line=self.plan.lines.get(subdivision='')
        global_line.amount_usd+=local.amount_usd
        for segment,value in local.segment_amounts.items():
            global_line.segment_amounts[segment]=str(Decimal(global_line.segment_amounts[segment])+Decimal(value))
        totals=[]
        for kind in ['Политики','Продажи']:
            amount=Decimal(0)
            for segment,record in global_line.calculation[kind].items():
                usd=Decimal(global_line.segment_amounts[segment]);motivation=usd*Decimal(record['coefficient'])
                record.update(usd=str(usd),motivation=str(motivation));amount+=motivation
            totals.append(amount)
        global_line.policies_usd,global_line.sales_usd=totals
        global_line.total_usd=sum(totals)+global_line.directors_usd
        global_line.save()
        return local,global_line

    def test_comparison_projection_does_not_read_saved_calculation_json(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .services.plan_scope import global_lines_without_excluded
        self.legacy_ventilation()
        with CaptureQueriesContext(connection) as captured:
            projected = global_lines_without_excluded(self.plan, input_only=True)
        self.assertEqual(sum(line.amount_usd for line in projected), Decimal(1500))
        for query in captured.captured_queries:
            self.assertNotIn('"calculation"', query['sql'])
        self.assertTrue(all(not line.calculation for line in projected))

    def test_import_ignores_excluded_source_before_building_global_plan(self):
        rows=self.fixture.rows()
        self.fixture.cursor.fetchall.return_value=[*rows,(' Вентиляция ',*rows[0][1:]),('вЕнТиЛяЦиЯ',*rows[0][1:])]
        self.assertEqual(self.fixture.load(),2)
        self.assertEqual(self.plan.lines.get(subdivision='').amount_usd,Decimal(1500))
        self.assertFalse(self.plan.lines.filter(subdivision__icontains='Вентиляция').exists())
        from .services.sales_plans import SQL
        self.assertEqual(SQL.count("N'Вентиляция'"),2)

    def test_legacy_reports_remove_branch_and_global_contribution_without_overwriting_audit(self):
        from .services.plan_report import period_report
        from .services.plan_summary import summary_report
        from .services.balancing import Budget
        from .services.comparison import compare_plan
        local,original=self.legacy_ventilation()
        self.assertEqual(original.amount_usd,Decimal(2500));self.assertEqual(original.total_usd,Decimal(375))
        global_report=period_report(self.plan,'global',{})
        self.assertEqual(global_report['totals']['plan'],Decimal(1500))
        self.assertEqual(global_report['totals']['total'],Decimal(225))
        branches=period_report(self.plan,'subdivisions',{})
        self.assertEqual(branches['filter_choices']['subdivision'],['A','B'])
        self.assertEqual(branches['totals']['plan'],Decimal(1500))
        summary=summary_report(self.plan,{})
        self.assertEqual(summary['summary_nodes'][0]['global'],Decimal(225))
        self.assertNotIn('Вентиляция',str(summary['summary_nodes']))
        budget=Budget(self.version,self.plan).payload()
        self.assertEqual(sum(Decimal(row['before']) for row in budget['display_totals']),Decimal(225))
        comparison=compare_plan(self.plan,self.version)
        self.assertEqual(Decimal(comparison['variants']['current']['plan']),Decimal(1500))
        self.assertEqual(Decimal(comparison['variants']['current']['total']),Decimal(225))
        original.refresh_from_db();local.refresh_from_db()
        self.assertEqual(original.amount_usd,Decimal(2500));self.assertEqual(original.total_usd,Decimal(375))
        self.assertEqual(local.subdivision,'Вентиляция')
        response=self.client.get(reverse('motivation_sales_plan',args=[self.plan.pk]),{'export':'details'})
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'Вентиляция')

    def test_calculation_rebuilds_old_global_amounts_without_excluded_branch(self):
        from .services.sales_plans import calculate_plan
        self.legacy_ventilation()
        self.assertEqual(calculate_plan(self.plan.pk),3)
        line=self.plan.lines.get(subdivision='')
        self.assertEqual(line.amount_usd,Decimal(1500))
        self.assertEqual(line.total_usd,Decimal(225))
        self.assertTrue(self.plan.lines.filter(subdivision='Вентиляция').exists())

    def test_excluded_coefficients_do_not_become_minimum_fallbacks_or_navigation(self):
        from .services.comparison import coefficient_rows,Index
        vent=Subdivision.all_objects.create(subdivision_key='Вентиляция',subdivision_name='Вентиляция',subdivision_global='Вентиляция',chanel=self.fixture.subs[0].chanel)
        for row in list(SubdivisionCoeff.objects.filter(version=self.version,subdivision=self.fixture.subs[0])):
            row.pk=None;row.subdivision=vent;row.motivation_coeff=.000001;row.save()
        self.assertFalse(Subdivision.objects.filter(pk=vent.pk).exists())
        rows=list(coefficient_rows([self.version.pk],subdivision=True))
        self.assertTrue(all(row[1]!='Вентиляция' for row in rows))
        matrix,_=Index(rows).coefficients(self.version.pk,'A','Missing group','*','*')
        self.assertEqual(matrix[0][0],Decimal('.2'))
        from .services.reports import parameters
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):parameters('manager-detail',{'subdivision':['Вентиляция']})

    def test_legacy_global_director_snapshot_removes_only_excluded_branch_share(self):
        from .services.plan_report import period_report
        _,original=self.legacy_ventilation()
        original.calculation['Директора']={'employees':[
            {'employee_id':1,'employee_name':'Common director','subdivisions':['A','Вентиляция'],
             'coefficients':['.01']*5,'segment_amounts':['200','400','600','200','600'],'motivation':'20'},
            {'employee_id':2,'employee_name':'Excluded director','subdivisions':['Вентиляция'],
             'coefficients':['.01']*5,'segment_amounts':['100','200','300','100','300'],'motivation':'10'}], 'motivation':'30'}
        original.directors_usd=Decimal(30);original.total_usd=Decimal(405);original.save()
        report=period_report(self.plan,'global',{})
        self.assertEqual(report['totals']['directors'],Decimal(10))
        self.assertEqual(report['totals']['total'],Decimal(235))
        records=report['report_lines'][0].calculation['Директора']['employees']
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['subdivisions'],['A'])
        original.refresh_from_db();self.assertEqual(original.directors_usd,Decimal(30))
