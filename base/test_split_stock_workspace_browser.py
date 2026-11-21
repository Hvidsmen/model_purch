import importlib.util
import os
from decimal import Decimal
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_split_brand_pages import setup_brands


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class StockWorkspaceBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        setup_brands(self)
        for group in self.scenario.state['groups'].values():
            group['buy']='0'
            for line in group['channels'].values():line['input']='500'
            for power in group['powers'].values():
                power.update(stock_indoor='0',stock_outdoor='0',incoming_indoor='0',incoming_outdoor='0')
        self.scenario.save(update_fields=['state'])

    def test_group_picker_multiple_search_brand_scope_and_all_steps(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page();page.set_default_timeout(10000)
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+self.url+'?tab=plan&brand_page=%5B%22alpha%22,%22beta%22%5D')
            expect(page.locator('[data-group-choice]')).to_have_count(2)
            page.locator('[data-group-picker] > summary').click()
            page.locator('[data-group-choice]').nth(1).uncheck()
            page.locator('[data-group-picker] > summary').click()
            page.get_by_role('spinbutton',name='DDP G',exact=True).fill('123')
            page.get_by_role('spinbutton',name='DDP G',exact=True).press('Control+s')
            expect(page.locator('.split-plan-save-panel')).to_contain_text('Изменения сохранены')
            expect(page.locator('[data-group-choice]:checked')).to_have_count(1)
            for query in ('tab=plan','tab=distribution&section=months','tab=distribution&section=powers','tab=stock&stock_step=opening','tab=stock&stock_step=targets','tab=composition'):
                page.goto(self.live_server_url+self.url+'?'+query+'&brand_page=_all_')
                picker=page.locator('[data-group-picker]');picker.locator('summary').click()
                self.assertGreaterEqual(picker.locator('[data-group-choice]').count(),2)
                picker.locator('[data-group-picker-none]').click()
                expect(picker.locator('[data-group-picker-caption]')).to_have_text('Не выбраны')
                picker.locator('[data-group-choice]').first.check()
                selected=picker.locator('[data-group-choice]').first.get_attribute('data-group-choice')
                self.assertEqual(page.locator('[data-group-picker-excluded][data-plan-group],[data-group-picker-excluded][data-grid-row],[data-group-picker-excluded][data-stock-group],[data-group-picker-excluded][data-composition-group]').count()>0,True)
                picker.locator('[data-group-picker-search]').fill('zzzz-not-a-group')
                expect(picker.locator('[data-group-picker-empty]')).to_be_visible()
                picker.locator('[data-group-picker-search]').fill('')
                picker.locator('[data-group-choice]').nth(1).check()
                self.assertEqual(picker.locator('[data-group-choice]:checked').count(),2)
                picker.locator('[data-group-picker-all]').click()
            page.goto(self.live_server_url+self.url+'?tab=plan&brand_page=alpha')
            expect(page.locator('[data-group-choice]')).to_have_count(1)
            page.goto(self.live_server_url+self.url+'?tab=plan&brand_page=beta')
            expect(page.locator('[data-group-choice]')).to_have_count(1)
            expect(page.locator('[data-group-choice]')).to_be_checked()
            browser.close()

    def test_live_save_keeps_document_filters_and_allows_repeated_saves(self):
        group=next(iter(self.scenario.state['groups'].values()))
        channel=next(iter(self.scenario.state['channels'].values()))
        self.scenario.state['history']={'through':'2026-11-06','loaded_at':'test','first_year':2024,'last_year':2026,
            'groups':[group['ref']['source_key']],'channels':[channel['name'].casefold()],
            'rows':[{'group':group['ref']['source_key'],'channel':channel['name'].casefold(),'year':year,'month':1,'qty':'10','cost':'100','revenue':'150'} for year in (2024,2025)]}
        self.scenario.save(update_fields=['state'])
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+self.url+'?tab=stock&stock_step=targets&brand_page=alpha')
            page.evaluate('window.liveSaveSentinel=123')
            for value in ('17','18'):
                page.get_by_role('spinbutton',name='Целевой процент G',exact=True).fill(value)
                page.get_by_role('spinbutton',name='Целевой процент G',exact=True).press('Control+s')
                expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
                expect(page.get_by_role('spinbutton',name='Целевой процент G',exact=True)).to_have_value(value)
                self.assertEqual(page.evaluate('window.liveSaveSentinel'),123)
            page.goto(self.live_server_url+self.url+'?tab=plan&brand_page=alpha')
            page.evaluate('window.liveSaveSentinel=456')
            page.locator('.split-sales-view > summary').click()
            page.locator('[data-split-metric-choice="incoming"]').check()
            page.locator('[data-history-year-choice]').first.check()
            page.locator('[data-sales-check-filter]').select_option('all')
            page.locator('[data-plan-opening-toggle]').click()
            ddp=page.get_by_role('spinbutton',name='DDP G',exact=True)
            ddp.fill('123')
            ddp.press('Control+s')
            expect(page.locator('.split-plan-save-panel')).to_contain_text('Изменения сохранены')
            expect(page.locator('[data-split-metric-choice="incoming"]')).to_be_checked()
            expect(page.locator('[data-history-year-choice]').first).to_be_checked()
            expect(page.locator('.split-sales-view')).to_have_attribute('open','')
            expect(page.locator('[data-plan-opening-toggle]')).to_have_attribute('aria-expanded','true')
            expect(page.get_by_role('spinbutton',name='DDP G',exact=True)).to_have_value('123')
            self.assertEqual(page.evaluate('window.liveSaveSentinel'),456)
            self.assertEqual(errors,[])
            browser.close()

    def test_live_distribution_selection_and_composition_survive_saving(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page();page.set_default_timeout(10000)
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            for section in ('months','powers'):
                page.goto(self.live_server_url+self.url+'?tab=distribution&section='+section+'&brand_page=alpha')
                page.evaluate('window.liveSaveSentinel=789')
                row=page.locator('[data-grid-row]').first
                row.locator('[data-grid-select]').check()
                field=row.locator('[data-month]').first
                field.fill('25');field.press('Control+s')
                expect(page.locator('[data-grid-message]')).to_contain_text('Показаны сохранённые значения')
                expect(page.locator('[data-grid-select]').first).to_be_checked()
                self.assertEqual(page.evaluate('window.liveSaveSentinel'),789)
                field=page.locator('[data-grid-row]').first.locator('[data-month]').first
                field.fill('26');field.press('Control+s')
                expect(page.locator('[data-grid-message]')).to_contain_text('Показаны сохранённые значения')
            page.goto(self.live_server_url+self.url+'?tab=composition&brand_page=alpha')
            page.evaluate('window.liveSaveSentinel=999')
            page.get_by_label('Способ задания объёма',exact=True).select_option('annual')
            page.get_by_label('Способ задания объёма',exact=True).press('Control+s')
            expect(page.locator('.split-unsaved-dock')).not_to_be_visible()
            expect(page.get_by_label('Способ задания объёма',exact=True)).to_have_value('annual')
            self.assertEqual(page.evaluate('window.liveSaveSentinel'),999)
            browser.close()

    def test_live_save_refresh_failure_can_retry_and_plain_forms_do_not_navigate(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page();page.set_default_timeout(10000)
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            url=self.live_server_url+self.url+'?tab=stock&stock_step=targets&brand_page=alpha'
            page.goto(url);page.evaluate('window.liveSaveSentinel=101')
            def abort_get(route):
                if route.request.method=='GET':route.abort()
                else:route.continue_()
            page.route(url,abort_get)
            field=page.get_by_role('spinbutton',name='Целевой процент G',exact=True)
            field.fill('19');field.press('Control+s')
            expect(page.locator('[data-live-save-error]')).to_contain_text('Сохранение выполнено')
            self.assertTrue(page.locator('.split-workspace').evaluate('el=>el.inert'))
            page.unroute(url,abort_get)
            page.get_by_role('button',name='Обновить расчёты',exact=True).click()
            expect(page.locator('[data-live-save-error]')).to_have_count(0)
            expect(page.get_by_role('spinbutton',name='Целевой процент G',exact=True)).to_have_value('19')
            self.assertEqual(page.evaluate('window.liveSaveSentinel'),101)
            page.goto(self.live_server_url+self.url+'?tab=sources')
            page.evaluate('window.liveSaveSentinel=202')
            page.locator('[name=source_notes]').fill('Проверка сохранения без навигации')
            page.get_by_role('button',name='Сохранить примечания',exact=True).click()
            expect(page.locator('.split-workspace')).not_to_have_attribute('inert','')
            expect(page.locator('[name=source_notes]')).to_have_value('Проверка сохранения без навигации')
            self.assertEqual(page.evaluate('window.liveSaveSentinel'),202)
            browser.close()

    def test_closing_stock_expands_receipts_and_preserves_opening_expansion(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page()
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+self.url+'?tab=plan&brand_page=alpha')
            page.locator('.split-sales-view > summary').click()
            page.locator('[data-split-metric-choice="incoming"]').check()
            page.locator('[data-split-metric-choice="planned_incoming"]').check()
            closing=page.locator('[data-plan-closing-toggle]')
            expect(closing).to_have_attribute('aria-expanded','false')
            receipts=page.locator('[data-plan-group] [data-plan-inventory="existing"]')
            purchases=page.locator('[data-plan-group] [data-plan-inventory="new"]')
            expect(receipts).not_to_be_visible();expect(purchases).not_to_be_visible()
            band=page.locator('[data-history-band="all"]');initial=int(band.get_attribute('colspan'))
            closing.click()
            expect(receipts).to_be_visible();expect(purchases).to_be_visible()
            expect(band).to_have_attribute('colspan',str(initial+2))
            page.locator('[data-plan-opening-toggle]').click()
            expect(band).to_have_attribute('colspan',str(initial+5))
            closing.focus();closing.press('Enter')
            expect(receipts).not_to_be_visible();expect(purchases).not_to_be_visible()
            expect(band).to_have_attribute('colspan',str(initial+3))
            expect(page.locator('[data-plan-opening-toggle]')).to_have_attribute('aria-expanded','true')
            browser.close()

    def test_sales_checks_filter_live_values_and_selected_channels(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page()
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+self.url+'?tab=plan&brand_page=alpha')
            row=page.locator('[data-plan-group]')
            page.evaluate("""() => {const row=document.querySelector('[data-plan-group]');
              row.querySelector('input[name^="ddp_"]').value='100';
              row.querySelectorAll('input[name^="q_"]').forEach(input=>input.value='10');
              row.querySelectorAll('input[name^="markup_"]').forEach(input=>input.value='1.5');
              row.dispatchEvent(new Event('input',{bubbles:true}));} """)
            filters=page.get_by_label('Фильтр по проверкам',exact=True)
            filters.select_option('ready');expect(row).to_be_visible()
            ddp=row.locator('input[type="number"][name^="ddp_"]');ddp.fill('')
            expect(row).not_to_be_visible()
            filters.select_option('ddp');expect(row).to_be_visible()
            ddp.fill('100');expect(row).not_to_be_visible()
            filters.select_option('all')
            quantity=row.locator('input[name^="q_"]').first
            quantity.fill('0');filters.select_option('zero');expect(row).to_be_visible()
            filters.select_option('quantity');expect(row).not_to_be_visible()
            filters.select_option('all');quantity.fill('')
            filters.select_option('quantity');expect(row).to_be_visible()
            filters.select_option('all');quantity.fill('10')
            markup=row.locator('input[name^="markup_"]').first;markup.fill('0')
            filters.select_option('markup');expect(row).to_be_visible()
            page.locator('[data-sales-channel-filter] > summary').click()
            page.locator('[data-sales-channel-choice]').first.uncheck()
            expect(row).not_to_be_visible()
            filters.select_option('ready');expect(row).to_be_visible()
            browser.close()

    def test_tables_use_document_vertical_scroll_and_keep_horizontal_scroll(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1100,'height':700})
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            for query in ('tab=composition','tab=distribution&section=months','tab=distribution&section=powers','tab=stock&stock_step=opening','tab=stock&stock_step=targets','tab=plan'):
                page.goto(self.live_server_url+self.url+'?'+query)
                page.wait_for_selector('.split-table-viewport')
                result=page.evaluate("""() => {
                  const viewport=document.querySelector('.split-table-viewport');
                  const table=viewport.querySelector('table'),body=table.tBodies[0];
                  const sample=body.rows[0];
                  for(let i=0;i<50;i++)body.append(sample.cloneNode(true));
                  table.style.minWidth='2000px';
                  const wrappers=[...document.querySelectorAll('.split-table-viewport,.split-stock-workspace-scroll,.split-stock-table-scroll')];
                  viewport.scrollLeft=100;
                  return {nested:wrappers.some(el=>el.scrollHeight>el.clientHeight+1),
                    horizontal:viewport.scrollLeft>0,page:document.documentElement.scrollHeight>innerHeight};
                }""")
                self.assertEqual(result,{'nested':False,'horizontal':True,'page':True},query)
            browser.close()

    def test_enter_does_not_save_but_shortcut_and_save_button_do(self):
        from playwright.sync_api import sync_playwright,expect
        from concurrent.futures import ThreadPoolExecutor
        def saved_percent():
            scenario=type(self.scenario).objects.get(pk=self.scenario.pk)
            return next(g for g in scenario.state['groups'].values() if g['ref']['name']=='G').get('target_stock_percent')
        def read_percent():
            with ThreadPoolExecutor(max_workers=1) as executor:return executor.submit(saved_percent).result()
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page()
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+self.url+'?tab=stock&stock_step=targets&brand_page=alpha')
            field=page.get_by_role('spinbutton',name='Целевой процент G',exact=True)
            field.fill('17')
            with page.expect_response(lambda r:r.request.method=='POST' and 'preview_stock_workspace' in (r.request.post_data or '')):
                field.press('Enter')
            expect(page.locator('[data-stock-save-status]')).to_contain_text('Изменено')
            self.assertNotEqual(read_percent(),'17')
            field.press('Control+s')
            expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            self.assertEqual(Decimal(read_percent()),17)
            field.fill('18')
            page.locator('[data-stock-save]').focus()
            page.locator('[data-stock-save]').press('Enter')
            expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            self.assertEqual(Decimal(read_percent()),18)
            browser.close()

    def test_opening_targets_proposal_and_single_save_with_brand_guard(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1050})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            base=self.live_server_url+self.url
            page.goto(base+'?tab=stock&stock_step=opening&brand_page=alpha')
            expect(page.locator('[data-stock-group]')).to_have_count(1)
            expect(page.locator('[data-stock-summary]')).to_have_count(2)
            expect(page.locator('[data-stock-group] [data-stock-characteristic]').first).not_to_be_visible()
            page.locator('[data-stock-toggle]').click()
            stock=page.get_by_role('spinbutton',name='Остаток внутренние G 07',exact=True)
            stock.fill('10')
            expect(page.locator('[data-stock-save-status]')).to_contain_text('Изменено 1 групп')
            page.get_by_role('link',name='Целевой запас',exact=False).first.click()
            expect(page.get_by_role('dialog',name='Несохранённые изменения')).to_be_visible()
            page.get_by_role('button',name='Сохранить и перейти',exact=True).click()
            expect(page.locator('[data-stock-workspace]')).to_have_attribute('data-step','targets')
            page.get_by_role('spinbutton',name='Целевой процент G',exact=True).fill('15')
            page.locator('[data-stock-save]').click()
            expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            expect(page.get_by_role('spinbutton',name='Целевой процент G',exact=True)).to_have_value('15')
            page.screenshot(path='/tmp/stock-workspace-targets.png',full_page=True)
            page.get_by_role('link',name='2. Спланировать поставки',exact=False).click()
            expect(page.locator('[data-stock-workspace]')).to_have_attribute('data-step','supplies')
            page.locator('[data-stock-proposal="supplies"]').click()
            page.locator('[data-stock-proposal-preview]').click()
            expect(page.locator('[data-stock-proposal-results]')).to_contain_text('1 150')
            page.locator('[data-stock-proposal-confirm]').click()
            expect(page.get_by_role('spinbutton',name='Новые поставки G',exact=True)).to_have_value('1150.000000')
            page.keyboard.press('Control+s')
            expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            expect(page.locator('[data-stock-group] [data-stock-result="closing"]')).to_have_text('150')
            page.locator('[data-stock-search]').fill('not found')
            expect(page.locator('[data-stock-group]')).not_to_be_visible()
            page.locator('[data-stock-search]').fill('')
            expect(page.locator('[data-stock-group]')).to_be_visible()
            page.locator('.split-stock-controls details').last.locator('summary').click()
            page.locator('[data-stock-characteristics]').check()
            expect(page.locator('[data-stock-group] [data-stock-characteristic]').first).to_be_visible()
            page.locator('[data-stock-nested]').check()
            expect(page.locator('.split-stock-nested-heading')).to_be_visible()
            page.locator('[data-stock-nested]').uncheck()
            page.locator('[data-stock-characteristics]').uncheck()
            page.locator('.split-stock-controls details').last.locator('summary').click()
            page.evaluate('window.scrollTo(0,0)')
            page.screenshot(path='/tmp/stock-workspace-supplies.png',full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
            self.assertEqual(errors,[])
            browser.close()
        self.scenario.refresh_from_db()
        own=self.scenario.state['groups'][self.gid]
        self.assertEqual(own['powers']['07']['stock_indoor'],'10')
        self.assertEqual(Decimal(own['target_stock_percent']),15)
        self.assertEqual(Decimal(own['buy']),1150)
        self.assertEqual(self.scenario.state['groups']['other']['buy'],'0')

    def test_history_is_confirmed_into_table_before_common_save(self):
        from datetime import date
        from unittest.mock import patch
        from playwright.sync_api import sync_playwright,expect
        self.scenario.stock_date=date(2026,11,6)
        for group in self.scenario.state['groups'].values():
            group['forecast_mode']='group';group['forecast_before_total']=None
        self.scenario.save(update_fields=['stock_date','state'])
        group=self.scenario.state['groups'][self.gid]
        facts={(group['ref']['source_key'],model['name'].casefold()):{'qty':Decimal(2),'unknown':False} for power in group['powers'].values() for model in power['models']}
        with patch('base.split_forecast_history.source',return_value=(facts,['Дилеры'])),sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page(viewport={'width':1440,'height':1050})
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            requests=[];errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('request',lambda req:requests.append(req.post_data or '') if req.method=='POST' else None)
            page.goto(self.live_server_url+self.url+'?tab=stock&stock_step=opening&brand_page=alpha')
            page.locator('[data-forecast-history-open]').click()
            page.locator('[data-forecast-history-year]').fill('2025')
            page.locator('[data-forecast-history-preview]').click()
            expect(page.locator('[data-forecast-history-results]')).to_contain_text('G')
            page.locator('[data-forecast-history-apply]').click()
            expect(page.locator('#split-forecast-history')).not_to_be_visible()
            expect(page.get_by_role('spinbutton',name='Прогноз группы G',exact=True)).to_have_value('4.000000')
            expect(page.locator('[data-stock-save-status]')).to_contain_text('Изменено 1 групп')
            self.assertFalse(any('action=apply_forecast_history' in body for body in requests))
            page.keyboard.press('Control+s')
            expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
            expect(page.get_by_role('spinbutton',name='Прогноз группы G',exact=True)).to_have_value('4.000000')
            self.assertEqual(errors,[])
            browser.close()
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['forecast_mode'],'powers')
        self.assertIsNone(self.scenario.state['groups']['other']['forecast_before_total'])

    def test_automatic_percent_and_supplies_save_without_preview(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=stock&stock_step=targets&brand_page=alpha')
                page.locator('[data-stock-bulk-percent]').fill('15')
                with page.expect_navigation():page.locator('[data-stock-save-percent]').click()
                expect(page.locator('[data-stock-field=target_stock_percent]').first).to_have_value('15')
                page.get_by_role('link',name='2. Спланировать поставки',exact=False).click()
                page.locator('[data-stock-proposal=supplies]').click()
                with page.expect_navigation():page.locator('[data-stock-proposal-save]').click()
                expect(page.get_by_role('spinbutton',name='Новые поставки G',exact=True)).to_have_value('1150.000000')
                expect(page.locator('[data-stock-save-status]')).to_have_text('Изменения сохранены')
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.scenario.refresh_from_db();self.assertEqual(Decimal(self.scenario.state['groups'][self.gid]['buy']),1150)

    def test_power_detail_shows_kit_counts_and_updates_them_when_blocks_change(self):
        from datetime import date
        self.scenario.stock_date=date(2026,11,6)
        self.scenario.save(update_fields=['stock_date'])
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1050})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page()
            try:
                page.goto(self.live_server_url+self.url+'?tab=stock&stock_step=opening&brand_page=alpha')
                page.locator('[data-stock-toggle]').first.click()
                power=page.locator('[data-stock-child] [data-stock-power="07"]').first
                expect(power.locator('[data-stock-power-result="stock"]')).to_have_text('0')
                power.locator('[data-stock-field="07:stock_indoor"]').fill('315')
                power.locator('[data-stock-field="07:stock_outdoor"]').fill('42')
                expect(power.locator('[data-stock-power-result="stock"]')).to_have_text('42')
                power.locator('[data-stock-field="07:prior_indoor"]').fill('3')
                power.locator('[data-stock-field="07:prior_outdoor"]').fill('2')
                expect(power.locator('[data-stock-power-result="prior"]')).to_have_text('2')
            finally:context.close();browser.close()
