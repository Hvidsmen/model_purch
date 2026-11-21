from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from .test_split_stock_workspace import StockWorkspaceTests
from .split_workflow import create_scenario
from .split_scenario_copy import candidate


class ScenarioCopyTests(TestCase):
    def setUp(self):
        StockWorkspaceTests.setUp(self)
        self.source=self.scenario
        self.target=create_scenario(self.actor,{'title':'Новый','start':date(2027,1,1),'end':date(2027,12,31),'stock_date':date(2026,11,21)})

    def test_creation_and_all_copy_new_stock_failure_keeps_settings_not_old_inventory(self):
        before=deepcopy(self.source.state)
        with patch('base.split_inventory.source_rows_many',side_effect=ValidationError('Источник недоступен')):
            state,result=candidate(self.target,self.source,self.actor,'all','replace')
        group=next(iter(state['groups'].values()))
        self.assertEqual(group['target_stock_percent'],'15')
        self.assertEqual(group['ddp'],next(iter(before['groups'].values()))['ddp'])
        self.assertTrue(group['channels'])
        self.assertTrue(all(p['stock_indoor'] is None for p in group['powers'].values()))
        self.assertNotIn('forecast_history_source',group)
        self.assertTrue(any('Остатки на новую дату' in note for note in result['notes']))
        self.assertEqual(self.source.state,before)
        self.assertEqual(self.target.state['groups'],{})

    def test_different_stock_date_loads_actual_new_cutoff_caps_forecast_and_recalculates_buy(self):
        group=next(iter(self.source.state['groups'].values()))
        facts={(group['ref']['source_key'],model['name'].casefold()):{'qty':Decimal(100),'unknown':False} for p in group['powers'].values() for model in p['models']}
        with patch('base.split_inventory.source_rows_many',return_value={self.gid:([],[])}),patch('base.split_forecast_history.source',return_value=(facts,['Дилеры','Розница'])),patch('base.split_forecast_history.timezone.localdate',return_value=date(2026,11,21)):
            state,result=candidate(self.target,self.source,self.actor,'all','replace')
        copied=next(iter(state['groups'].values()))
        self.assertEqual(copied['inventory_source']['stock_date'],'2026-11-21')
        self.assertTrue(all(p['stock_indoor']=='0' and Decimal(p['forecast_before'])==0 for p in copied['powers'].values()))
        self.assertEqual(copied['buy_source']['kind'],'calculated')
        self.assertGreater(Decimal(copied['buy']),0)

    def test_same_dates_copy_and_per_step_append_preserve_manual_policy(self):
        self.target.stock_date=self.source.stock_date
        state,result=candidate(self.target,self.source,self.actor,'all','replace')
        self.target.state=state
        group=next(iter(state['groups'].values()))
        self.assertEqual(group['powers']['07']['stock_indoor'],self.power['stock_indoor'])
        group['target_stock_percent']='0'
        updated,_=candidate(self.target,self.source,self.actor,'targets','append')
        self.assertEqual(next(iter(updated['groups'].values()))['target_stock_percent'],'0')
        updated,_=candidate(self.target,self.source,self.actor,'targets','replace')
        self.assertEqual(next(iter(updated['groups'].values()))['target_stock_percent'],'15')

    def test_quantity_seasonality_is_remapped_to_new_year_and_access_checked(self):
        from django.contrib.auth.models import User
        from django.core.exceptions import PermissionDenied
        group=next(iter(self.source.state['groups'].values()))
        for line in group['channels'].values():
            line['allocation']='quantity';line['month_quantities']={f'2027-{m:02}':'10' if m==1 else '0' for m in range(1,13)}
        self.target.start=date(2028,1,1);self.target.end=date(2028,12,31)
        state,_=candidate(self.target,self.source,self.actor,'composition','replace');self.target.state=state
        state,_=candidate(self.target,self.source,self.actor,'months','replace')
        for line in next(iter(state['groups'].values()))['channels'].values():
            self.assertEqual(Decimal(line['months']['2028-01']),100)
            self.assertNotIn('2027-01',line['months'])
            self.assertEqual(line['allocation'],'shares')
        outsider=User.objects.create_user('copy-outsider')
        with patch('base.split_inventory.source_rows_many') as inventory:
            with self.assertRaises(PermissionDenied):candidate(self.target,self.source,outsider,'all')
            inventory.assert_not_called()

    def test_endpoint_revision_and_form_creation_and_controls_on_every_step(self):
        endpoint=reverse('split_scenario',args=[self.target.pk])
        response=self.client.post(endpoint,{'action':'copy_scenario_step','revision':999,'copy_source':self.source.pk,'copy_stage':'all'})
        self.assertEqual(response.status_code,400)
        with patch('base.split_inventory.source_rows_many',side_effect=ValidationError('Нет связи')):
            response=self.client.post(reverse('sales_planning_split'),{'title':'Копия','start':'2027-01-01','end':'2027-12-31','stock_date':'2026-11-21','copy_source':self.source.pk})
        self.assertEqual(response.status_code,302)
        for query in ({'tab':'composition'},{'tab':'distribution','section':'months'},{'tab':'distribution','section':'powers'},{'tab':'stock','stock_step':'opening'},{'tab':'stock','stock_step':'targets'},{'tab':'plan'},{'tab':'checks'}):
            response=self.client.get(endpoint,query)
            self.assertContains(response,'data-copy-scenario-form')
        self.target.status='approved'
        with self.assertRaises(ValidationError):candidate(self.target,self.source,self.actor,'all')


import importlib.util,os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class ScenarioCopyBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        ScenarioCopyTests.setUp(self)
        self.target.stock_date=self.source.stock_date;self.target.save(update_fields=['stock_date'])

    def test_copy_all_on_check_step_saves_without_reloading_and_keeps_source(self):
        from playwright.sync_api import sync_playwright,expect
        before=deepcopy(self.source.state)
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page();page.set_default_timeout(10000)
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+reverse('split_scenario',args=[self.target.pk])+'?tab=checks')
            page.evaluate('window.copySentinel=42')
            page.get_by_text('Скопировать данные из предыдущего сценария',exact=True).click()
            form=page.locator('[data-copy-scenario-form]')
            form.locator('[name=copy_source]').select_option(str(self.source.pk))
            form.locator('[name=copy_fill]').select_option('replace')
            with page.expect_response(lambda r:r.request.method=='POST') as copied_response:
                form.get_by_role('button',name='Скопировать и сохранить',exact=True).click()
            self.assertEqual(copied_response.value.status,200)
            expect(page.locator('[data-copy-scenario-form] [name=revision]')).to_have_value('2')
            self.assertEqual(page.evaluate('window.copySentinel'),42)
            browser.close()
        self.target.refresh_from_db();self.source.refresh_from_db()
        self.assertTrue(self.target.state['groups']);self.assertEqual(self.target.status,'draft')
        self.assertEqual(self.source.state,before)
