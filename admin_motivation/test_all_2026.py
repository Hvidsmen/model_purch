from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import uuid4
from unittest.mock import Mock, patch
from django.contrib.auth.models import User, Group
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from openpyxl import load_workbook
from base.access import POLITICIAN, SALES, DIRECTOR, BRANCH_DIRECTOR, DEPUTY_DIRECTOR, FINANCIER, MODELER, MOTIVATION_VIEWER, ADMIN
from base.models import MotivationReportAccess, PortalReportGroup
from .models import MotivationCalculationSchedule, MotivationExecution, SalesPlanScenario, Chanel, Subdivision, TypeCoeff
from .services.calculation_variants import ALL_2026, isolate, full_year_patch, main_runs
from .services.calculation_jobs import calculation_command
from .services.execution_tracking import parameters
from .services.manager_detail import manager_detail_report
from .test_manager_detail import detail_rows
from .test_pilot_calculation import procedure


class All2026SqlTests(SimpleTestCase):
    def test_extra_sql_keeps_all_periods_and_isolates_all_persistent_results(self):
        sql = procedure() + "\nIF OBJECT_ID('DataWH.motivation.Fact') IS NOT NULL DROP TABLE DataWH.motivation.Fact; SELECT 1 INTO DataWH.motivation.Fact; SELECT * INTO [DataWH].[motivation].[PlanFact] FROM DataWH.motivation.Fact;"
        changed = isolate(full_year_patch(sql))
        self.assertIn('FactAll2026', changed)
        self.assertIn('PlanFactAll2026', changed)
        self.assertIn('ReportMotivationByDealAuthorAll2026', changed)
        import re
        self.assertNotRegex(changed, r'\bmotivation\.(?:Fact|PlanFact|ReportMotivationByDealAuthor)\b')
        self.assertNotIn('WHERE 1=0 AND', changed)
        self.assertNotIn('IIF(Квартал=4,СуммаМотивацииUSDГод,0)', changed)
        self.assertIn('IF 1=0\nINSERT', changed)

    def test_all_five_periods_keep_full_year_annual_amounts(self):
        import sqlite3
        prefix = full_year_patch(procedure()).split('-- Separate 2026 variant:')[0].split(' AS\n', 1)[1]
        prefix = prefix.replace('INTO DataWH.motivation.ReportMotivationByDealAuthor', '').replace('#pf_base', 'pf_base')
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE pf_base (Квартал, СуммаМотивацииUSDГод, СуммаМотивацииRURГод)')
            db.executemany('INSERT INTO pf_base VALUES (?,?,?)', [(quarter, 20, 2000) for quarter in range(1, 5)])
            rows = db.execute(prefix).fetchall()
        self.assertEqual({row[0] for row in rows}, {'1 Квартал', '2 Квартал(1-2 кв)', '3 Квартал', '4 Квартал(1-4 кв)', 'Год'})
        self.assertEqual(sum(row[3] for row in rows if row[0] == 'Год'), 80)

    def test_extra_job_is_fixed_to_2026_and_has_independent_procedure_and_audit(self):
        job = MotivationCalculationSchedule(variant=ALL_2026, fact_year=2026, plan_version='Plan', quarter_share=80, year_share=20, payout_threshold='.8')
        command = calculation_command(job)
        self.assertIn('EXEC motivation.PortalCalcAll2026 @FactYear=2026', command)
        self.assertNotIn('EXEC motivation.PortalCalcBySub ', command)
        self.assertEqual(parameters(job)['premium_periods'], list('12345'))
        self.assertEqual(parameters(job)['premium_amount_quarters'], [1,2,3,4])
        job.fact_year = 2025
        with self.assertRaises(ValidationError): calculation_command(job)


