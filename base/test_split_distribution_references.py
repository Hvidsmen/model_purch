import importlib.util
import os
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle as fixtures


def setup_references(test):
    fixtures.SplitCycleTests.setUp(test)
    fixtures.SplitCycleTests.complete(test)
    test.gid, group = next(iter(test.scenario.state['groups'].items()))
    test.reference = {'sales_plan_group': 'RAC', 'brand': 'Brand', 'outdoor_type': 'INV', 'factory': 'Factory <A>'}
    group['ref']['details'] = dict(test.reference)
    test.scenario.save(update_fields=['state'])
    test.url = reverse('split_scenario', args=[test.scenario.pk])


class DistributionReferenceTests(TestCase):
    def setUp(self):
        setup_references(self)

    def test_months_and_powers_share_plan_reference_values_without_source_reads(self):
        with patch('model_purch.conns.connect_database', side_effect=AssertionError('No DWH read')):
            for section, count in [('months', 2), ('powers', 1)]:
                response = self.client.get(self.url, {'tab': 'distribution', 'section': section})
                rows = response.context['distribution_overview']['rows']
                self.assertEqual(len(rows), count)
                self.assertTrue(all(row['reference'] == self.reference for row in rows))
                for label in ['Группа планов продаж', 'Марка', 'Тип наружного блока', 'Завод']:
                    self.assertContains(response, label)
                self.assertContains(response, 'Factory &lt;A&gt;')
                self.assertNotContains(response, 'Factory <A>')
            stock = self.client.get(self.url, {'tab': 'stock'})
            self.assertEqual(stock.context['stock_overview']['groups'][0]['reference'], self.reference)
            self.assertContains(stock, 'Factory &lt;A&gt;')
            card_url = reverse('sales_planning_group_detail', args=[self.group.pk])
            for tab in ['plan', 'distribution', 'stock', 'detail']:
                response = self.client.get(self.url, {'tab': tab})
                self.assertContains(response, 'href="' + card_url + '" target="_blank"')

    def test_old_snapshot_uses_local_nomenclature_and_missing_values_stay_blank(self):
        group = self.scenario.state['groups'][self.gid]
        group['ref'].pop('details')
        good = self.models[0]
        good.sales_plan_group = 'RAC'
        good.outdoor_block_type = 'INV'
        good.manufacturer = 'Actual factory'
        good.save()
        self.scenario.save(update_fields=['state'])
        rows = self.client.get(self.url, {'tab': 'distribution'}).context['distribution_overview']['rows']
        self.assertEqual(rows[0]['reference']['factory'], 'Actual factory')
        self.assertEqual(rows[0]['reference']['sales_plan_group'], 'RAC')


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Requires Chromium')
class DistributionReferenceBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        setup_references(self)

    def test_reference_columns_leave_month_paste_and_power_table_usable(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1800, 'height': 1100})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + self.url + '?tab=distribution&section=months')
                row = page.locator('[data-grid-row]').first
                expect(row.locator('.split-distribution-reference')).to_have_text(['RAC', 'Brand', 'INV', 'Factory <A>'])
                expect(row.locator('.split-month-channel-cell')).to_have_text('Дилеры')
                first = row.locator('[data-month]').first
                first.evaluate("""el => {
                    const data = new DataTransfer();
                    data.setData('text/plain', ['100', ...Array(11).fill('0')].join('\t'));
                    el.dispatchEvent(new ClipboardEvent('paste', {clipboardData:data, bubbles:true, cancelable:true}));
                }""")
                expect(first).to_have_value('100')
                expect(row.locator('[data-month]').nth(1)).to_have_value('0')
                with page.expect_navigation():
                    page.get_by_role('button', name='Сохранить изменения', exact=True).click()
                expect(first).to_have_value('100')
                expect(row.locator('.split-distribution-reference').last).to_have_text('Factory <A>')
                page.goto(self.live_server_url + self.url + '?tab=distribution&section=powers')
                expect(page.locator('[data-grid-row]').first.locator('.split-distribution-reference')).to_have_text(['RAC', 'Brand', 'INV', 'Factory <A>'])
                page.locator('[data-power-config] summary').first.click()
                expect(page.locator('[data-power-param="factor"]').first).to_be_visible()
                page.goto(self.live_server_url + self.url + '?tab=stock')
                stock_row = page.locator('.split-stock-group-row')
                expect(stock_row.locator('.split-distribution-reference')).to_have_text(['RAC', 'Brand', 'INV', 'Factory <A>'])
                expect(page.locator('[data-stock-fullspan]').first).to_have_attribute('colspan', '13')
                page.locator('[data-stock-channel-toggle]').click()
                expect(page.locator('[data-stock-fullspan]').first).to_have_attribute('colspan', '15')
                page.get_by_role('button', name='Раскрыть группу G', exact=True).click()
                page.locator('[data-stock-band-toggle="stock"]').click()
                expect(page.locator('[data-stock-fullspan]').first).to_have_attribute('colspan', '17')
                expect(page.get_by_label('Сток внутренние G 07', exact=True)).to_be_visible()
                page.get_by_label('Новые поставки G', exact=True).fill('5')
                page.get_by_role('button', name='Сохранить поставки', exact=True).click()
                expect(page.get_by_label('Новые поставки G', exact=True)).to_have_value('5')
                page.get_by_label('Новые поставки G', exact=True).fill('9')
                with page.expect_popup() as popup:
                    stock_row.locator('.split-group-card-link').click()
                card = popup.value
                card.wait_for_load_state()
                self.assertIn(reverse('sales_planning_group_detail', args=[self.group.pk]), card.url)
                card.close()
                expect(page.get_by_label('Новые поставки G', exact=True)).to_have_value('9')
                expect(page.locator('.split-stock-power-row').first).to_be_visible()
                expect(stock_row.locator('.split-distribution-reference')).to_have_text(['RAC', 'Brand', 'INV', 'Factory <A>'])
                expect(stock_row.locator('td').last).to_have_text('75')
            finally:
                context.close()
                browser.close()

    def test_reference_filters_across_tabs_preserve_hidden_values_and_tree(self):
        from copy import deepcopy
        from playwright.sync_api import sync_playwright, expect
        second = deepcopy(self.scenario.state['groups'][self.gid])
        second['ref']['name'] = 'Other group'
        second['ref']['details'] = {'sales_plan_group': 'Other', 'brand': 'Other brand', 'outdoor_type': 'OFF', 'factory': 'Other factory'}
        self.scenario.state['groups']['other'] = second
        self.scenario.save(update_fields=['state'])
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1800, 'height': 1100})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                for tab in ['plan', 'distribution&section=months', 'distribution&section=powers', 'stock']:
                    page.goto(self.live_server_url + self.url + '?tab=' + tab)
                    filters = page.locator('[data-reference-filter]')
                    expect(filters).to_have_count(4)
                    page.locator('[data-reference-filter="factory"]').select_option('Factory <A>', force=True)
                    expect(page.locator('[data-reference-filter-count]')).to_have_text('Показано групп: 1')
                    rows = page.locator('[data-plan-group="other"], [data-stock-group="other"], [data-grid-row][data-gid="other"]')
                    for row in rows.all():
                        expect(row).not_to_be_visible()
                    if tab == 'plan':
                        page.locator('[data-reference-reset]').click()
                        page.get_by_label('Количество Other group Дилеры', exact=True).fill('17')
                        page.locator('[data-reference-filter="factory"]').select_option('Factory <A>', force=True)
                        with page.expect_navigation():
                            page.get_by_role('button', name='Сохранить объёмы и наценки', exact=True).click()
                        expect(page.locator('[data-reference-filter="factory"]')).to_have_values(['Factory <A>'])
                        page.locator('[data-reference-reset]').click()
                        expect(page.get_by_label('Количество Other group Дилеры', exact=True)).to_have_value('17')
                        page.locator('[data-reference-filter="factory"]').select_option('Factory <A>', force=True)
                        page.locator('[data-plan-group="' + self.gid + '"] [data-plan-group-toggle]').click()
                        expect(page.locator('[data-plan-power-group="' + self.gid + '"]').first).to_be_visible()
                        expect(page.locator('[data-plan-power-group="other"]').first).not_to_be_visible()
                    page.locator('[data-reference-filter="brand"]').select_option('Other brand', force=True)
                    expect(page.locator('[data-reference-filter-count]')).to_have_text('Нет групп по выбранным условиям.')
                    page.locator('[data-reference-reset]').click()
                    expect(page.locator('[data-reference-filter-count]')).to_have_text('Показано групп: 2')
                page.locator('[data-reference-filter="factory"]').select_option('Factory <A>', force=True)
                page.get_by_label('Новые поставки G', exact=True).fill('5')
                page.locator('[data-stock-group="' + self.gid + '"]').get_by_role('button', name='Сохранить поставки').click()
                expect(page.locator('[data-reference-filter="factory"]')).to_have_values(['Factory <A>'])
                page.locator('[data-reference-reset]').click()
                expect(page.get_by_label('Новые поставки Other group', exact=True)).to_have_value('0')
            finally:
                context.close()
                browser.close()

    def test_bulk_modes_select_visible_rows_and_save_only_changed_rows(self):
        from copy import deepcopy
        from playwright.sync_api import sync_playwright, expect
        second = deepcopy(self.scenario.state['groups'][self.gid])
        second['ref']['details'] = {**self.reference, 'factory': 'Other factory'}
        self.scenario.state['groups']['other'] = second
        self.scenario.save(update_fields=['state'])
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1800, 'height': 1100})
            try:
                context.add_cookies([{'name':'sessionid', 'value':self.client.cookies['sessionid'].value, 'url':self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + self.url + '?tab=distribution&section=months')
                first = page.locator('[data-grid-row]').first
                other = page.locator('[data-grid-row][data-gid="other"]').first
                previous = other.locator('[data-month]').first.input_value()
                first.locator('[data-grid-select]').check()
                page.locator('[data-grid-bulk-mode]').select_option('equal')
                page.locator('[data-grid-bulk-apply]').click()
                expect(first.locator('[data-mode-button="equal"]')).to_have_attribute('aria-pressed', 'true')
                expect(other.locator('[data-month]').first).to_have_value(previous)
                page.locator('[data-reference-filter="factory"]').select_option('Factory <A>', force=True)
                page.locator('[data-grid-select-all]').click()
                expect(page.locator('[data-grid-selected-count]')).to_have_text('Выбрано строк: 2')
                page.locator('[data-grid-bulk-apply]').click()
                expect(page.locator('[data-grid-bulk-result]')).to_contain_text('Обработано строк: 2')
                with page.expect_navigation():
                    page.locator('[data-grid-save]').click()
                expect(first.locator('[data-mode-button="equal"]')).to_have_attribute('aria-pressed', 'true')
                page.locator('[data-grid-select-all]').click()
                page.locator('[data-grid-bulk-mode]').select_option('manual')
                page.locator('[data-grid-bulk-apply]').click()
                expect(first.locator('[data-month]').first).to_be_editable()
                page.locator('[data-grid-select-none]').click()
                expect(page.locator('[data-grid-selected-count]')).to_have_text('Выбрано строк: 0')
                expect(page.locator('[data-grid-bulk-apply]')).to_be_disabled()
                page.locator('[data-grid-reset]').click()
                from .split_engine import planning_months
                from django.core.exceptions import ValidationError
                proposal = {'values': {month: '100' if month.endswith('-01') else '0' for month in planning_months(self.scenario)}, 'token': 'test-token', 'basis': {'rule_label': 'Из факта'}}
                page.locator('[data-grid-select-all]').click()
                page.locator('[data-grid-bulk-mode]').select_option('fact')
                with patch('base.split_month_grid.preview', return_value=proposal) as preview:
                    page.locator('[data-grid-bulk-apply]').click()
                    expect(page.locator('[data-grid-bulk-result]')).to_contain_text('Обработано строк: 2')
                    expect(first.locator('[data-month]').first).to_have_value('100')
                    self.assertEqual(preview.call_count, 2)
                page.locator('[data-grid-reset]').click()
                with patch('base.split_month_grid.preview', side_effect=ValidationError('Нет факта')):
                    page.locator('[data-grid-bulk-apply]').click()
                    expect(page.locator('[data-grid-bulk-result]')).to_contain_text('Не удалось обработать: 2')
                page.locator('[data-grid-reset]').click()
                page.goto(self.live_server_url + self.url + '?tab=distribution&section=powers')
                page.locator('[data-grid-select-all]').click()
                expect(page.locator('[data-grid-selected-count]')).to_have_text('Выбрано строк: 1')
                page.locator('[data-grid-bulk-mode]').select_option('equal')
                page.locator('[data-grid-bulk-apply]').click()
                expect(page.locator('[data-grid-row]').first.locator('[data-mode-button="equal"]')).to_have_attribute('aria-pressed', 'true')
            finally:
                context.close()
                browser.close()

    def test_flat_and_nested_views_keep_inputs_filters_and_selection(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width':1800,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                for tab in ['plan','distribution&section=months','distribution&section=powers','stock']:
                    page.goto(self.live_server_url + self.url + '?tab=' + tab)
                    table = page.locator('.split-plan-matrix, .split-stock-table, [data-month-grid] table')
                    first_header = table.locator('thead tr').first.locator('th').first
                    page.locator('[data-reference-columns]').click()
                    expect(first_header).not_to_be_visible()
                    page.locator('[data-reference-columns]').click()
                    expect(first_header).to_be_visible()
                    page.locator('button[data-table-view="nested"]').click()
                    expect(table).to_have_attribute('data-table-view','nested')
                    page.locator('[data-view-collapse]').click()
                    leaf = page.locator('[data-plan-group], [data-grid-row], [data-stock-group]').first
                    expect(leaf).not_to_be_visible()
                    page.locator('button[data-table-view="flat"]').click()
                    expect(leaf).to_be_visible()
                    page.locator('button[data-table-view="nested"]').click()
                    expect(leaf).not_to_be_visible()
                    page.locator('[data-view-expand]').click()
                    expect(leaf).to_be_visible()
                    if tab == 'stock':
                        page.get_by_label('Новые поставки G',exact=True).fill('19')
                        page.locator('button[data-table-view="flat"]').click()
                        expect(page.get_by_label('Новые поставки G',exact=True)).to_have_value('19')
                        page.locator('button[data-table-view="nested"]').click()
                        page.locator('[data-stock-band-toggle="stock"]').click()
                        width = table.locator('.split-view-section th').first.get_attribute('colspan')
                        self.assertEqual(width, '15')
                        page.get_by_label('Новые поставки G',exact=True).fill('0')
                    if tab.startswith('distribution'):
                        page.locator('[data-grid-select-all]').click()
                        self.assertNotEqual(page.locator('[data-grid-selected-count]').inner_text(), 'Выбрано строк: 0')
                        page.locator('[data-view-collapse]').click()
                        expect(page.locator('[data-grid-selected-count]')).to_have_text('Выбрано строк: 0')
                self.assertEqual(errors, [])
            finally:
                context.close()
                browser.close()

    def test_workspace_steps_cancel_and_atomic_stock_save(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1800,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page()
                page.goto(self.live_server_url+self.url+'?tab=composition')
                expect(page.locator('.split-workflow-step')).to_have_count(5)
                expect(page.get_by_role('button',name='Добавить группы со стока',exact=True)).to_be_visible()
                page.locator('.split-workflow-step[href="?tab=plan"]').click()
                page.screenshot(path='/tmp/split-workspace-ux.png',full_page=False)
                quantity=page.get_by_label('Количество G Дилеры',exact=True)
                before=quantity.input_value()
                quantity.fill('23')
                expect(quantity).to_have_class('split-input-changed')
                page.get_by_role('button',name='Отменить изменения',exact=True).click()
                expect(quantity).to_have_value(before)
                quantity.fill('24')
                with page.expect_navigation():
                    page.get_by_role('button',name='Сохранить объёмы и наценки',exact=True).click()
                expect(quantity).to_have_value('24')
                page.locator('.split-workflow-step[href="?tab=stock"]').click()
                expect(page.locator('.split-workspace')).to_have_attribute('data-workspace-ux-ready','true')
                buy=page.get_by_label('Новые поставки G',exact=True)
                buy.fill('12')
                expect(page.get_by_role('button',name='Сохранить изменения стока',exact=True)).to_be_enabled()
                with page.expect_navigation():
                    page.get_by_role('button',name='Сохранить изменения стока',exact=True).click()
                expect(buy).to_have_value('12')
            finally:
                context.close()
                browser.close()

    def test_search_multiple_reference_values_and_mixed_factories(self):
        from copy import deepcopy
        from playwright.sync_api import sync_playwright, expect
        group=self.scenario.state['groups'][self.gid]
        group['ref']['details']['factory']='Factory <A>, Mixed factory'
        other=deepcopy(group)
        other['ref']['details']={**self.reference,'factory':'Other factory','brand':'Other brand'}
        self.scenario.state['groups']['other']=other
        self.scenario.save(update_fields=['state'])
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1600,'height':1000})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page()
                page.goto(self.live_server_url+self.url+'?tab=plan')
                page.get_by_label('Завод: выбрать значения',exact=True).click()
                search=page.get_by_role('searchbox',name='Поиск: Завод',exact=True)
                search.fill('mixed')
                page.get_by_role('checkbox',name='Mixed factory',exact=True).check()
                expect(page.locator('[data-reference-filter-count]')).to_have_text('Показано групп: 1')
                search.fill('other')
                page.get_by_role('checkbox',name='Other factory',exact=True).check()
                expect(page.locator('[data-reference-filter-count]')).to_have_text('Показано групп: 2')
                search.fill('no such factory')
                expect(page.locator('.split-reference-multi[open]').get_by_text('Ничего не найдено',exact=True)).to_be_visible()
                expect(page.locator('[data-reference-filter="factory"]')).to_have_values(['Mixed factory','Other factory'])
                search.press('Escape')
                page.get_by_label('Марка: выбрать значения',exact=True).click()
                page.get_by_role('checkbox',name='Brand',exact=True).check()
                expect(page.locator('[data-reference-filter-count]')).to_have_text('Показано групп: 1')
                page.locator('.split-workflow-step[href="?tab=stock"]').click()
                expect(page.locator('[data-reference-filter="factory"]')).to_have_values(['Mixed factory','Other factory'])
                expect(page.locator('[data-reference-filter-count]')).to_have_text('Показано групп: 1')
                page.get_by_role('button',name='Сбросить справочные фильтры',exact=True).click()
                expect(page.locator('[data-reference-filter-count]')).to_have_text('Показано групп: 2')
                page.get_by_label('Завод: выбрать значения',exact=True).click()
                page.get_by_role('searchbox',name='Поиск: Завод',exact=True).fill('other')
                page.get_by_role('button',name='Выбрать найденные',exact=True).click()
                expect(page.locator('[data-reference-filter="factory"]')).to_have_values(['Other factory'])
                page.get_by_role('button',name='Снять выбор',exact=True).click()
                expect(page.locator('[data-reference-filter="factory"]')).to_have_values([])
            finally:
                context.close()
                browser.close()

    def test_progress_reports_completed_rows_and_failures(self):
        from threading import Event
        from django.core.exceptions import ValidationError
        from .split_engine import planning_months
        from playwright.sync_api import sync_playwright, expect
        gates=[Event(),Event()]
        calls=[]
        proposal={'values':{month:'100' if month.endswith('-01') else '0' for month in planning_months(self.scenario)},'token':'test-token','basis':{'rule_label':'Из факта'}}
        def slow_preview(*args):
            index=len(calls);calls.append(args)
            gates[index].wait(10)
            return proposal
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1800,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page()
                page.goto(self.live_server_url+self.url+'?tab=distribution&section=months')
                page.locator('[data-grid-select-all]').click()
                region=page.locator('.split-operation-status')
                with patch('base.split_month_grid.preview',side_effect=slow_preview):
                    page.locator('[data-grid-bulk-apply]').click()
                    expect(region).to_be_visible()
                    expect(region.locator('progress')).to_have_attribute('max','2')
                    expect(region.locator('progress')).to_have_attribute('value','0')
                    gates[0].set()
                    expect(region.locator('progress')).to_have_attribute('value','1')
                    expect(region).to_contain_text('осталось 1')
                    gates[1].set()
                    expect(region).to_have_attribute('data-state','done')
                    expect(region.locator('progress')).to_have_attribute('value','2')
                page.locator('[data-grid-reset]').click()
                with patch('base.split_month_grid.preview',side_effect=ValidationError('Нет данных')):
                    page.locator('[data-grid-bulk-apply]').click()
                    expect(region).to_have_attribute('data-state','error')
                    expect(region).to_contain_text('Ошибок: 2')
                region.get_by_role('button',name='Закрыть',exact=True).click()
                expect(region).not_to_be_visible()
                with page.expect_download() as download:
                    page.get_by_role('link',name='↓ Excel',exact=True).click()
                self.assertTrue(download.value.suggested_filename.endswith('.xlsx'))
                expect(region).to_have_attribute('data-state','done')
                page.goto(self.live_server_url+self.url+'?tab=stock')
                expect(page.locator('.split-workspace')).to_have_attribute('data-workspace-ux-ready','true')
                from .split_views import persist as original_persist
                native_gate=Event()
                gates.append(native_gate)
                def slow_save(*args,**kwargs):
                    native_gate.wait(10)
                    return original_persist(*args,**kwargs)
                page.get_by_label('Новые поставки G',exact=True).fill('6')
                page.evaluate("""() => {
                    const frame=document.createElement('iframe');frame.name='progress-test-frame';frame.hidden=true;document.body.append(frame);
                    document.querySelector('[name=action][value=stock]').form.target=frame.name;
                }""")
                with patch('base.split_views.persist',side_effect=slow_save):
                    page.get_by_role('button',name='Сохранить поставки',exact=True).evaluate('button => button.click()')
                    expect(region).to_be_visible()
                    expect(region).to_have_attribute('data-state','running')
                    self.assertIsNone(region.locator('progress').get_attribute('value'))
                    with page.expect_response(lambda response: response.request.method=='POST' and response.url.startswith(self.live_server_url+self.url)):
                        native_gate.set()
                page.reload()
                expect(page.get_by_label('Новые поставки G',exact=True)).to_have_value('6')
            finally:
                for gate in gates:gate.set()
                context.close()
                browser.close()

    def test_click_deficit_reveals_exact_power_without_losing_edits(self):
        from copy import deepcopy
        from playwright.sync_api import sync_playwright, expect
        group=self.scenario.state['groups'][self.gid]
        next(iter(group['channels'].values()))['input']='200'
        other=deepcopy(group)
        other['ref']['name']='Other group';other['ref']['source_key']='other-group'
        other['ref']['details']={**self.reference,'factory':'Other factory'}
        for line in other['channels'].values():line['input']='0'
        self.scenario.state['groups']['other']=other
        self.scenario.save(update_fields=['state'])
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1600,'height':1000})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page()
                page.goto(self.live_server_url+self.url+'?tab=stock')
                expect(page.locator('.split-workspace')).to_have_attribute('data-workspace-ux-ready','true')
                page.locator('button[data-table-view="nested"]').click()
                page.get_by_label('Новые поставки G',exact=True).fill('23')
                page.locator('[data-reference-filter="factory"]').select_option('Other factory',force=True)
                page.locator('[data-view-collapse]').click()
                issue=page.locator('[data-stock-issue-group="'+self.gid+'"]')
                expect(issue).to_have_count(1)
                issue.click()
                power=page.locator('[data-stock-child="'+self.gid+'"][data-stock-power="07"]')
                expect(power).to_be_visible()
                expect(power).to_have_class('split-stock-power-row split-focus-row')
                expect(power.locator('td').last).to_be_focused()
                expect(page.locator('.split-stock-table')).to_have_attribute('data-table-view','nested')
                expect(page.get_by_label('Новые поставки G',exact=True)).to_have_value('23')
                expect(page.locator('[data-reference-filter="factory"]')).to_have_values([])
                issue.click()
                expect(power).to_be_visible()
                page.get_by_label('Новые поставки G',exact=True).fill('0')
                page.goto(self.live_server_url+self.url+'?tab=checks')
                link=page.locator('a[href*="power=07"]')
                expect(link).to_have_count(1)
                link.click()
                expect(power).to_be_visible()
                expect(power.locator('td').last).to_be_focused()
                page.goto(self.live_server_url+self.url+'?tab=distribution&section=powers&group='+self.gid+'&power=07&focus=1')
                config=page.locator('[data-grid-row][data-gid="'+self.gid+'"] [data-power-config="07"]')
                expect(config).to_have_attribute('open','')
                expect(config.locator('input').first).to_be_focused()
            finally:
                context.close()
                browser.close()

    def test_sticky_headers_and_synchronized_top_scroll_in_all_tables(self):
        from copy import deepcopy
        from playwright.sync_api import sync_playwright, expect
        original=self.scenario.state['groups'][self.gid]
        for index in range(10):
            group=deepcopy(original);group['ref']['name']='Group '+str(index);group['ref']['source_key']='group-'+str(index)
            self.scenario.state['groups']['extra-'+str(index)]=group
        self.scenario.save(update_fields=['state'])
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1100,'height':900})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page()
                for tab in ['plan','distribution&section=months','distribution&section=powers','stock']:
                    page.goto(self.live_server_url+self.url+'?tab='+tab)
                    viewport=page.locator('.split-table-viewport')
                    bar=page.get_by_role('region',name='Горизонтальная прокрутка таблицы',exact=True)
                    expect(bar).to_be_visible()
                    bar.evaluate('el=>el.scrollLeft=200')
                    page.wait_for_function("document.querySelector('.split-table-viewport').scrollLeft > 100")
                    bar.evaluate('el=>el.scrollLeft=el.scrollWidth')
                    page.wait_for_function("Math.abs(document.querySelector('.split-table-top-scroll').scrollLeft-document.querySelector('.split-table-viewport').scrollLeft)<2")
                    viewport.evaluate('el=>el.scrollLeft=40')
                    page.wait_for_function("Math.abs(document.querySelector('.split-table-top-scroll').scrollLeft-40)<2")
                    viewport.evaluate('el=>el.scrollTop=400')
                    page.wait_for_function("document.querySelector('.split-table-viewport').scrollTop > 100")
                    positions=viewport.evaluate("el=>({viewport:el.getBoundingClientRect().top,header:el.querySelector('thead').getBoundingClientRect().top})")
                    self.assertLess(abs(positions['viewport']-positions['header']),3)
                    page.locator('[data-reference-columns]').click()
                    page.wait_for_function("Math.abs(parseFloat(document.querySelector('.split-table-top-scroll>div').style.width)-document.querySelector('.split-table-viewport').scrollWidth)<2")
            finally:
                context.close()
                browser.close()

    def test_always_visible_save_and_three_way_navigation_guard(self):
        from django.core.exceptions import ValidationError
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1400,'height':700})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page()
                page.goto(self.live_server_url+self.url+'?tab=plan')
                quantity=page.get_by_label('Количество G Дилеры',exact=True)
                quantity.fill('77')
                dock=page.locator('.split-unsaved-dock')
                page.evaluate('window.scrollTo(0,0)')
                expect(dock).to_be_visible()
                bounds=dock.bounding_box();self.assertLessEqual(bounds['y']+bounds['height'],700)
                page.locator('.split-workflow-step[href="?tab=stock"]').click()
                dialog=page.get_by_role('dialog',name='Несохранённые изменения',exact=True)
                expect(dialog).to_be_visible()
                dialog.get_by_role('button',name='Остаться на странице',exact=True).click()
                expect(quantity).to_have_value('77')
                page.locator('.split-workflow-step[href="?tab=stock"]').click()
                with page.expect_navigation():
                    dialog.get_by_role('button',name='Сохранить и перейти',exact=True).click()
                expect(page.locator('[data-stock-plan-channel]').first).to_have_value('77')
                buy=page.get_by_label('Новые поставки G',exact=True)
                buy.fill('19')
                page.locator('.split-workflow-step[href="?tab=plan"]').click()
                with page.expect_navigation():
                    dialog.get_by_role('button',name='Перейти без сохранения',exact=True).click()
                expect(page.get_by_label('Новые поставки G',exact=True)).to_have_value('0')
                expect(quantity).to_have_value('77')
                quantity.fill('88')
                with patch('base.split_views.persist',side_effect=ValidationError('Проверочная ошибка')):
                    page.locator('.split-workflow-step[href="?tab=stock"]').click()
                    dialog.get_by_role('button',name='Сохранить и перейти',exact=True).click()
                    expect(dialog.get_by_role('alert')).to_contain_text('Проверочная ошибка')
                    expect(dialog).to_be_visible()
                    expect(quantity).to_have_value('88')
                dialog.get_by_role('button',name='Остаться на странице',exact=True).click()
                with page.expect_navigation():
                    dock.get_by_role('button',name='Сохранить все изменения',exact=True).click()
                expect(quantity).to_have_value('88')
                page.goto(self.live_server_url+self.url+'?tab=distribution&section=months')
                page.locator('[data-grid-select-all]').click()
                page.locator('[data-grid-bulk-mode]').select_option('equal')
                page.locator('[data-grid-bulk-apply]').click()
                expect(dock).to_be_visible()
                page.locator('.split-workflow-step[href="?tab=stock"]').click()
                with page.expect_navigation():
                    dialog.get_by_role('button',name='Сохранить и перейти',exact=True).click()
                buy.fill('12')
                page.locator('.split-workflow-step[href="?tab=plan"]').click()
                with page.expect_navigation():
                    dialog.get_by_role('button',name='Сохранить и перейти',exact=True).click()
                expect(page.get_by_label('Новые поставки G',exact=True)).to_have_value('12')
            finally:
                context.close()
                browser.close()

    def test_save_shortcuts_for_plan_stock_and_distribution(self):
        from threading import Event
        from .split_views import persist as original_persist
        from playwright.sync_api import sync_playwright, expect
        gate=Event()
        def slow_save(*args,**kwargs):
            gate.wait(10)
            return original_persist(*args,**kwargs)
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1500,'height':900})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();posts=[]
                page.on('request',lambda request:posts.append(request) if request.method=='POST' else None)
                page.goto(self.live_server_url+self.url+'?tab=plan')
                expect(page.locator('.split-workspace')).to_have_attribute('data-workspace-ux-ready','true')
                quantity=page.get_by_label('Количество G Дилеры',exact=True)
                quantity.fill('31')
                with patch('base.split_views.persist',side_effect=slow_save):
                    page.keyboard.press('Control+s')
                    expect(page.locator('.split-operation-status')).to_have_attribute('data-state','running')
                    page.keyboard.press('Control+s')
                    self.assertEqual(len(posts),1)
                    with page.expect_navigation():gate.set()
                expect(quantity).to_have_value('31')
                page.locator('.split-workflow-step[href="?tab=stock"]').click()
                expect(page.locator('.split-workspace')).to_have_attribute('data-workspace-ux-ready','true')
                page.get_by_label('Новые поставки G',exact=True).fill('7')
                with page.expect_navigation():page.keyboard.press('Meta+s')
                expect(page.get_by_label('Новые поставки G',exact=True)).to_have_value('7')
                page.goto(self.live_server_url+self.url+'?tab=distribution&section=months')
                page.locator('[data-grid-select-all]').click()
                page.locator('[data-grid-bulk-mode]').select_option('equal')
                page.locator('[data-grid-bulk-apply]').click()
                with page.expect_navigation():page.keyboard.press('Control+s')
                expect(page.locator('[data-mode-button="equal"]').first).to_have_attribute('aria-pressed','true')
                count=len(posts)
                page.keyboard.press('Control+s')
                expect(page.locator('.split-operation-status')).to_contain_text('Нет несохранённых изменений')
                self.assertEqual(len(posts),count)
                page.goto(self.live_server_url+self.url+'?tab=plan')
                expect(page.locator('.split-workspace')).to_have_attribute('data-workspace-ux-ready','true')
                quantity.fill('44')
                with page.expect_navigation():
                    suppressed=page.evaluate("!document.dispatchEvent(new KeyboardEvent('keydown',{key:'ы',code:'KeyS',ctrlKey:true,bubbles:true,cancelable:true}))")
                self.assertTrue(suppressed)
                expect(quantity).to_have_value('44')
                count=len(posts)
                quantity.fill('-1');page.keyboard.press('Control+s')
                expect(page.locator('.split-operation-status')).to_contain_text('Исправьте ошибки ввода')
                self.assertEqual(len(posts),count)
                quantity.fill('44')
            finally:
                gate.set();context.close();browser.close()

    def test_stock_group_cannot_be_removed_and_composition_keeps_it(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1500,'height':900})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page()
                page.goto(self.live_server_url+self.url+'?tab=plan')
                expect(page.get_by_role('button',name='Удалить группу G из плана',exact=True)).to_be_disabled()
                expect(page.get_by_text('Есть остатки · удаление недоступно',exact=True)).to_be_visible()
                page.locator('.split-workflow-step[href="?tab=composition"]').click()
                option=page.locator('select[name="groups"] option[value="'+self.gid+'"]')
                expect(option).to_have_attribute('disabled','')
                values=page.locator('input[name="action"][value="composition"]').evaluate('el=>new FormData(el.form).getAll("groups")')
                self.assertEqual(values,[self.gid])
                page.get_by_label('Подтверждаю выбранный состав и способ задания объёма',exact=True).check()
                with page.expect_navigation():
                    page.get_by_role('button',name='Сохранить состав плана',exact=True).click()
                page.locator('.split-workflow-step[href="?tab=plan"]').click()
                expect(page.locator('[data-plan-group="'+self.gid+'"]')).to_be_visible()
                expect(page.get_by_role('button',name='Удалить группу G из плана',exact=True)).to_be_disabled()
            finally:
                context.close()
                browser.close()

    def test_compact_identity_keeps_modes_details_and_recalculate_accessible(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1800,'height':1000})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page()
                for section in ['months','powers']:
                    page.goto(self.live_server_url+self.url+'?tab=distribution&section='+section)
                    row=page.locator('[data-grid-row]').first
                    line=row.locator('.split-identity-line')
                    self.assertLessEqual(line.bounding_box()['height'],34)
                    expect(row.locator('.split-group-card-link')).to_have_text('↗')
                    expect(row.locator('[data-row-info]')).not_to_have_attribute('open','')
                    row.locator('[data-row-mode]').select_option('manual')
                    expect(row.locator('[data-month]').first).to_be_editable()
                    row.locator('[data-row-mode]').select_option('equal')
                    expect(row.locator('[data-month]').first).not_to_be_editable()
                    keys=row.locator('[data-month]').evaluate_all('elements=>elements.map(element=>element.dataset.month)')
                    proposal={'values':{key:'100' if index==0 else '0' for index,key in enumerate(keys)},'token':'test-token','basis':{'rule_label':'Проверенное основание'}}
                    target='base.split_month_grid.preview' if section=='months' else 'base.split_power_grid.preview'
                    with patch(target,return_value=proposal):
                        row.locator('[data-row-mode]').select_option('fact')
                        expect(row.locator('[data-month]').first).to_have_value('100')
                        expect(row.locator('[data-row-recalculate]')).to_be_visible()
                        row.locator('[data-row-recalculate]').click()
                        expect(row.locator('[data-row-recalculate]')).to_be_enabled()
                    expect(row.locator('[data-row-info]')).not_to_have_attribute('open','')
                    row.locator('[data-row-info-toggle]').click()
                    expect(row.locator('[data-row-rule]')).to_be_visible()
                    expect(row.locator('[data-row-rule]')).to_contain_text('Проверенное основание')
                    row.locator('[data-row-info-toggle]').click()
                    expect(row.locator('[data-row-rule]')).not_to_be_visible()
                    self.assertLessEqual(line.bounding_box()['height'],34)
                    page.locator('[data-grid-reset]').click()
                page.screenshot(path='/tmp/split-compact-identity.png',full_page=False)
            finally:
                context.close()
                browser.close()

    def test_all_planning_pages_enhance_every_table_with_headers_and_top_scroll(self):
        from playwright.sync_api import sync_playwright, expect
        urls=[reverse('sales_planning_goods'),reverse('sales_planning_channels'),reverse('sales_planning_groups'),reverse('sales_planning_group_detail',args=[self.group.pk]),reverse('split_scenarios'),self.url+'?tab=detail',self.url+'?tab=sources',self.url+'?tab=versions']
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1300,'height':900})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page()
                for url in urls:
                    page.goto(self.live_server_url+url)
                    page.evaluate("document.querySelectorAll('details').forEach(el=>{if(el.querySelector('table'))el.open=true;})")
                    tables=page.locator('#portal-main table')
                    self.assertGreater(tables.count(),0,url)
                    for table in tables.all():
                        if not table.locator('thead').count():continue
                        expect(table.locator('..')).to_have_attribute('data-table-scroll-ready','true')
                        table.evaluate("""table=>{
                            table.style.width='1800px';table.style.minWidth='1800px';
                            const viewport=table.parentElement;viewport.style.maxHeight='180px';
                            const body=table.tBodies[0], first=body?.rows[0];
                            if(first){for(let i=0;i<8;i++)body.append(first.cloneNode(true));[...body.rows].forEach(row=>row.style.height='80px');}
                        }""")
                        page.wait_for_function("table=>!table.parentElement.previousElementSibling.hidden",arg=table.element_handle())
                        table.evaluate('table=>table.parentElement.previousElementSibling.scrollLeft=200')
                        page.wait_for_function('table=>table.parentElement.scrollLeft>100',arg=table.element_handle())
                        table.evaluate('table=>table.parentElement.scrollTop=100')
                        page.wait_for_function('table=>table.parentElement.scrollTop>50',arg=table.element_handle())
                        positions=table.evaluate('table=>({head:table.tHead.getBoundingClientRect().top,viewport:table.parentElement.getBoundingClientRect().top})')
                        self.assertLess(abs(positions['head']-positions['viewport']),3,url)
            finally:
                context.close();browser.close()
