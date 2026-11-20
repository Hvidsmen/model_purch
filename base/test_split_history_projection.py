from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import importlib.util,os
from unittest import skipUnless
from unittest.mock import Mock,patch
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_split_power_grid as fixtures
from .split_history_projection import presentation
from .split_history import load_history
from .split_matrix_totals import enrich_history


def setup(test):
    fixtures.PowerGridTests.setUp(test)
    test.scenario.stock_date=date(2026,11,6)
    group=test.scenario.state['groups'][test.gid];group.update(forecast_mode='group',forecast_before_total='30')
    channels=list(test.scenario.state['channels'].values())
    def snap(through,multiplier):
        return {'quantity_unit':'sets-v1','through':through,'loaded_at':'test','first_year':2024,'last_year':2026,'groups':[group['ref']['source_key']],
                'channels':[ch['name'].casefold() for ch in channels],
                'rows':[{'group':group['ref']['source_key'],'channel':ch['name'].casefold(),'year':2026,'month':11,
                         'qty':str(20*index*multiplier),'cost':str(100*index*multiplier),'revenue':str(150*index*multiplier)} for index,ch in enumerate(channels,1)]}
    snapshot=snap('2026-11-20',1.5);snapshot['projection_snapshot']=snap('2026-11-06',1)
    test.scenario.state['history']=snapshot;test.scenario.save(update_fields=['stock_date','state'])


class HistoryProjectionTests(TestCase):
    setUp=setup
    def year(self,history):return next(year for year in history['groups'][0]['years'] if year['year']==2026)
    def test_projection_uses_aligned_fact_and_forecast_in_kits_without_double_count(self):
        before=deepcopy(self.scenario.state);history,info=presentation(self.scenario)
        self.assertTrue(info['active']);year=self.year(history)
        self.assertEqual(sum(cell['qty'] for cell in year['annual']),90) # 60 actual kits + 30 forecast kits.
        self.assertAlmostEqual(year['annual'][0]['qty'],D(30),places=5)
        self.assertEqual(sum(month['cells'][0]['qty']+month['cells'][1]['qty'] for month in year['months']),90)
        self.assertEqual(sum(cell['cost'] for cell in year['annual']),450)
        self.assertTrue(all(abs(cell['markup']-D('1.5'))<D('.000001') for cell in year['annual']))
        total=enrich_history(history)['years'][-1]['total'];self.assertEqual(total['qty'],90)
        raw,off=presentation(self.scenario,False)
        self.assertEqual(sum(cell['qty'] for cell in self.year(raw)['annual']),90)
        self.assertFalse(off['active']);self.assertEqual(self.scenario.state,before)
    def test_monthly_forecast_keeps_saved_months(self):
        group=self.scenario.state['groups'][self.gid];group.update(forecast_mode='monthly',forecast={'2026-11':'0','2026-12':'30'})
        year=self.year(presentation(self.scenario)[0])
        self.assertEqual(sum(cell['qty'] for cell in year['months'][10]['cells']),60)
        self.assertEqual(sum(cell['qty'] for cell in year['months'][11]['cells']),30)
    def test_missing_forecast_is_unknown_and_missing_cutoff_requests_reload(self):
        self.scenario.state['groups'][self.gid]['forecast_before_total']=None
        history,info=presentation(self.scenario);self.assertEqual(len(info['issues']),1)
        self.assertTrue(all(cell['qty'] is None for cell in self.year(history)['annual']))
        self.scenario.state['history'].pop('projection_snapshot')
        history,info=presentation(self.scenario);self.assertFalse(info['active']);self.assertIn('Загрузить факт',info['note'])
        self.assertEqual(sum(cell['qty'] for cell in self.year(history)['annual']),90)
    def test_explicit_fact_load_saves_two_distinct_cutoffs(self):
        connection,cursor=Mock(),Mock();cursor.fetchall.side_effect=[[('G','Дилеры',2026,11,30,150,100,'IN'),('G','Дилеры',2026,11,30,150,100,'OUT')],[('G','Дилеры',2026,11,20,100,50,'IN'),('G','Дилеры',2026,11,20,100,50,'OUT')]]
        with patch('base.split_history.connect_database',return_value=(connection,cursor)),patch('base.split_history.timezone.localdate',return_value=date(2026,11,20)):
            snapshot=load_history(self.scenario)
        self.assertEqual(snapshot['through'],'2026-11-20')
        self.assertEqual(snapshot['projection_snapshot']['through'],'2026-11-06')
        self.assertEqual(cursor.execute.call_args_list[1].args[2],'2026-11-07')
        self.assertEqual(cursor.close.call_count,2);self.assertEqual(connection.close.call_count,2)
    def test_toggle_cookie_changes_display_without_writing_scenario(self):
        before=deepcopy(self.scenario.state);revision=self.scenario.revision
        response=self.client.get(self.url,{'tab':'plan'})
        self.assertContains(response,'Факт + прогноз 2026')
        response=self.client.get(self.url,{'tab':'plan','history_forecast':'0'})
        self.assertNotContains(response,'Факт + прогноз 2026')
        self.assertEqual(response.cookies['split-history-forecast'].value,'0')
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before);self.assertEqual(self.scenario.revision,revision)


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class HistoryProjectionBrowserTests(StaticLiveServerTestCase):
    setUp=setup
    def test_toggle_and_cancel_navigation_preserve_unsaved_plan(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=plan')
                expect(page.locator('thead [data-history-toggle]').first).to_contain_text('Факт + прогноз 2026')
                page.locator('.split-sales-view > summary').click()
                toggle=page.locator('[data-history-forecast-choice]');expect(toggle).to_be_checked()
                plan=page.locator('[name^="q_"]').first;original=plan.input_value();plan.fill('17')
                toggle.click()
                guard=page.locator('.split-unsaved-dialog');expect(guard).to_be_visible()
                guard.get_by_role('button',name='Остаться на странице',exact=True).click()
                expect(plan).to_have_value('17');expect(toggle).to_be_checked()
                page.get_by_role('button',name='Отменить все изменения',exact=True).click()
                expect(plan).to_have_value(original)
                with page.expect_navigation():toggle.click()
                expect(page.locator('thead [data-history-toggle]').first).to_contain_text('Факт 2026')
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