class All2026ViewTests(TestCase):
    def test_multiple_subdivisions_survive_display_filters_and_excel(self):
        from urllib.parse import urlsplit
        from django.http import QueryDict
        self.execution.parameters['variant'] = 'separate_2026'
        self.execution.save(update_fields=['parameters'])
        channel = Chanel.objects.create(chanel_name='Multi branches')
        moscow = Subdivision.all_objects.create(subdivision_key='Д-Москва',subdivision_name='Москва',subdivision_global='Москва',chanel=channel)
        Subdivision.all_objects.create(subdivision_key='Термотрейд',subdivision_name='ТТ',subdivision_global='ТТ',chanel=channel,merged_into=moscow)
        def source(kind, values, **kwargs):
            entries = [('Д-Москва',10),('Термотрейд',20)]
            return {'period_summary':{'columns':[{'label':'3 кв.'}], 'rows':[
                {'subdivision':name,'author':name+' employee','values':[Decimal(amount)],'possible_values':[Decimal(amount)]}
                for name,amount in entries if not values['Subdivision'] or name in values['Subdivision']]}}
        base = {'scenario':'separate','period':'3','type':'Политики','run':'1'}
        with patch('admin_motivation.services.execution_tracking.observe_latest',return_value=None), \
             patch('admin_motivation.services.reports_next.current_run',return_value=nullcontext()), \
             patch('admin_motivation.services.reports_next.load_report',side_effect=source) as load:
            for selected,total in [(['Д-Москва'],10),(['Термотрейд'],20),(['Д-Москва','Термотрейд'],30),([],30)]:
                response = self.client.get(self.url,{**base,'subdivision':selected})
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.context['selected_subdivisions'],selected)
                self.assertEqual(load.call_args.args[1]['Subdivision'],selected)
                self.assertEqual(response.context['data']['total'],total)
                groups=response.context['presentation']['subdivision_summaries']
                self.assertEqual({row['branch'] for row in groups},set(selected or ['Д-Москва','Термотрейд']))
                self.assertEqual(sum(row['total'] for row in groups),total)
                self.assertEqual(QueryDict(urlsplit(response.context['export_url']).query).getlist('subdivision'),selected)
                self.assertContains(response,'type="checkbox" name="subdivision"')
                self.assertNotContains(response,'select name="subdivision"')
            exported = self.client.get(self.url,{**base,'subdivision':['Д-Москва','Термотрейд'],'export':'xlsx'})
            self.assertEqual(load.call_args.args[1]['Subdivision'],['Д-Москва','Термотрейд'])
            book = load_workbook(BytesIO(b''.join(exported.streaming_content)))
            names = {row[0] for row in book['Политики'].values}
            self.assertTrue({'Д-Москва','Термотрейд'} <= names)
            book.close(); exported.close()

    @patch('admin_motivation.services.execution_tracking.observe_latest',return_value=None)
    def test_combined_scenario_maps_original_selection_to_merged_subdivision(self, observe):
        response = self.client.get(self.url,{'scenario':'combined','subdivision':['Д-Москва','Термотрейд']})
        self.assertEqual(response.context['query'].getlist('subdivision'),['Д-Москва'])
        self.assertEqual(response.context['selected_subdivisions'],['Д-Москва'])

    def setUp(self):
        self.admin = User.objects.create_superuser('all-year-admin')
        self.client.force_login(self.admin)
        self.url = reverse('motivation_report_all_2026')
        self.plan = SalesPlanScenario.objects.create(title='Plan 2026', source_version='Plan', loaded_at=timezone.now())
        self.execution = MotivationExecution.objects.create(run_id=uuid4(), job_key=uuid4(), state='completed', started_at=timezone.now(), completed_at=timezone.now(), parameters={'variant': ALL_2026, 'fact_year': 2026})

    def source(self, author='Employee', branch='A'):
        return manager_detail_report([dict(detail_rows()[1], АвторСделки=author, Подразделение=branch, КварталМотивации=period)
                                      for period in ('1 Квартал', '2 Квартал(1-2 кв)', '3 Квартал', '4 Квартал(1-4 кв)', 'Год')])

    @patch('admin_motivation.services.execution_tracking.observe_latest',return_value=None)
    def test_detailed_view_and_excel_keep_variant_filters_and_separate_results(self,observe):
        from urllib.parse import urlsplit
        from django.http import QueryDict
        MotivationExecution.objects.create(run_id=uuid4(),job_key=uuid4(),state='completed',
            started_at=timezone.now(),completed_at=timezone.now(),parameters={'variant':'separate_2026','fact_year':2026})
        for scenario,variant in [('combined',ALL_2026),('separate','separate_2026')]:
            source=manager_detail_report([dict(detail_rows()[1],АвторСделки='Person',Подразделение='Д-Москва',
                КварталМотивации=period) for period in ('1 Квартал','2 Квартал(1-2 кв)')],'RUB')
            query={'run':'1','detail':'1','scenario':scenario,'type':'Политики','period':['1','2'],
                'currency':'RUB','subdivision':'Д-Москва','author':'Person','planning':'1. RAC'}
            with self.subTest(scenario=scenario),patch('admin_motivation.services.reports_next.current_run',return_value=nullcontext()), \
                 patch('admin_motivation.services.reports_next.load_report',return_value=source) as load:
                response=self.client.get(self.url,query)
                self.assertEqual(response.status_code,200)
                self.assertContains(response,'Расширенная таблица мотивации')
                self.assertContains(response,'manager_detail.js')
                self.assertContains(response,'name="detail" value="1"')
                self.assertFalse(load.call_args.kwargs['summary_only'])
                self.assertTrue(load.call_args.kwargs['include_performance'])
                self.assertEqual(load.call_args.kwargs['calculation_variant'],variant)
                self.assertEqual(load.call_args.kwargs['premium_period'],['1','2'])
                values=load.call_args.args[1]
                self.assertEqual(values['DealAuthor'],['Person'])
                self.assertEqual(values['gp_sales'],['1. RAC'])
                self.assertEqual(values['Currency'],'RUB')
                link=response.context['data']['rows'][0]['detail_url']
                linked=QueryDict(urlsplit(link).query)
                self.assertEqual(linked['detail'],'1');self.assertEqual(linked['scenario'],scenario)
                self.assertEqual(linked.getlist('author'),['Person'])
                exported=self.client.get(self.url,{**query,'export':'xlsx','table':'detail'})
                self.assertEqual(exported.status_code,200)
                book=load_workbook(BytesIO(b''.join(exported.streaming_content)))
                metadata=str(list(book['Параметры отчёта'].values))
                self.assertIn('раздельно' if scenario=='separate' else 'объединённо',metadata)
                self.assertIn('Годовая доля',metadata)
                self.assertEqual(load.call_args.kwargs['calculation_variant'],variant)
                book.close();exported.close()

    def test_extra_report_has_five_periods_and_independent_excel_and_source(self):
        with patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.services.reports_next.load_report', return_value=self.source()) as load:
            response = self.client.get(self.url, {'run':'1', 'type':'Политики'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(response.context['presentation']['period_columns']), 5)
            self.assertNotContains(response, 'value="[]"')
            self.assertEqual(response.context['presentation']['total'], 450)
            self.assertTrue(all(call.kwargs['calculation_variant'] == ALL_2026 for call in load.call_args_list))
            exported = self.client.get(self.url, {'export':'xlsx', 'type':'Политики'})
        book = load_workbook(BytesIO(b''.join(exported.streaming_content)))
        self.assertEqual(book['Сводка']['A1'].value, 'Все кварталы 2026')
        book.close(); exported.close()
        self.assertNotIn(self.execution, main_runs())

    def test_missing_extra_results_never_fall_back_to_main(self):
        self.execution.delete()
        MotivationExecution.objects.create(run_id=uuid4(), job_key=uuid4(), state='completed', started_at=timezone.now(), completed_at=timezone.now(), parameters={'fact_year':2026})
        with patch('admin_motivation.services.reports_next.load_report') as load:
            response = self.client.get(self.url, {'run':'1'})
        self.assertContains(response, 'Результаты основного варианта здесь не используются.')
        load.assert_not_called()

    def test_sql_report_reads_only_extra_result_table_and_keeps_early_periods(self):
        from .services.reports import load_report
        row = dict(detail_rows()[0])
        fields = ['Подразделение', 'АвторСделки', 'Год', 'КварталМотивации', 'СуммаМотивацииUSD', 'СуммаМотивацииUSDУчетПроцента', 'Остаток', 'СуммаМотивацииRUR', 'СуммаМотивацииRURУчетПроцента', 'ОстатокRUR']
        connection, cursor = Mock(), Mock()
        cursor.description = [(field,) for field in fields]
        cursor.fetchmany.return_value = [tuple(row[field] for field in fields)]
        values = {'TypeCoeff_':'Политики', 'Subdivision':['A'], 'DealAuthor':[], 'Years':['2026'], 'gp_sales':[], 'GPGoods':[], 'Brend':[], 'Currency':'USD'}
        with patch('admin_motivation.services.reports.connect_database', return_value=(connection,cursor)):
            result = load_report('manager-detail', values, summary_only=True, calculation_variant=ALL_2026)
        sql = cursor.execute.call_args.args[0]
        self.assertIn('ReportMotivationByDealAuthorAll2026', sql)
        self.assertNotIn("LIKE N'4 кв%'", sql)
        self.assertEqual(result['period_summary']['rows'][0]['values'][0], 90)

    def test_start_creates_separate_manual_job_and_cannot_override_year_or_mode(self):
        with patch('admin_motivation.services.calculation_jobs.start_job') as start, patch('admin_motivation.services.execution_tracking.observe', return_value=None):
            response = self.client.post(self.url, {'plan_version':'Plan', 'quarter_share':'80', 'year_share':'20', 'payout_threshold':'.8', 'fact_year':'2025', 'variant':'main', 'mode':'daily'})
        self.assertEqual(response.status_code, 302)
        job = start.call_args.args[0]
        self.assertEqual((job.variant, job.fact_year, job.mode, job.enabled), (ALL_2026, 2026, 'manual', False))
        self.assertTrue(MotivationCalculationSchedule.objects.filter(pk=job.pk, variant=ALL_2026).exists())

    def test_personal_roles_cannot_open_additional_report_or_export(self):
        channel = Chanel.objects.create(chanel_name='All-year')
        branch = Subdivision.objects.create(subdivision_key='A', subdivision_name='A', subdivision_global='A', chanel=channel)
        user = User.objects.create_user('all-year-policy'); user.groups.add(Group.objects.get(name=POLITICIAN))
        MotivationReportAccess.objects.create(user=user, subdivision=branch, deal_author='Own')
        self.client.force_login(user)
        with patch('admin_motivation.services.reports_next.load_report') as load:
            self.assertEqual(self.client.get(self.url, {'run':'1'}).status_code, 403)
            self.assertEqual(self.client.get(self.url, {'export':'xlsx'}).status_code, 403)
            load.assert_not_called()
        self.assertEqual(self.client.get(self.url, {'run':'1', 'subdivision':'Foreign'}).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'plan_version':'Plan'}).status_code, 403)


    def test_plan_fact_is_read_from_additional_source(self):
        source = self.source()
        source['motivation_performance'] = [{
            'Подразделение': 'A', 'Год': 2026, 'КварталМотивации': '3 Квартал',
            'VariationCalculate': 'Подразделение(Глобально)', 'Plan_': 200, 'Fact_': 150}]
        with patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.services.reports_next.load_report', return_value=source) as load:
            response = self.client.get(self.url, {'run':'1', 'type':'Политики'})
        self.assertContains(response, 'План-факт мотивации · Политики')
        self.assertEqual(response.context['data']['plan_fact'][0]['plan'], 200)
        self.assertEqual(response.context['data']['plan_fact'][0]['fact'], 150)
        self.assertTrue(all(call.kwargs['summary_only'] is True for call in load.call_args_list))


    def test_repeated_start_reuses_job_and_does_not_restart_running_calculation(self):
        params = {'plan_version':'Plan', 'quarter_share':'80', 'year_share':'20', 'payout_threshold':'.8'}
        with patch('admin_motivation.services.calculation_jobs.start_job') as start, patch('admin_motivation.services.calculation_jobs.monitor', return_value={'status':'Выполняется'}):
            first = self.client.post(self.url, params)
            second = self.client.post(self.url, params)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(MotivationCalculationSchedule.objects.filter(variant=ALL_2026).count(), 1)
        start.assert_called_once()

    def role_account(self, role, name):
        user = User.objects.create_user(name)
        user.groups.add(Group.objects.get(name=role))
        channel, _ = Chanel.objects.get_or_create(chanel_name='Access')
        branch, _ = Subdivision.objects.get_or_create(subdivision_key='A', defaults={
            'subdivision_name': 'A', 'subdivision_global': 'A', 'chanel': channel})
        MotivationReportAccess.objects.create(user=user, subdivision=branch, deal_author='Own')
        if role in (BRANCH_DIRECTOR, DEPUTY_DIRECTOR):
            group = Group.objects.create(name=name + '-scope')
            config = PortalReportGroup.objects.create(group=group, portal_role=role)
            config.subdivisions.add(branch)
            user.groups.add(group)
        TypeCoeff.objects.get_or_create(type_coeff_name='Политики')
        return user

    def test_explicit_roles_see_link_and_can_read_and_export(self):
        index = reverse('motivation_reports')
        for role in (ADMIN, DIRECTOR, BRANCH_DIRECTOR, MOTIVATION_VIEWER):
            with self.subTest(role=role):
                user = self.role_account(role, 'allowed-' + str(len(User.objects.all())))
                self.client.force_login(user)
                response = self.client.get(index)
                self.assertContains(response, self.url)
                with patch('admin_motivation.services.reports_next.current_run', return_value=nullcontext()), patch('admin_motivation.services.reports_next.load_report', return_value=self.source('Own')):
                    response = self.client.get(self.url, {'run': '1', 'type': 'Политики'})
                    self.assertEqual(response.status_code, 200)
                    exported = self.client.get(self.url, {'export': 'xlsx', 'type': 'Политики'})
                    self.assertEqual(exported.status_code, 200)
                    exported.close()

    def test_other_roles_do_not_see_link_and_cannot_open_by_url(self):
        for role in (POLITICIAN, SALES, DEPUTY_DIRECTOR, FINANCIER, MODELER):
            with self.subTest(role=role):
                user = self.role_account(role, 'denied-' + str(len(User.objects.all())))
                self.client.force_login(user)
                self.assertNotContains(self.client.get(reverse('motivation_reports')), self.url)
                with patch('admin_motivation.services.reports_next.load_report') as load:
                    self.assertEqual(self.client.get(self.url, {'run': '1'}).status_code, 403)
                    self.assertEqual(self.client.get(self.url, {'export': 'xlsx'}).status_code, 403)
                    load.assert_not_called()

    def test_read_access_does_not_grant_starting_calculations(self):
        for role in (DIRECTOR, BRANCH_DIRECTOR, MOTIVATION_VIEWER):
            user = self.role_account(role, 'readonly-' + str(len(User.objects.all())))
            self.client.force_login(user)
            with patch('admin_motivation.services.calculation_jobs.start_job') as start:
                self.assertEqual(self.client.post(self.url, {'plan_version': 'Plan'}).status_code, 403)
                start.assert_not_called()

    def test_director_remains_limited_to_assigned_subdivisions(self):
        user = self.role_account(BRANCH_DIRECTOR, 'scoped-director')
        self.client.force_login(user)
        with patch('admin_motivation.services.reports_next.load_report') as load:
            self.assertEqual(self.client.get(self.url, {'run': '1', 'subdivision': 'Foreign'}).status_code, 403)
            self.assertEqual(self.client.get(self.url, {'run': '1', 'subdivision': ['A', 'Foreign']}).status_code, 403)
            load.assert_not_called()
