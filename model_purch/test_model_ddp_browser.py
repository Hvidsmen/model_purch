import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.conf import settings
from base.testing import authorize_admin
from .models import ModelDDPCalculation, ModelDDPRow, ScenarioModel, Freight
from .test_model_ddp import PARAMS, RATES

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'), 'Browser checks are opt-in')
class ModelDDPBrowserTests(StaticLiveServerTestCase):
    def test_calculation_edit_guard_and_scenario_snapshot(self):
        user=authorize_admin(self.client)
        batch=ModelDDPCalculation.objects.create(name='DDP моделей',as_of=date(2026,11,22),created_by=user,parameters=PARAMS,rates=RATES)
        row=ModelDDPRow.objects.create(calculation=batch,model_ref='m',model_name='Внутренний блок',price_type='PURCH USD',currency='USD',price=100,source_data={'volume':'1'})
        source=ScenarioModel.objects.create(name='Логистика 2027',date_start_plan=date(2027,1,1),date_end_plan=date(2027,12,31))
        Freight.objects.create(scenario=source,price_per_container=1200)
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as pw, ThreadPoolExecutor(max_workers=1) as pool:
            browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,headless=True)
            try:
                page=browser.new_page(viewport={'width':1500,'height':1000})
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(self.live_server_url+reverse('model_ddp_detail',args=[batch.pk]))
                with page.expect_navigation():page.get_by_role('button',name='Рассчитать все модели',exact=True).click()
                expect(page.locator('.ddp-status-ready')).to_contain_text('Рассчитан')
                expect(page.locator('.ddp-money').nth(0)).to_contain_text('143')
                expect(page.locator('[name="r'+str(row.pk)+'-volume"]')).to_be_visible()
                field=page.locator('[name="r'+str(row.pk)+'-volume"]')
                field.fill('2')
                field.press('Enter')
                self.assertEqual(pool.submit(lambda:ModelDDPCalculation.objects.get(pk=batch.pk).revision).result(),2)
                page.get_by_role('button',name='Рассчитать все модели',exact=True).click()
                expect(page.locator('[data-ddp-dirty-status]')).to_contain_text('Сначала сохраните')
                with page.expect_navigation():page.get_by_role('button',name='Сохранить параметры строк и рассчитать весь набор').click()
                self.assertEqual(pool.submit(lambda:ModelDDPRow.objects.get(pk=row.pk).overrides['volume']).result(),'2')
                page.get_by_text('Скопировать параметры из сценария закупок',exact=True).click()
                page.locator('[name=source]').select_option(str(source.pk))
                with page.expect_navigation():page.get_by_role('button',name='Показать параметры',exact=True).click()
                expect(page.locator('.ddp-snapshot')).to_contain_text('Логистика 2027')
                with page.expect_navigation():page.get_by_role('button',name='Скопировать показанные параметры',exact=True).click()
                self.assertEqual(pool.submit(lambda:ModelDDPCalculation.objects.get(pk=batch.pk).source_scenario_id).result(),source.pk)
                page.screenshot(path='/tmp/model-ddp-browser.png',full_page=True)
                self.assertEqual(errors,[])
            finally:browser.close()

    def test_load_progress_stream_completion_and_error(self):
        user=authorize_admin(self.client)
        batch=ModelDDPCalculation.objects.create(name='Progress',as_of=date(2026,11,22),created_by=user)
        from playwright.sync_api import sync_playwright,expect
        import json
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,headless=True)
            try:
                page=browser.new_page()
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                payload=[{'stage':'создание индекса пропущено','warning':'ONLINE недоступен: загрузка без индекса'},{'stage':'выполнение запроса PURCH'},{'stage':'чтение результата PURCH','rows':1000},{'done':True,'rows':1200}]
                page.route('**/load-stream/',lambda route:route.fulfill(content_type='application/x-ndjson',body='\n'.join(json.dumps(event) for event in payload)+'\n'))
                url=self.live_server_url+reverse('model_ddp_detail',args=[batch.pk])
                page.goto(url)
                page.get_by_role('button',name='Загрузить цены PURCH').click()
                expect(page.locator('[data-load-status]')).to_be_visible()
                expect(page.locator('[data-load-stage]')).to_contain_text('1200')
                expect(page.locator('[data-load-warning]')).to_contain_text('ONLINE недоступен')
                expect(page.get_by_role('link',name='Показать загруженные цены')).to_be_visible()
                expect(page.locator('[data-load-status] progress')).to_have_attribute('value','1')
                page.unroute('**/load-stream/')
                page.route('**/load-stream/',lambda route:route.fulfill(content_type='application/x-ndjson',body=json.dumps({'done':True,'error':'SQLSTATE 08001 test'})+'\n'))
                page.goto(url)
                page.get_by_role('button',name='Загрузить цены PURCH').click()
                expect(page.locator('[data-load-error]')).to_contain_text('SQLSTATE 08001')
                expect(page.get_by_role('button',name='Загрузить цены PURCH')).to_be_enabled()
            finally:browser.close()
