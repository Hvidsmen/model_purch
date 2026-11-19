from copy import deepcopy
from decimal import Decimal
import importlib.util
import json
import os
from unittest import skipUnless
from unittest.mock import Mock, patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from . import test_split_power_grid as fixtures
from .models import SalesPlanningGoods
from .split_power_defaults import defaults, load_costs, SQL


def setup_defaults(test):
    fixtures.PowerGridTests.setUp(test)
    cache.clear()
    group = test.scenario.state['groups'][test.gid]
    group['power_distribution_mode'] = 'fact'
    for power in group['powers'].values():
        power.update(factor=None, indoor_cost_percent=None, indoor=None, outdoor=None)
    test.scenario.save(update_fields=['state'])
    test.basis = {'rows': [], 'first_year': 2022, 'last_year': 2026, 'stock_date': '2026-12-31',
                  'through': '2026-12-31', 'rule_key': 'planning_group_all_channels',
                  'rule_label': 'Группа планирования · все каналы'}
    test.costs = {'in': {'qty': Decimal(10), 'cost': Decimal(300), 'unknown': False},
                  'out': {'qty': Decimal(10), 'cost': Decimal(700), 'unknown': False},
                  'nine indoor': {'qty': Decimal(20), 'cost': Decimal(800), 'unknown': False},
                  'nine outdoor': {'qty': Decimal(20), 'cost': Decimal(3200), 'unknown': False}}


