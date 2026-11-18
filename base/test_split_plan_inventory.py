from copy import deepcopy
from datetime import date
import importlib.util
import os
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle as fixtures
from .split_engine import evaluate
from .split_history import normalized
from .split_stock_overview import overview
from .split_plan_inventory import presentation


def setup_inventory(test):
    fixtures.SplitCycleTests.setUp(test)
    fixtures.SplitCycleTests.complete(test)
    test.gid, group = next(iter(test.scenario.state['groups'].items()))
    test.scenario.stock_date = date(2026, 11, 6)
    group.update(forecast_mode='group', forecast_before_total='5', buy='5')
    power = next(iter(group['powers'].values()))
    power.update(prior_indoor='10', prior_outdoor='20', incoming_indoor='12', incoming_outdoor='20',
                 incoming_sql_indoor='12', incoming_sql_outdoor='20')
    test.scenario.state['history'] = {'first_year': 2024, 'last_year': 2026, 'through': '2026-10-31',
        'groups': [group['ref']['source_key']], 'channels': [normalized(ch['name']) for ch in test.scenario.state['channels'].values()],
        'rows': [{'group': group['ref']['source_key'], 'channel': normalized('Дилеры'), 'year': 2026, 'month': 1,
                  'qty': '10', 'revenue': '1500', 'cost': '1000'}]}
    test.scenario.save(update_fields=['state', 'stock_date'])
    test.url = reverse('split_scenario', args=[test.scenario.pk])


class PlanInventoryTests(TestCase):
    def setUp(self):
        setup_inventory(self)

    def test_summary_reuses_stock_tab_values_for_group_power_and_total_without_mutation(self):
        before = deepcopy(self.scenario.state)
        result = presentation(overview(self.scenario, evaluate(self.scenario)))
        expected = {'stock': 100, 'prior': 10, 'forecast': 5, 'opening': 105, 'existing': 12, 'new': 5, 'closing': 92}
        self.assertEqual(result['total'], expected)
        self.assertEqual(result['groups'][self.gid]['powers']['07'], expected)
        self.assertEqual({key: value for key, value in result['groups'][self.gid].items() if key != 'powers'}, expected)
        self.assertEqual(self.scenario.state, before)
        with patch('model_purch.conns.connect_database', side_effect=AssertionError('No DWH read')):
            response = self.client.get(self.url)
        self.assertEqual(response.context['plan_inventory'], result)
        self.assertContains(response, 'data-plan-opening-toggle')

    def test_unknown_opening_is_not_zero_and_is_independent_of_plan_prices(self):
        group = self.scenario.state['groups'][self.gid]
        group['ddp'] = None
        result = presentation(overview(self.scenario, evaluate(self.scenario)))
        self.assertEqual(result['total']['opening'], 105)
        self.assertEqual(result['total']['existing'], 12)
        self.assertIsNone(result['total']['closing'])
        group['powers']['07']['stock_indoor'] = None
        result = presentation(overview(self.scenario, evaluate(self.scenario)))
        self.assertIsNone(result['total']['opening'])
        self.assertIsNone(result['total']['stock'])


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Requires Chromium')
class PlanInventoryBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        setup_inventory(self)

    def test_totals_show_inventory_and_opening_breakdown_with_history_months(self):
        from playwright.sync_api import sync_playwright, expect
        self.scenario.status = 'approved'
        self.scenario.save(update_fields=['status'])
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1800, 'height': 1100})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + self.url)
                row = page.locator('[data-plan-group]')
                expect(row.locator('[data-plan-inventory="opening"]')).to_have_text('105')
                expect(row.locator('[data-plan-inventory="existing"]')).to_have_text('12')
                expect(row.locator('[data-plan-inventory="new"]')).to_have_text('5')
                expect(row.locator('[data-plan-inventory="closing"]')).to_have_text('92')
                expect(page.locator('.split-matrix-grand-total [data-plan-inventory="closing"]')).to_have_text('92')
                expect(page.locator('[data-plan-opening-component]:visible')).to_have_count(0)
                band = page.locator('[data-history-band="all"]')
                expect(band).to_have_attribute('colspan', '6')
                toggle = page.get_by_role('button', name='Раскрыть сток на начало планового периода', exact=True)
                toggle.click()
                expect(row.locator('[data-plan-opening-component]')).to_have_text(['100', '10', '5'])
                expect(row.locator('[data-plan-opening-component]').first).to_be_visible()
                expect(band).to_have_attribute('colspan', '9')
                page.locator('[data-history-toggle="all-2026"] summary').click()
                expect(band).to_have_attribute('colspan', '21')
                page.get_by_role('button', name='Раскрыть мощности G', exact=True).click()
                expect(page.locator('[data-plan-power-group] [data-plan-inventory="opening"]')).to_have_text('105')
                toggle.click()
                expect(band).to_have_attribute('colspan', '18')
                expect(page.locator('[data-plan-opening-component]:visible')).to_have_count(0)
                expect(page.locator('[data-plan-power-group]')).to_be_visible()
            finally:
                context.close()
                browser.close()
