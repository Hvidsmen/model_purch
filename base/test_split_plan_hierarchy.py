from copy import deepcopy
from decimal import Decimal
import importlib.util
import os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle as fixtures
from .sales_planning_group_service import group_key


def setup_hierarchy(test):
    fixtures.SplitCycleTests.setUp(test)
    fixtures.SplitCycleTests.complete(test)
    test.gid, first = next(iter(test.scenario.state['groups'].items()))
    first['ref']['details'] = {'sales_plan_group': 'RAC', 'brand': 'Brand', 'outdoor_type': 'INV', 'factory': 'Factory A'}
    second = deepcopy(first)
    second['ref'].update(name='H', source_key=group_key('H'))
    second['ref']['details'].update(outdoor_type='On-Off', factory='Factory B')
    second['ddp'] = '200'
    for value, line in zip(('5', '15'), second['channels'].values()):
        line.update(input=value, markup='2')
    test.scenario.state['groups']['other'] = second
    test.scenario.state['history'] = {'first_year': 2024, 'last_year': 2026, 'through': '2026-12-31',
        'groups': [first['ref']['source_key'], second['ref']['source_key']], 'channels': ['дилеры', 'розница'],
        'rows': [{'group': group['ref']['source_key'], 'channel': 'дилеры', 'year': 2026, 'month': 1,
                  'qty': qty, 'revenue': revenue, 'cost': cost}
                 for group, qty, revenue, cost in [(first, '10', '100', '50'), (second, '30', '600', '150')]]}
    test.scenario.save(update_fields=['state'])
    test.url = reverse('split_scenario', args=[test.scenario.pk])


class PlanHierarchyTests(TestCase):
    def setUp(self):
        setup_hierarchy(self)

    def test_subtotals_are_weighted_and_include_unique_groups_and_inventory(self):
        before = deepcopy(self.scenario.state)
        page = self.client.get(self.url)
        rows = page.context['plan_hierarchy']
        leaves = [row for row in rows if row['kind'] == 'group']
        self.assertEqual({row['gid'] for row in leaves}, {self.gid, 'other'})
        self.assertEqual(len(leaves), 2)
        root = rows[0]['node']
        self.assertEqual(root['count'], 2)
        self.assertEqual(root['total']['qty'], 50)
        self.assertEqual(root['total']['revenue'], 12500)
        self.assertEqual(root['total']['cost'], 7000)
        self.assertEqual(root['total']['markup'].quantize(Decimal('.000001')), Decimal('1.785714'))
        fact = root['history']['years'][0]['total']
        self.assertEqual(fact['qty'], 40)
        self.assertEqual(fact['markup'], Decimal('3.5'))
        self.assertEqual(root['history']['years'][0]['months'][0]['total']['markup'], Decimal('3.5'))
        self.assertEqual(root['inventory']['opening'], 200)
        self.assertEqual(root['inventory']['closing'], 150)
        self.assertEqual(self.scenario.state, before)

    def test_mixed_and_missing_attributes_do_not_duplicate_or_drop_groups(self):
        self.scenario.state['groups']['other']['ref']['details'].update(sales_plan_group='RAC, VRF', factory='')
        self.scenario.save(update_fields=['state'])
        rows = self.client.get(self.url).context['plan_hierarchy']
        roots = [row['node'] for row in rows if row['kind'] == 'subtotal' and row['node']['level'] == 0]
        self.assertEqual({root['name'] for root in roots}, {'RAC', 'RAC, VRF'})
        self.assertEqual(sum(root['total']['qty'] for root in roots), 50)
        self.assertEqual(len([row for row in rows if row['kind'] == 'group']), 2)
        self.assertTrue(any(row['kind'] == 'subtotal' and row['node']['name'] == 'Не указано' for row in rows))

    def test_unknown_financial_or_stock_value_remains_unknown_in_subtotal(self):
        self.scenario.state['groups']['other']['ddp'] = None
        self.scenario.save(update_fields=['state'])
        root = self.client.get(self.url).context['plan_hierarchy'][0]['node']
        self.assertEqual(root['total']['qty'], 50)
        self.assertIsNone(root['total']['cost'])
        self.assertIsNone(root['total']['markup'])
        self.assertEqual(root['inventory']['opening'], 200)
        self.assertIsNone(root['inventory']['closing'])


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Requires Chromium')
class PlanHierarchyBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        setup_hierarchy(self)

    def test_collapse_subtotals_and_save_preserve_tree_and_leaf_data(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1800, 'height': 1100})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + self.url)
                root = page.locator('[data-plan-node][data-plan-level="0"]').first
                expect(root.locator('[data-subtotal-total] strong').first).to_have_text('50')
                expect(root.locator('.split-fact-column').first.locator('strong').last).to_have_text('3,50')
                expect(page.locator('[data-plan-group]:visible')).to_have_count(2)
                toggle = root.locator('[data-plan-subtotal-toggle]')
                toggle.click()
                expect(page.locator('[data-plan-group]:visible')).to_have_count(0)
                expect(page.locator('.split-matrix-grand-total')).to_be_visible()
                toggle.press('Enter')
                expect(page.locator('[data-plan-group]:visible')).to_have_count(2)
                page.get_by_role('button', name='Раскрыть мощности G', exact=True).click()
                expect(page.locator('[data-plan-power-group]:visible')).to_have_count(1)
                toggle.click()
                expect(page.locator('[data-plan-power-group]:visible')).to_have_count(0)
                toggle.click()
                expect(page.locator('[data-plan-power-group]:visible')).to_have_count(1)
                page.get_by_label('Количество G Дилеры', exact=True).fill('12')
                page.get_by_role('button', name='Свернуть разделы', exact=True).click()
                with page.expect_navigation():
                    page.get_by_role('button', name='Сохранить объёмы и наценки', exact=True).click()
                expect(root.locator('[data-subtotal-total] strong').first).to_have_text('52')
                expect(page.locator('[data-plan-group]:visible')).to_have_count(0)
                page.get_by_role('button', name='Развернуть разделы', exact=True).click()
                expect(page.locator('[data-plan-group]:visible')).to_have_count(2)
                expect(page.get_by_label('Количество G Дилеры', exact=True)).to_have_value('12')
                expect(page.locator('[data-plan-power-group]:visible')).to_have_count(1)
            finally:
                context.close()
                browser.close()
