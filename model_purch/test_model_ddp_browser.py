import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.conf import settings
from base.testing import authorize_admin
from .models import ModelDDPCalculation, ModelDDPRow, ScenarioModel, Freight,GoodsGroup
from .test_model_ddp import PARAMS, RATES

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'), 'Browser checks are opt-in')
class ModelDDPBrowserTests(StaticLiveServerTestCase):
    def test_calculation_edit_guard_and_scenario_snapshot(self):
        user=authorize_admin(self.client)
        GoodsGroup.objects.create(name='ERP goods',container_volume=50,duty_rate=.1)
        batch=ModelDDPCalculation.objects.create(name='DDP моделей',as_of=date(2026,11,22),created_by=user,parameters=PARAMS,rates=RATES)
        row=ModelDDPRow.objects.create(calculation=batch,model_ref='m',model_name='Внутренний блок',price_type='PURCH USD',currency='USD',price=100,source_data={'volume':'1','classification':{'supply_type':'p2','group_goods':'ERP goods'}})
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
                expect(page.locator('.model-ddp')).to_have_attribute('data-workflow-step','review')
                page.get_by_role('link',name='Перейти к расчёту',exact=True).click()
                with page.expect_navigation():page.get_by_role('button',name='Рассчитать DDP',exact=True).click()
                expect(page.locator('.ddp-status-ready')).to_contain_text('Рассчитан')
                expect(page.locator('.ddp-money').nth(0)).to_contain_text('143')
                expect(page.locator('[name="r'+str(row.pk)+'-volume"]')).to_be_visible()
                field=page.locator('[name="r'+str(row.pk)+'-volume"]')
                field.fill('2')
                field.press('Enter')
                self.assertEqual(pool.submit(lambda:ModelDDPCalculation.objects.get(pk=batch.pk).revision).result(),2)
                expect(page.locator('[data-ddp-dirty-status]')).to_contain_text('несохранённые')
                page.locator('.ddp-workflow [data-step-link=review]').click()
                expect(field).to_have_value('2')
                with page.expect_navigation():page.get_by_role('button',name='Сохранить параметры моделей',exact=True).click()
                self.assertEqual(pool.submit(lambda:ModelDDPRow.objects.get(pk=row.pk).overrides['volume']).result(),'2')
                self.assertTrue(pool.submit(lambda:ModelDDPCalculation.objects.get(pk=batch.pk).needs_recalculation).result())
                page.get_by_role('link',name='Перейти к расчёту',exact=True).click()
                page.locator('[name=price_per_container]').fill('1500')
                page.locator('[name="r'+str(row.pk)+'-weight_kg"]').fill('3')
                with page.expect_navigation():page.get_by_role('button',name='Рассчитать DDP',exact=True).click()
                self.assertFalse(pool.submit(lambda:ModelDDPCalculation.objects.get(pk=batch.pk).needs_recalculation).result())
                self.assertEqual(pool.submit(lambda:ModelDDPRow.objects.get(pk=row.pk).overrides['weight_kg']).result(),'3')
                self.assertNotIn('price_per_container',pool.submit(lambda:ModelDDPRow.objects.get(pk=row.pk).overrides).result())
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
                page.get_by_role('button',name='Загрузить цены').click()
                expect(page.locator('.model-ddp')).to_have_attribute('data-workflow-step','review')
                page.locator('.ddp-workflow [data-step-link=load]').click()
                expect(page.locator('[data-saved-load-diagnostics]')).to_contain_text('ONLINE недоступен')
                page.unroute('**/load-stream/')
                page.route('**/load-stream/',lambda route:route.fulfill(content_type='application/x-ndjson',body=json.dumps({'done':True,'error':'SQLSTATE 08001 test'})+'\n'))
                page.goto(url)
                page.get_by_role('button',name='Загрузить цены').click()
                expect(page.locator('[data-load-technical-stage]')).to_contain_text('SQLSTATE 08001')
                expect(page.locator('[data-load-error]')).to_contain_text('Не удалось загрузить цены')
                expect(page.get_by_role('button',name='Загрузить цены')).to_be_enabled()
            finally:browser.close()

    def test_header_stays_fixed_with_page_scroll_and_horizontal_alignment(self):
        user=authorize_admin(self.client)
        batch=ModelDDPCalculation.objects.create(name='Header',as_of=date(2026,11,22),created_by=user)
        ModelDDPRow.objects.bulk_create([ModelDDPRow(calculation=batch,model_ref=str(i),model_name='Model '+str(i),price=100,currency='USD') for i in range(15)])
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,headless=True)
            try:
                page=browser.new_page(viewport={'width':1400,'height':900})
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                page.goto(self.live_server_url+reverse('model_ddp_detail',args=[batch.pk]))
                page.evaluate("window.scrollTo(0,document.querySelector('.ddp-table').getBoundingClientRect().top+window.scrollY+180)")
                floating=page.locator('.ddp-floating-header')
                expect(floating).to_be_visible()
                self.assertLess(abs(floating.bounding_box()['y']),1)
                page.locator('.ddp-table-scroll').evaluate('(el)=>{el.scrollLeft=250}')
                page.wait_for_timeout(100)
                original=page.locator('.ddp-table-scroll th').nth(1).bounding_box()
                copied=floating.locator('th').nth(1).bounding_box()
                self.assertLess(abs(original['x']-copied['x']),2)
                self.assertLess(abs(original['width']-copied['width']),2)
                self.assertTrue(page.locator('.ddp-table-scroll').evaluate('(el)=>el.scrollHeight<=el.clientHeight+1'))
                page.evaluate('window.scrollTo(0,0)')
                expect(floating).to_be_hidden()
            finally:browser.close()

    def test_filtered_model_copy_preview_and_apply(self):
        user=authorize_admin(self.client)
        GoodsGroup.objects.create(name='Copy goods',container_volume=50,duty_rate=.1)
        source=ModelDDPCalculation.objects.create(name='Copy source',as_of=date(2026,10,1),created_by=user,parameters=PARAMS,rates=RATES)
        target=ModelDDPCalculation.objects.create(name='Copy target',as_of=date(2026,11,22),created_by=user,parameters=PARAMS,rates=RATES)
        for batch,is_source in ((source,True),(target,False)):
            ModelDDPRow.objects.bulk_create([ModelDDPRow(calculation=batch,model_ref=str(i),model_name='Copy model '+str(i),
                price_type_ref='k',price_type='PURCH USD',currency='USD',price=200 if is_source else 100,
                source_data={'volume':'1','classification':{'brand':'A' if i==0 else 'B','group_goods':'Copy goods','supply_type':'p2'}},
                overrides={'volume':'2'} if is_source else {}) for i in range(2)])
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as pw,ThreadPoolExecutor(max_workers=1) as pool:
            browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,headless=True)
            try:
                page=browser.new_page()
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                page.goto(self.live_server_url+reverse('model_ddp_detail',args=[target.pk])+'?brand=A&step=calculate')
                page.get_by_text('Копировать из помодельного расчёта',exact=True).click()
                page.locator('[name=source_calculation]').select_option(str(source.pk))
                page.locator('[name=mode]').select_option('rows')
                with page.expect_navigation():page.get_by_role('button',name='Показать, что будет скопировано').click()
                expect(page.locator('[data-model-copy-preview]')).to_contain_text('Совпало: 1')
                with page.expect_navigation():page.get_by_role('button',name='Копировать и пересчитать',exact=True).click()
                expect(page.locator('.alert-success')).to_contain_text('Скопировано строк: 1')
                self.assertIn('brand=A',page.url)
                values=pool.submit(lambda:[
                    (row.model_ref,row.price,row.overrides,row.result)
                    for row in ModelDDPRow.objects.filter(calculation=target).order_by('model_ref')]).result()
                self.assertEqual(values[0][1],100)
                self.assertEqual(values[0][2]['volume'],'2')
                self.assertEqual(values[1][2],{})
                self.assertEqual(values[1][3],{})
            finally:browser.close()
