from datetime import datetime
from unittest.mock import Mock,patch
from django.test import SimpleTestCase,TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from base.testing import authorize_test_case
from .services import prepared_sales as sales


class PreparedSalesSQLTests(SimpleTestCase):
    def test_producer_prepares_all_years_once_without_modifying_shared_sales(self):
        sql=sales.procedure_sql()
        self.assertEqual(sql.count('FROM DataWH.dbo.Sales'),1)
        self.assertIn("ClosedDealDate>='20250101' AND ClosedDealDate<@PreparedTo",sql)
        self.assertNotIn('@FactYear',sql)
        self.assertNotIn('YEAR(rtu.Дата)',sql);self.assertNotIn('YEAR(kr.Дата)',sql)
        self.assertIn('AND Subdivision IN (SELECT SubdivisionKey FROM DataWH.dbo.Subdivisions)',sql)
        self.assertIn('AS PortalOriginalDealAuthor',sql)
        self.assertEqual(sql.count('SET s.DiscountMotivation'),1)
        self.assertIn('s.BallsOuterUSD',sql)
        self.assertNotIn('UPDATE DataWH.dbo.Sales',sql)
        self.assertNotIn('K0',sql);self.assertNotIn('PortalSubdivisionMerge',sql)
        self.assertIn('COUNT_BIG(*) > 1',sql)

    def test_publication_is_atomic_and_old_snapshot_is_not_dropped_during_build(self):
        sql=sales.procedure_sql()
        publish=sql.index('BEGIN TRANSACTION;')
        old_drop=sql.index('DROP TABLE motivation.PortalPreparedSales;')
        commit=sql.index('COMMIT TRANSACTION;')
        active=sql.index('INSERT INTO motivation.PortalPreparedSalesActive')
        self.assertLess(sql.index('IF @Rows=0'),publish)
        self.assertLess(sql.index('CREATE INDEX IX_PortalPreparedSales_Date'),publish)
        self.assertLess(publish,old_drop);self.assertLess(old_drop,active);self.assertLess(active,commit)
        failure=sql[sql.index('BEGIN CATCH'):]
        self.assertIn('ROLLBACK TRANSACTION',failure)
        self.assertNotIn('DROP TABLE motivation.PortalPreparedSales',failure)
        self.assertNotIn('DELETE FROM motivation.PortalPreparedSalesActive',failure)
        self.assertIn("@Resource=N'PortalMotivationCalculation'",sql)

    def test_consumer_uses_snapshot_preserving_scope_and_rejecting_wrong_version_schema_or_year(self):
        original=sales.canonical_preparation()+';WITH fact AS (SELECT * FROM #portal_motivation_sales) SELECT * FROM fact;'
        sql=sales.patch(original)
        self.assertIn('FROM DataWH.motivation.PortalPreparedSales WHERE ClosedDealDate',sql)
        self.assertIn('AND Subdivision IN (SELECT SubdivisionKey FROM DataWH.motivation.PortalCalculationSubdivisions)',sql)
        self.assertIn('ClosedDealDate<DATEFROMPARTS(@FactYear+1,1,1)',sql)
        self.assertNotIn('YEAR(ClosedDealDate)=@FactYear',sql)
        self.assertIn('ClosedDealDate<DATEFROMPARTS(@FactYear+1,1,1)',sql)
        self.assertNotIn('vPriceHistoryERP',sql);self.assertNotIn('#document_sales',sql)
        self.assertNotIn('FROM DataWH.dbo.Sales',sql)
        self.assertIn('r.SourceSchema=@PortalSalesSchema',sql)
        self.assertIn('r.PreparedFrom<=DATEFROMPARTS(@FactYear,1,1)',sql)
        self.assertIn('r.PreparedTo>=DATEFROMPARTS(@FactYear+1,1,1)',sql)
        self.assertIn('IF @FactYear<2025',sql);self.assertIn(sales.RULE,sql)
        self.assertEqual(sales.patch(sql),sql)

    def test_historical_author_fallback_is_preserved_only_for_2025(self):
        original=sales.canonical_preparation()+';WITH fact AS (SELECT * FROM #portal_motivation_sales) SELECT * FROM fact;'
        changed=original.replace('s.Даичи_АвторСделкиНаименование = ds.Даичи_АвторСделкиНаименование',
            "s.Даичи_АвторСделкиНаименование=COALESCE(NULLIF(ds.Даичи_АвторСделкиНаименование,N''),s.Даичи_АвторСделкиНаименование)")
        sql=sales.patch(changed,historical_author=True)
        self.assertIn("COALESCE(NULLIF(Даичи_АвторСделкиНаименование,N''),PortalOriginalDealAuthor)",sql)
        self.assertNotIn('UPDATE #portal_motivation_sales',sales.patch(original))

    def test_changed_discount_math_is_rejected_instead_of_silently_replaced(self):
        sql=sales.canonical_preparation()+';WITH fact AS (SELECT 1)'
        with self.assertRaisesMessage(ValidationError,'отличается'):
            sales.patch(sql.replace('s.BallsOuterUSD','0'))
        with self.assertRaises(ValidationError):sales.patch('SELECT * FROM DataWH.dbo.Sales')
        self.assertEqual(sales.patch('SELECT 1'),'SELECT 1')

    def test_calculation_journal_records_the_published_snapshot_under_the_same_lock(self):
        from types import SimpleNamespace
        from uuid import uuid4
        from .services.execution_tracking import tracked_statement
        job=SimpleNamespace(key=uuid4(),variant='all_2026',fact_year=2026,plan_version='Example plan',
            quarter_share=80,year_share=20,payout_threshold='.8')
        command=tracked_statement(job,'EXEC motivation.PortalCalcAll2026;')
        self.assertIn('$.prepared_sales_run',command)
        self.assertIn('$.prepared_sales_rule',command)
        self.assertIn('PortalPreparedSalesActive',command)
        self.assertLess(command.index('$.prepared_sales_run'),command.index('EXEC motivation.PortalCalcAll2026;'))
        self.assertIn("r.State_=N'completed'",command)

    def test_agent_commands_no_longer_allocate_raw_sales_session_cache(self):
        from types import SimpleNamespace
        from uuid import uuid4
        from .services.calculation_jobs import calculation_command
        def job(variant):
            return SimpleNamespace(key=uuid4(),variant=variant,fact_year=2026,plan_version='Example plan',
                quarter_share=80,year_share=20,payout_threshold='.8')
        for command in (calculation_command(job('main'),job('all_2026')),calculation_command(job('all_2026'))):
            self.assertNotIn('FROM DataWH.dbo.Sales',command)
            self.assertNotIn('#portal_shared_sales',command)
            self.assertIn('$.prepared_sales_run',command)

    def test_installer_has_one_daily_job_at_0800_moscow_and_no_dynamic_user_sql(self):
        cursor=Mock();sales.install(cursor)
        statements=[call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(sum('ALTER PROCEDURE motivation.PortalPrepareSales AS' in s for s in statements),1)
        agent=statements[-1]
        self.assertIn('PortalMotivation_PrepareSales',agent)
        self.assertIn('@freq_type=4,@freq_interval=1',agent)
        self.assertIn('DATEPART(TZOFFSET,SYSDATETIMEOFFSET())-180',agent)
        self.assertIn('DATEADD(hour,8',agent)
        self.assertIn('IF NOT EXISTS',agent)
        self.assertNotIn('sp_start_job',agent)

    def test_start_configures_commits_releases_lock_before_start_and_closes(self):
        conn=Mock();cursor=Mock()
        with patch.object(sales,'install') as install:
            sales.start(lambda *_:(conn,cursor))
        install.assert_called_once_with(cursor)
        statements=[c.args[0] for c in cursor.execute.call_args_list]
        self.assertIn('sp_getapplock',statements[0]);self.assertIn('sp_releaseapplock',statements[1])
        self.assertIn('sp_start_job',statements[2])
        self.assertEqual(conn.commit.call_count,2);conn.close.assert_called_once()

    def test_failed_install_does_not_start_agent_and_rolls_back(self):
        conn=Mock();cursor=Mock()
        with patch.object(sales,'install',side_effect=RuntimeError('failed')):
            with self.assertRaises(RuntimeError):sales.start(lambda *_:(conn,cursor))
        self.assertFalse(any('sp_start_job' in c.args[0] for c in cursor.execute.call_args_list))
        conn.rollback.assert_called_once();conn.close.assert_called_once()

    def test_monitor_reports_ready_snapshot_when_latest_build_failed(self):
        conn=Mock();cursor=Mock();now=datetime(2026,11,11,5)
        cursor.fetchone.side_effect=[(1,),('old-ready',now,123),(now,now,0)]
        cursor.fetchall.return_value=[('new-failed',now,now,'failed','Ошибка подготовки',None,50040)]
        data=sales.monitor(lambda *_:(conn,cursor))
        self.assertEqual(data['ready']['run_id'],'old-ready')
        self.assertEqual(data['history'][0]['state'],'failed');self.assertFalse(data['agent_running'])
        self.assertTrue(data['ready']['completed_at'].endswith('Z'))
        conn.close.assert_called_once()

    def test_cancelled_agent_is_not_left_disabled_as_running(self):
        conn=Mock();cursor=Mock();now=datetime(2026,11,11,5)
        cursor.fetchone.side_effect=[(1,),None,(now,now,3)]
        cursor.fetchall.return_value=[('cancelled',now,None,'running','Расчёт скидок',None,None)]
        data=sales.monitor(lambda *_:(conn,cursor))
        self.assertEqual(data['history'][0]['state'],'interrupted')
        self.assertFalse(data['agent_running'])


class PreparedSalesViewsTests(TestCase):
    def setUp(self):authorize_test_case(self)

    def test_center_has_independent_preparation_card_without_dwh_on_get(self):
        with patch.object(sales,'monitor') as monitor,patch.object(sales,'start') as start:
            response=self.client.get(reverse('motivation_calculations'))
        self.assertContains(response,'Подготовка факта продаж с 2025 года')
        self.assertContains(response,'08:00 МСК');self.assertContains(response,'Подготовить сейчас')
        monitor.assert_not_called();start.assert_not_called()

    def test_post_starts_independent_preparation_not_a_calculation_job(self):
        with patch.object(sales,'start') as start:
            response=self.client.post(reverse('motivation_calculations'),{'action':'prepare_sales'})
        self.assertRedirects(response,reverse('motivation_calculations'))
        start.assert_called_once()
        from .models import MotivationCalculationSchedule
        self.assertEqual(MotivationCalculationSchedule.objects.count(),0)

    def test_monitor_get_is_readonly_and_privileged(self):
        with patch.object(sales,'monitor',return_value={'configured':False,'history':[]}) as monitor,patch.object(sales,'start') as start:
            response=self.client.get(reverse('motivation_prepared_sales_monitor'))
        self.assertEqual(response.status_code,200);monitor.assert_called_once();start.assert_not_called()
        from django.contrib.auth.models import User,Group
        from base.access import MOTIVATION_VIEWER
        user=User.objects.create_user('preparation-viewer');user.groups.add(Group.objects.get(name=MOTIVATION_VIEWER))
        self.client.force_login(user)
        with patch.object(sales,'monitor',return_value={'configured':False,'history':[]}):
            self.assertEqual(self.client.get(reverse('motivation_prepared_sales_monitor')).status_code,200)
        self.assertEqual(self.client.post(reverse('motivation_calculations'),{'action':'prepare_sales'}).status_code,403)


import os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1','Optional Chromium check')
class PreparedSalesBrowserTests(StaticLiveServerTestCase):
    def test_center_manual_preparation_status_and_ready_version(self):
        from playwright.sync_api import sync_playwright,expect
        from django.conf import settings
        from base.testing import authorize_admin
        authorize_admin(self.client)
        data={'configured':True,'status':'Готово','agent_running':False,'history':[
            {'run_id':'test-version','started_at':'2026-11-11T05:00:00Z','completed_at':'2026-11-11T05:01:00Z','state':'completed','stage':'Готово','row_count':123,'error_number':None}],
            'ready':{'run_id':'test-version','completed_at':'2026-11-11T05:01:00Z','row_count':123}}
        with patch.object(sales,'monitor',return_value=data),patch.object(sales,'start') as start:
            with sync_playwright() as p:
                browser=p.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True)
                page=browser.new_page(viewport={'width':1500,'height':1000})
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                page.goto(self.live_server_url+reverse('motivation_calculations'))
                card=page.locator('[data-prepared-sales]')
                expect(card).to_contain_text('08:00 МСК');expect(card.locator('[data-prepared-ready]')).to_contain_text('test-version')
                card.get_by_role('button',name='Подготовить сейчас',exact=True).click()
                expect(page.locator('[data-prepared-ready]')).to_contain_text('строк: 123')
                start.assert_called_once()
                page.screenshot(path='/tmp/prepared-sales-center.png',full_page=True)
                browser.close()
