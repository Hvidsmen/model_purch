from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock,patch
import importlib.util,json,os,time
from unittest import skipUnless
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase,TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.contrib.auth.models import User
from django.utils import timezone
from . import test_coefficient_iteration_calculation as fixture
from .services.coefficient_iteration_runner import start,execute
from .services.iteration_author_diagnostics import diagnose,scope,MAP,field_coverage
from .services.prepared_sales import RULE


def snapshot():
    return SimpleNamespace(pk=18,started_at=timezone.now(),inputs={'export_names':{'a':'Д-Москва','b':'ТЕРМОТРЕЙД'},
        'excluded_subdivision_names':[],'coefficient_exclusions':[['channel','Политики','ДПП']]},
        results={'moscow_q4_2026':{'state':'completed','source':{'through':'2026-11-19','prepared_run_id':'frozen','named_authors':1,'missing_author_rows':66}}})


class DiagnosticsSourceTests(SimpleTestCase):
    def cursor(self,current='frozen'):
        cursor=Mock()
        cursor.fetchone.side_effect=[('frozen',timezone.now(),date(2025,1,1),date(2027,1,1),RULE),(current,)]
        cursor.fetchall.side_effect=[
            [('document_not_found',200,Decimal('13000000.12'),150),('document_author_blank',30,Decimal(1000),25)],
            [('source-key','invoice-key',date(2026,10,2),'100','ERP-MODEL','MODEL','1. RAC',Decimal(500),None,None,None,1,1,'Иванов')],
            [('erp','Документы.ЗаказКлиента','АвторНаименование','nvarchar')]]
        return cursor

    def test_read_only_report_distinguishes_missing_documents_and_empty_author(self):
        cursor=self.cursor();connection=Mock();original=deepcopy(snapshot().inputs)
        snap=snapshot();snap.results['moscow_q4_2026']['source']['prepared_run_id']='FROZEN'
        report=diagnose(snap,connector=Mock(return_value=(connection,cursor)))
        self.assertEqual(report['coverage'][0]['status'],'document_not_found')
        self.assertEqual(report['coverage'][1]['status'],'document_author_blank')
        self.assertEqual(report['samples'][0]['invoice_key_author'],'Иванов')
        self.assertTrue(report['same_prepared_snapshot']);self.assertEqual(report['saved_missing_groups'],66)
        scope_query=next(call.args for call in cursor.execute.call_args_list if call.args[0].startswith('INSERT INTO #diag_author_sales'))
        self.assertEqual(scope_query[1:3],(date(2026,10,1),date(2026,11,20)))
        self.assertIn('ДПП',scope_query[1:])
        for call in cursor.execute.call_args_list:
            if len(call.args)>1:self.assertTrue(call.args[0].startswith('INSERT INTO #diag_author_sales'))
            self.assertNotRegex(call.args[0],r'(?i)\b(?:INTO|UPDATE|DELETE FROM)\s+(?:DataWH\.)?(?:dbo|erp|motivation)\.')
        self.assertNotIn('WHERE AuthorName IS NOT NULL',MAP)
        self.assertEqual(snapshot().inputs,original);connection.close.assert_called_once()

    def test_changing_published_snapshot_discards_diagnostic_and_closes_connection(self):
        connection=Mock()
        with self.assertRaisesMessage(ValidationError,'изменился'):
            diagnose(snapshot(),connector=Mock(return_value=(connection,self.cursor('other'))))
        connection.close.assert_called_once()

    def test_scope_is_frozen_and_requires_completed_q4(self):
        snap=snapshot();snap.inputs['coefficient_exclusions']=[]
        predicate,params,_=scope(snap)
        self.assertNotIn('ДПП',params)
        snap.inputs['coefficient_exclusions']=[['group','Политики','OTHER']]
        predicate,params,_=scope(snap)
        self.assertIn("N'9. OTHER'",predicate);self.assertEqual(params[-1],'OTHER')
        snap.results['moscow_q4_2026']['state']='failed'
        with self.assertRaises(ValidationError):scope(snap)

    def test_field_coverage_compares_whitelisted_sources_and_escapes_catalog_names(self):
        cursor=Mock();cursor.fetchone.side_effect=[(100,80),(100,2)]
        fields=[{'schema':'DBO','table':'sales','column':'Author]Name','type':'nvarchar'},
                {'schema':'motivation','table':'PortalPreparedSales','column':'Даичи_АвторСделкиНаименование','type':'nvarchar'}]
        rows=field_coverage(cursor,fields,'s.ClosedDealDate>=?',[date(2026,10,1)])
        self.assertEqual([row['nonempty'] for row in rows],[80,2])
        query=cursor.execute.call_args_list[0].args
        self.assertIn('s.[Author]]Name]',query[0]);self.assertEqual(query[1],date(2026,10,1))
        self.assertEqual(query[0].count('('),query[0].count(')'))
        self.assertIn('FROM DataWH.dbo.Sales',query[0])

    def test_live_sales_diagnostics_do_not_require_a_prepared_publication(self):
        snap=snapshot();snap.results['moscow_q4_2026']['source']['source_table']='DataWH.dbo.Sales'
        cursor=Mock();connection=Mock()
        cursor.fetchall.side_effect=[[('sales_author',3831,Decimal(1000),50,9)],[],[]]
        report=diagnose(snap,connector=Mock(return_value=(connection,cursor)))
        self.assertFalse(report['uses_prepared']);self.assertEqual(report['current_named_authors'],9)
        self.assertEqual(report['author_field'],'Даичи_АвторСделкиНаименование')
        query=cursor.execute.call_args_list[0].args
        self.assertIn('FROM DataWH.dbo.Sales s',query[0])
        self.assertNotIn('PortalOriginalDealAuthor',query[0]);cursor.fetchone.assert_not_called()
        connection.close.assert_called_once()

    def test_original_author_diagnostics_use_exact_prepared_field_without_erp(self):
        snap=snapshot();snap.results['moscow_q4_2026']['source'].update(source_table='DataWH.motivation.PortalPreparedSales',author_field='PortalOriginalDealAuthor')
        cursor=Mock();connection=Mock()
        cursor.fetchone.side_effect=[('frozen',timezone.now(),date(2025,1,1),date(2027,1,1),RULE),('frozen',)]
        cursor.fetchall.side_effect=[[('sales_author',3800,Decimal(1000),50,9),('sales_author_blank',31,Decimal(100),0,0)],[],[]]
        report=diagnose(snap,connector=Mock(return_value=(connection,cursor)))
        self.assertTrue(report['uses_prepared']);self.assertTrue(report['same_prepared_snapshot'])
        self.assertEqual(report['author_field'],'PortalOriginalDealAuthor');self.assertEqual(report['current_named_authors'],9)
        query=cursor.execute.call_args_list[1].args[0]
        self.assertIn('s.[PortalOriginalDealAuthor]',query)
        self.assertIn('FROM DataWH.motivation.PortalPreparedSales s',query)
        for call in cursor.execute.call_args_list:
            self.assertNotIn('DataWH.erp',call.args[0]);self.assertNotIn('s.Даичи_АвторСделкиНаименование',call.args[0])
        connection.close.assert_called_once()


