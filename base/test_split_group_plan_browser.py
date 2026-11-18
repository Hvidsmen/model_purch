import importlib.util
import os
import re
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from . import test_split_cycle as fixtures


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Requires Chromium')
class SplitGroupPlanBrowserTests(StaticLiveServerTestCase):
    setUp = fixtures.SplitCycleTests.setUp
    complete = fixtures.SplitCycleTests.complete

    def test_planned_and_new_supplies_have_no_correction_column(self):
        from playwright.sync_api import sync_playwright, expect
        self.complete()
        group = next(iter(self.scenario.state['groups'].values()))
        power = next(iter(group['powers'].values()))
        group['supply_adjustment'] = '-2'  # Existing values must not be silently erased.
        power.update(incoming_sql_indoor='12', incoming_sql_outdoor='15', incoming_indoor='12', incoming_outdoor='15')
        self.scenario.save(update_fields=['state'])
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1800, 'height': 1100})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('split_scenario', args=[self.scenario.pk]) + '?tab=stock')
                page.get_by_label('Новые поставки G', exact=True).fill('10')
                expect(page.locator('#split-stock-table')).not_to_contain_text('SQL')
                expect(page.locator('#split-stock-table')).not_to_contain_text('Корректировка')
                expect(page.locator('[name="supply_adjustment"]')).to_have_count(0)
                page.get_by_role('button', name='Сохранить поставки', exact=True).click()
                expect(page.get_by_role('button', name='Раскрыть группу G', exact=True)).to_have_attribute('aria-expanded', 'false')
                expect(page.get_by_label('Новые поставки G', exact=True)).to_have_value('10')
                expect(page.locator('.split-stock-group-row [data-stock-sets="incoming"]')).to_have_text('12')
                expect(page.locator('.split-stock-group-row > td').last).to_have_text('90')
                page.get_by_role('button', name='Раскрыть группу G', exact=True).click()
                page.locator('[data-stock-band-toggle="incoming"]').click()
                expect(page.get_by_label('В периоде внутренние G 07', exact=True)).to_have_value('12')
                expect(page.get_by_label('В периоде внутренние G 07', exact=True)).to_have_attribute('readonly', '')
            finally:
                context.close()
                browser.close()

    def test_stock_defaults_to_sets_and_each_block_band_expands_independently(self):
        from playwright.sync_api import sync_playwright, expect
        self.complete()
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1800, 'height': 1100})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('split_scenario', args=[self.scenario.pk]) + '?tab=stock')
                table = page.locator('#split-stock-table')
                expect(table.locator('[data-stock-block-band]:visible')).to_have_count(0)
                expect(table.locator('.split-stock-group-row [data-stock-sets="stock"]')).to_have_text('100')
                expect(table.locator('.split-stock-group-row [data-stock-sets="prior"]')).to_have_text('0')
                expect(table.locator('[data-stock-fullspan]').first).to_have_attribute('colspan', '9')
                page.get_by_role('button', name='Раскрыть группу G', exact=True).click()
                expect(page.get_by_label('Сток внутренние G 07', exact=True)).not_to_be_visible()
                table.locator('[data-stock-band-toggle="stock"]').click()
                expect(page.get_by_label('Сток внутренние G 07', exact=True)).to_be_visible()
                expect(table.locator('[data-stock-fullspan]').first).to_have_attribute('colspan', '11')
                page.get_by_label('Сток внутренние G 07', exact=True).fill('120')
                table.locator('[data-stock-band-toggle="stock"]').click()
                expect(page.get_by_label('Сток внутренние G 07', exact=True)).not_to_be_visible()
                table.locator('[data-stock-band-toggle="incoming"]').click()
                expect(page.get_by_label('В периоде внутренние G 07', exact=True)).to_be_visible()
                table.locator('[data-stock-channel-toggle]').click()
                expect(table.locator('[data-stock-fullspan]').first).to_have_attribute('colspan', '13')
                page.get_by_role('button', name='Сохранить группу', exact=True).click()
                # Hidden component inputs still submit their edited values.
                table.locator('[data-stock-band-toggle="stock"]').click()
                expect(page.get_by_label('Сток внутренние G 07', exact=True)).to_have_value('120')
                expect(table.locator('.split-stock-group-row [data-stock-sets="stock"]')).to_have_text('100')
            finally:
                context.close()
                browser.close()

    def test_stock_page_edits_total_and_channel_plan_and_updates_closing(self):
        from playwright.sync_api import sync_playwright, expect
        self.complete()
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1800, 'height': 1100})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('split_scenario', args=[self.scenario.pk]) + '?tab=stock')
                total = page.get_by_label('План продаж группы G', exact=True)
                expect(total).to_have_value('30')
                total.fill('90')
                page.get_by_role('button', name='Распределить план', exact=True).click()
                expect(total).to_have_value(re.compile(r'^90(?:\.0+)?$'))
                expect(page.get_by_role('button', name='Раскрыть группу G', exact=True)).to_have_attribute('aria-expanded', 'false')
                expect(page.locator('[data-stock-child]:visible')).to_have_count(0)
                expect(page.locator('.split-stock-group-row > td').last).to_have_text('10')
                page.get_by_role('button', name='Раскрыть план продаж по каналам', exact=True).click()
                expect(page.get_by_label('План продаж G Дилеры', exact=True)).to_have_value('30.000000')
                expect(page.get_by_label('План продаж G Розница', exact=True)).to_have_value('60.000000')
                page.get_by_label('План продаж G Дилеры', exact=True).fill('45')
                page.get_by_label('План продаж G Розница', exact=True).fill('55')
                page.get_by_role('button', name='Сохранить каналы', exact=True).click()
                expect(total).to_have_value(re.compile(r'^100(?:\.0+)?$'))
                expect(page.locator('.split-stock-group-row > td').last).to_have_text('0')
                # The shared plan is immediately visible on its other tab.
                page.get_by_role('link', name='План продаж', exact=True).click()
                expect(page.get_by_label('Общий план G', exact=True)).to_have_value('100')
            finally:
                context.close()
                browser.close()

    def test_plan_group_expands_powers_and_reference_fields_without_submitting(self):
        from playwright.sync_api import sync_playwright, expect
        self.complete()
        gid, group = next(iter(self.scenario.state['groups'].items()))
        for good in self.models:
            good.sales_plan_group = 'RAC'
            good.outdoor_block_type = 'INV'
            good.manufacturer = 'Factory <A>'
            good.save()
        group['ref'].pop('details', None)
        self.scenario.save(update_fields=['state'])
        revision = self.scenario.revision
        self.scenario.status = 'approved'
        self.scenario.save(update_fields=['status'])
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1800, 'height': 1100})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                url = self.live_server_url + reverse('split_scenario', args=[self.scenario.pk])
                page.goto(url)
                expect(page.locator('[data-plan-group] > td').first).to_have_text('RAC')
                expect(page.locator('[data-plan-group] > td').nth(1)).to_have_text('Brand')
                expect(page.locator('[data-plan-group] > td').nth(2)).to_have_text('INV')
                expect(page.locator('[data-plan-group] > td').nth(3)).to_have_text('Factory <A>')
                detail = page.locator('[data-plan-power-group]')
                expect(detail).not_to_be_visible()
                page.get_by_role('button', name='Раскрыть мощности G', exact=True).click()
                expect(detail).to_be_visible()
                expect(detail.locator('th')).to_have_text('↳ 07')
                expect(detail.locator('.split-plan-cell').first.locator('strong').nth(0)).to_have_text('10')
                expect(detail.locator('.split-plan-cell').first.locator('strong').nth(1)).to_have_text('1 500'.replace(' ', '\u00a0'))
                page.get_by_role('button', name='Раскрыть мощности G', exact=True).press('Space')
                expect(detail).not_to_be_visible()
                page.goto(url)
                page.get_by_role('button', name='Раскрыть мощности G', exact=True).click()
                expect(detail).to_be_visible()
            finally:
                context.close()
                browser.close()
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.revision, revision)

    def test_stock_plan_expands_channels_with_group_power_and_total_values(self):
        from playwright.sync_api import sync_playwright, expect
        self.complete()
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1600, 'height': 1000})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('split_scenario', args=[self.scenario.pk]) + '?tab=stock')
                toggle = page.get_by_role('button', name='Раскрыть план продаж по каналам', exact=True)
                expect(page.locator('[data-stock-channel-column]').first).not_to_be_visible()
                toggle.click()
                expect(toggle).to_have_attribute('aria-expanded', 'true')
                expect(page.locator('.split-stock-group-row [data-stock-plan-channel]').nth(0)).to_have_value('10')
                expect(page.locator('.split-stock-group-row [data-stock-plan-channel]').nth(1)).to_have_value('20')
                expect(page.locator('tfoot [data-stock-channel-column]')).to_have_text(['10', '20'])
                page.get_by_role('button', name='Раскрыть группу G', exact=True).click()
                expect(page.locator('.split-stock-power-row [data-stock-channel-column]')).to_have_text(['10', '20'])
                expect(page.locator('.split-stock-power-row [data-stock-channel-column]').first).to_be_visible()
                expect(page.locator('[data-stock-fullspan]').first).to_have_attribute('colspan', '11')
                toggle.click()
                expect(page.locator('.split-stock-power-row [data-stock-channel-column]').first).not_to_be_visible()
                expect(page.locator('[data-stock-fullspan]').first).to_have_attribute('colspan', '9')
            finally:
                context.close()
                browser.close()

    def test_edit_total_distributes_channels_and_localized_decimal_stays_editable(self):
        from playwright.sync_api import sync_playwright, expect
        self.complete()
        gid, group = next(iter(self.scenario.state['groups'].items()))
        cids = list(group['channels'])
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1600, 'height': 1000})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('split_scenario', args=[self.scenario.pk]))
                qty = page.get_by_label('Количество G Дилеры', exact=True)
                revenue = page.locator('[data-plan-revenue]').first
                markup = page.get_by_label('Наценка G Дилеры', exact=True)
                self.assertEqual(qty.evaluate('(el) => el.closest("td").querySelectorAll("input[type=number]").length'), 2)
                self.assertLess(qty.bounding_box()['y'], revenue.bounding_box()['y'])
                self.assertLess(revenue.bounding_box()['y'], markup.bounding_box()['y'])
                total = page.get_by_label('Общий план G', exact=True)
                expect(total).to_have_value('30')
                total.fill('90.000003')
                page.get_by_role('button', name='Распределить', exact=True).click()
                expect(page.locator('[name="q_' + gid + '_' + cids[0] + '"]')).to_have_value('30.000001')
                expect(page.locator('[name="q_' + gid + '_' + cids[1] + '"]')).to_have_value('60.000002')
                expect(total).to_have_value('90.000003')
            finally:
                context.close()
                browser.close()
