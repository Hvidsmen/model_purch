import sqlite3
from copy import copy
from pathlib import Path
from unittest.mock import MagicMock, patch
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from .models import MotivationCalculationSchedule, Goods
from .services import calculation_jobs
from .services.transition_2026 import patch as transition_patch, separate_coefficients
from .services.motivation_performance import period_coverage, grouped_sql
from .services.product_filters import fields
from .services.heat_exclusion import is_heat, predicate as heat_predicate
from .services.reports import fetch, bind_sql


class TransitionSqlTests(SimpleTestCase):
    def test_optimized_source_preserves_director_assignments_in_separate_scenario(self):
        source = '''SELECT * FROM #director_employees directors
        JOIN facts sub ON directors.Subdivision=sub.SubdivisionName
        JOIN coeff sc ON sub.SubdivisionName=sc.Subdivision;
        SELECT * FROM #director_employees directors
        JOIN plans s ON directors.Subdivision=s.SubdivisionName
        JOIN coeff sc ON s.SubdivisionName=sc.Subdivision;'''
        changed = separate_coefficients(source)
        self.assertEqual(changed.count('directors.Subdivision=COALESCE'),2)
        self.assertEqual(changed.count(')=sc.Subdivision'),2)
        self.assertIn('m.SourceName=sub.SubdivisionName',changed)

    def test_heat_accepts_numbered_erp_labels_without_excluding_other_groups(self):
        for label in ('HEAT', ' heat ', '5. HEAT'):
            self.assertTrue(is_heat(label))
        for label in ('HEATING', 'NOT HEAT', '1. RAC', '', None):
            self.assertFalse(is_heat(label))
        with sqlite3.connect(':memory:') as db:
            db.create_function('PATINDEX', 2, lambda pattern, value: next((i+1 for i,ch in enumerate(value or '') if ch not in '0123456789. '), 0))
            for label in ('HEAT', ' heat ', '5. HEAT', 'HEATING', 'NOT HEAT', None):
                actual = db.execute('SELECT '+heat_predicate('value').replace("N'", "'")+' FROM (SELECT ? AS value)', (label,)).fetchone()[0]
                self.assertEqual(bool(actual), is_heat(label))

    def test_q4_eligibility_does_not_inherit_prior_shortfall(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE pf (Год, Квартал, plan_, fact_)')
            db.executemany('INSERT INTO pf VALUES (2026,?,?,?)', [(1,100,0),(2,100,0),(3,100,0),(4,100,100)])
            db.execute('CREATE TABLE contexts (КварталМотивации)')
            db.executemany('INSERT INTO contexts VALUES (?)', [('4 квартал',),('год',),('2 квартал',),('3 квартал',)])
            rows = db.execute('SELECT c.КварталМотивации,SUM(pf.plan_),SUM(pf.fact_) FROM contexts c JOIN pf ON '
                              + period_coverage('c').replace("N'", "'") + ' GROUP BY c.КварталМотивации').fetchall()
        result = {label:(plan,fact) for label,plan,fact in rows}
        self.assertEqual(result['4 квартал'], (100,100))
        self.assertEqual(result['год'], (100,100))
        self.assertEqual(result['2 квартал'], (200,0))
        self.assertEqual(result['3 квартал'], (100,0))

    def test_heat_remains_in_director_base(self):
        politicians = grouped_sql('SELECT * FROM input', 'Политики')
        sales = grouped_sql('SELECT * FROM input', 'Продажи')
        directors = grouped_sql('SELECT * FROM input', 'Директора')
        self.assertIn("N'HEAT'", politicians)
        self.assertIn("N'HEAT'", sales)
        self.assertNotIn("N'HEAT'", directors)

    def test_unknown_output_shape_fails_closed(self):
        with self.assertRaises(ValidationError):
            transition_patch('SELECT 1')


class TransitionPreparationTests(TestCase):
    @patch('admin_motivation.services.execution_tracking.observe_latest', return_value=None)
    def test_additional_page_exposes_scenario_and_three_multiselect_filters(self, observe):
        from base.testing import authorize_test_case
        authorize_test_case(self)
        response = self.client.get(reverse('motivation_report_all_2026'), {'scenario':'separate'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="scenario"')
        self.assertContains(response, 'Группа планов продаж')
        self.assertContains(response, 'Бренд')
        self.assertEqual([item['code'] for item in response.context['periods']], list('123'))
        self.assertEqual(observe.call_args.args[0], 'separate_2026')

    @patch('admin_motivation.reports_all_2026.can_view', return_value=True)
    @patch('admin_motivation.reports_all_2026.role_for', return_value='Директор')
    @patch('admin_motivation.reports_all_2026.politician_scope', return_value={'type':['Политики'],'subdivision':['Д-Москва']})
    @patch('admin_motivation.services.subdivision_aliases.mapping', return_value={'ТЕРМОТРЕЙД':'Д-Москва'})
    def test_separate_scenario_keeps_branch_access_checks(self, *mocks):
        from .reports_all_2026 import selection
        selection(MagicMock(), QueryDict('scenario=separate&period=3&subdivision=ТЕРМОТРЕЙД'))
        with self.assertRaises(PermissionDenied):
            selection(MagicMock(), QueryDict('scenario=separate&period=3&subdivision=Denied'))

    def test_separate_rows_and_branch_parameters_do_not_merge_back(self):
        cursor = MagicMock()
        cursor.description = [('Подразделение',), ('amount',)]
        cursor.fetchmany.return_value = [('Д-Москва', 0), ('ТЕРМОТРЕЙД', 90)]
        rows = fetch(cursor, 'SELECT ...', canonical_rows=False)
        self.assertEqual([row['Подразделение'] for row in rows], ['Д-Москва','ТЕРМОТРЕЙД'])
        sql, args = bind_sql('SELECT * FROM results WHERE Подразделение IN (@Subdivision)',
                            {'Subdivision':['ТЕРМОТРЕЙД']}, expand_aliases=False)
        self.assertEqual(args, ['ТЕРМОТРЕЙД'])

    @patch('admin_motivation.reports_all_2026.selection', return_value=(None,['Политики'],['3']))
    @patch('admin_motivation.services.reports_next.load_report')
    def test_separate_report_keeps_employee_premiums_and_product_selection(self, load, selection):
        from .reports_all_2026 import data_for
        load.return_value = {'period_summary': {'columns':[{'label':'3 Квартал'}], 'rows':[
            {'subdivision':'Д-Москва','author':'Moscow employee','values':[0]},
            {'subdivision':'ТЕРМОТРЕЙД','author':'TT employee','values':[90]}]}}
        data = data_for(MagicMock(), MagicMock(), QueryDict('scenario=separate&period=3&planning=1.+RAC&brand=A&brand=B'))
        self.assertEqual(data['total'],90)
        self.assertEqual([row['subdivision'] for row in data['rows']], ['Д-Москва','ТЕРМОТРЕЙД'])
        self.assertEqual(load.call_args.kwargs['calculation_variant'], 'separate_2026')
        self.assertEqual(load.call_args.args[1]['Brend'], ['A','B'])
        self.assertEqual(load.call_args.args[1]['gp_sales'], ['1. RAC'])

    def source(self):
        text = (Path(__file__).parent/'sql/review/sp_CalcBySub.refactored.sql').read_text()
        return text[text.index('CREATE OR ALTER PROCEDURE'):].replace('@Year', '@FactYear')

    @patch.object(calculation_jobs, 'rules', return_value=[('ТЕРМОТРЕЙД','Д-Москва','target')])
    def test_each_namespace_prepares_correct_scope_and_windows(self, rules):
        for variant, suffix in [('main',''),('all_2026','All2026'),('separate_2026','Separate2026')]:
            with self.subTest(variant=variant):
                cursor = MagicMock()
                cursor.fetchone.return_value = (self.source(), None)
                cursor.fetchall.return_value = [('SubdivisionKey',),('SubdivisionName',)]
                calculation_jobs.prepare(cursor, variant)
                sql = cursor.execute.call_args.args[0]
                self.assertEqual(sql.count('FROM #pf_base WHERE (Квартал=4 OR @FactYear<>2026)'), 0 if variant == 'separate_2026' else 2)
                self.assertIn("[ТипКоэф])) IN (N'Политики',N'Продажи')", sql)
                self.assertIn('motivation.ReportMotivationByDealAuthor'+suffix, sql)
                self.assertIn('motivation.AnnualAccrual'+suffix, sql)
                view = next(call.args[0] for call in cursor.execute.call_args_list if call.args[0].startswith('ALTER VIEW'))
                self.assertIn("s.SubdivisionName IN (N'Д-Москва',N'ТЕРМОТРЕЙД')", view)
                if variant == 'separate_2026':
                    self.assertIn('s.[SubdivisionName]', view)
                    self.assertNotIn('COALESCE(m.TargetName,s.[SubdivisionName]) AS', view)
                    self.assertEqual(sql.count(')=sc.Subdivision'), 4)

    def test_product_filters_allow_multiple_values_and_keep_selected_values(self):
        Goods.objects.create(planning_group_sales='HEAT', group='Heating', brand='Brand A')
        result = {item['name']:item for item in fields(QueryDict('brand=Brand+A&brand=Other'))}
        self.assertEqual([item['value'] for item in result['brand']['options'] if item['selected']], ['Brand A','Other'])
        self.assertEqual(result['goods']['label'], 'Группа')
        with self.assertRaises(ValidationError):
            fields(QueryDict('brand='+'x'*256))

    def test_separate_command_has_independent_audit_and_namespace(self):
        job = MotivationCalculationSchedule(name='All',variant='all_2026',fact_year=2026,plan_version='Plan')
        command = calculation_jobs.calculation_command(job)
        self.assertIn('motivation.PortalCalcAll2026',command)
        self.assertIn('motivation.PortalCalcSeparate2026',command)
        self.assertIn('separate_2026',command)
        self.assertEqual(command.count('sp_getapplock'),1)