def setup(test):
    fixture.CalculationTests.setUp(test)
    test.run=execute(start(test.iteration.pk,test.user,test.config,test.iteration.revision),Mock())


class DiagnosticsAccessTests(TestCase):
    def setUp(self):setup(self)

    def test_endpoint_keeps_plan_and_result_snapshots_unchanged(self):
        before=deepcopy(self.run.results);inputs=deepcopy(self.run.inputs)
        with patch('admin_motivation.services.iteration_author_diagnostics.diagnose',return_value={'run':self.run.pk,'coverage':[]}) as reader, \
             patch('admin_motivation.coefficient_iteration_run_views.execute') as calculator:
            response=self.client.post(self.url+'?run='+str(self.run.pk),{'action':'diagnose_authors'})
        self.assertEqual(response.status_code,200);self.assertTrue(response.json()['ok'])
        reader.assert_called_once();calculator.assert_not_called()
        self.run.refresh_from_db();self.assertEqual(self.run.results,before);self.assertEqual(self.run.inputs,inputs)

    def test_other_owner_cannot_request_diagnostics(self):
        user=User.objects.create_superuser('other-author-admin');self.client.force_login(user)
        with patch('admin_motivation.services.iteration_author_diagnostics.diagnose') as reader:
            response=self.client.post(self.url+'?run='+str(self.run.pk),{'action':'diagnose_authors'})
        self.assertEqual(response.status_code,404);reader.assert_not_called()


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class DiagnosticsBrowserTests(StaticLiveServerTestCase):
    def setUp(self):setup(self)

    def test_button_reports_status_and_downloads_diagnostic_without_new_calculation(self):
        from playwright.sync_api import sync_playwright,expect
        report={'run':self.run.pk,'coverage':[{'label':'ERP-документ не найден по DocumentKey','rows':200,'documents':150,'amount_usd':'13000000.12'}],
                'same_prepared_snapshot':True,'samples':[{'document_key':'example'}],'author_fields':[],'note':'План и результаты не изменены.'}
        before=deepcopy(self.run.results)
        def read(*args):time.sleep(.3);return report
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();page.goto(self.live_server_url+self.url+'?run='+str(self.run.pk))
                panel=page.locator('[data-author-diagnostics-panel]')
                with patch('admin_motivation.services.iteration_author_diagnostics.diagnose',side_effect=read):
                    panel.get_by_role('button',name='Проверить источник авторов').click()
                    expect(panel.locator('progress')).to_be_visible()
                    expect(panel.get_by_role('status')).to_contain_text('Проверка завершена')
                expect(panel.locator('table')).to_contain_text('ERP-документ не найден')
                with page.expect_download() as event:panel.get_by_role('link',name='Скачать диагностику авторов').click()
                from pathlib import Path
                result=json.loads(Path(event.value.path()).read_text())
                self.assertEqual(result['samples'],report['samples'])
            finally:context.close();browser.close()
        self.run.refresh_from_db();self.assertEqual(self.run.results,before)
