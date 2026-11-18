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
            finally:
                context.close()
                browser.close()