class PowerDefaultsTests(TestCase):
    def setUp(self):
        setup_defaults(self)

    def test_pair_and_weighted_costs_fill_all_blank_parameters_without_mutating_state(self):
        group = self.scenario.state['groups'][self.gid]
        before = deepcopy(group)
        with patch('base.split_power_defaults.load_costs', return_value=self.costs):
            params, issues = defaults(self.scenario, group, self.basis)
        self.assertEqual(issues, [])
        self.assertEqual(params['07']['indoor'], str(self.models[0].pk))
        self.assertEqual(params['07']['outdoor'], str(self.models[1].pk))
        self.assertEqual(Decimal(params['07']['factor']), 1)
        self.assertEqual(Decimal(params['09']['factor']), 2)
        self.assertEqual(Decimal(params['07']['cost']), 30)
        self.assertEqual(Decimal(params['09']['cost']), 20)
        self.assertEqual(group, before)

    def test_manual_values_and_models_are_preserved(self):
        group = self.scenario.state['groups'][self.gid]
        group['powers']['07'].update(factor='3', indoor_cost_percent='55', indoor=str(self.models[0].pk))
        with patch('base.split_power_defaults.load_costs', return_value=self.costs):
            params, _ = defaults(self.scenario, group, self.basis)
        self.assertEqual(params['07']['factor'], '3')
        self.assertEqual(params['07']['cost'], '55')
        self.assertEqual(params['07']['indoor'], str(self.models[0].pk))

    def test_sales_leader_is_used_only_when_unique_and_active(self):
        self.group.block_pairs.all().delete()
        other = SalesPlanningGoods.objects.create(source_key='alternate', name='Alternative', planning_group='G',
            product_group='Бытовые кондиционеры', power_class='07', block_type='Внутренний')
        group = self.scenario.state['groups'][self.gid]
        group['powers']['07']['models'].append({'id': str(other.pk), 'key': other.source_key, 'name': other.name, 'kind': 'indoor'})
        self.costs['alternative'] = {'qty': Decimal(30), 'cost': Decimal(900), 'unknown': False}
        with patch('base.split_power_defaults.load_costs', return_value=self.costs):
            params, _ = defaults(self.scenario, group, self.basis)
            self.assertEqual(params['07']['indoor'], str(other.pk))
            self.costs['alternative']['qty'] = Decimal(10)
            params, issues = defaults(self.scenario, group, self.basis)
            self.assertIsNone(params['07']['indoor'])
            self.assertTrue(any('однозначной' in issue for issue in issues))
            other.is_active = False
            other.save(update_fields=['is_active'])
            params, _ = defaults(self.scenario, group, self.basis)
            self.assertEqual(params['07']['indoor'], str(self.models[0].pk))

    def test_missing_cost_and_source_errors_do_not_invent_parameters_or_discard_models(self):
        group = self.scenario.state['groups'][self.gid]
        self.costs['out']['unknown'] = True
        with patch('base.split_power_defaults.load_costs', return_value=self.costs):
            params, issues = defaults(self.scenario, group, self.basis)
        self.assertIsNone(params['07']['factor'])
        self.assertIsNone(params['07']['cost'])
        self.assertTrue(params['07']['outdoor'])
        self.assertTrue(any('себестоимости' in issue for issue in issues))
        with patch('base.split_power_defaults.load_costs', side_effect=ValidationError('Стоимость недоступна.')):
            params, issues = defaults(self.scenario, group, self.basis)
        self.assertIn('Стоимость недоступна.', issues)
        self.assertIsNone(params['09']['factor'])

    def test_preview_and_save_use_signed_defaults_without_second_source_read(self):
        with patch('base.split_power_grid.resolve_proposal', return_value=({'07': '75', '09': '25'}, self.basis)), \
             patch('base.split_power_defaults.load_costs', return_value=self.costs) as costs:
            result = self.client.post(self.url, {'action': 'preview_power_grid', 'revision': self.scenario.revision, 'group': self.gid}).json()
        self.assertTrue(result['ok'])
        costs.assert_called_once()
        payload = [{'gid': self.gid, 'mode': 'fact', 'values': result['values'], 'token': result['token']}]
        with patch('base.split_power_defaults.load_costs', side_effect=AssertionError('No second read')):
            response = self.client.post(self.url, {'action': 'save_power_grid', 'revision': self.scenario.revision, 'grid_payload': json.dumps(payload)})
        self.assertEqual(response.status_code, 200)
        self.scenario.refresh_from_db()
        powers = self.scenario.state['groups'][self.gid]['powers']
        self.assertEqual(Decimal(powers['09']['factor']), 2)
        self.assertEqual(Decimal(powers['07']['indoor_cost_percent']), 30)

    def test_sql_uses_same_cost_basis_cutoff_and_target_group_and_closes_connection(self):
        connection, cursor = Mock(), Mock()
        cursor.fetchall.return_value = [('IN', 10, 300, 0), ('OUT', 10, None, 1)]
        group = self.scenario.state['groups'][self.gid]
        with patch('base.split_power_defaults.connect_database', return_value=(connection, cursor)):
            rows = load_costs(self.scenario, group)
            self.assertEqual(load_costs(self.scenario, group), rows)
        self.assertEqual(cursor.execute.call_args.args, (SQL, '2022-01-01', '2027-01-01', 'Бытовые кондиционеры', 'G'))
        cursor.execute.assert_called_once()
        self.assertTrue(rows['out']['unknown'])
        self.assertEqual(rows['in']['cost'] / rows['in']['qty'], 30)
        cursor.close.assert_called_once()
        connection.close.assert_called_once()


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Requires Chromium')
class PowerDefaultsBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        setup_defaults(self)

    def test_fact_fills_models_and_cost_then_saves_without_overwriting_local_edit(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1600, 'height': 1100})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + self.url + '?tab=distribution&section=powers&edit=1')
                first = page.locator('[data-power-config="07"]')
                second = page.locator('[data-power-config="09"]')
                first.locator('summary').click()
                first.locator('[data-power-param="factor"]').fill('3')
                with patch('base.split_power_grid.resolve_proposal', return_value=({'07': '75', '09': '25'}, self.basis)), \
                     patch('base.split_power_defaults.load_costs', return_value=self.costs):
                    page.locator('[data-row-mode]').first.select_option('fact')
                    expect(first.locator('[data-power-param="cost"]')).to_have_value('30,000000')
                expect(first.locator('[data-power-param="factor"]')).to_have_value('3')
                expect(first.locator('[data-power-param="indoor"]')).to_have_value(str(self.models[0].pk))
                expect(first.locator('[data-power-param="outdoor"]')).to_have_value(str(self.models[1].pk))
                expect(second.locator('[data-power-param="factor"]')).to_have_value('2,000000')
                expect(second.locator('[data-power-param="cost"]')).to_have_value('20,000000')
                page.get_by_role('button', name='Сохранить изменения', exact=True).click()
                expect(first.locator('[data-power-param="cost"]')).to_have_value('30')
            finally:
                context.close()
                browser.close()
        self.scenario.refresh_from_db()
        power = self.scenario.state['groups'][self.gid]['powers']['07']
        self.assertEqual(power['factor'], '3')
        self.assertEqual(power['indoor_cost_percent'], '30')
