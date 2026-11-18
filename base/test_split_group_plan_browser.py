import importlib.util
import os
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from . import test_split_cycle as fixtures


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Requires Chromium')
class SplitGroupPlanBrowserTests(StaticLiveServerTestCase):
    setUp = fixtures.SplitCycleTests.setUp
    complete = fixtures.SplitCycleTests.complete

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
                expect(detail.locator('td').nth(5)).to_have_text('10')
                expect(detail.locator('td').nth(7)).to_have_text('1 500'.replace(' ', '\u00a0'))
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
                expect(page.locator('.split-stock-group-row [data-stock-channel-column]')).to_have_text(['10', '20'])
                expect(page.locator('tfoot [data-stock-channel-column]')).to_have_text(['10', '20'])
                page.get_by_role('button', name='Раскрыть группу G', exact=True).click()
                expect(page.locator('.split-stock-power-row [data-stock-channel-column]')).to_have_text(['10', '20'])
                expect(page.locator('.split-stock-power-row [data-stock-channel-column]').first).to_be_visible()
                expect(page.locator('[data-stock-fullspan]').first).to_have_attribute('colspan', '15')
                toggle.click()
                expect(page.locator('.split-stock-power-row [data-stock-channel-column]').first).not_to_be_visible()
                expect(page.locator('[data-stock-fullspan]').first).to_have_attribute('colspan', '13')
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
