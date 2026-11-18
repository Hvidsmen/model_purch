from copy import deepcopy
from datetime import date
from decimal import Decimal
from io import BytesIO
from django.core.exceptions import ValidationError
from django.test import TestCase
from openpyxl import load_workbook
from . import test_split_cycle as fixtures
from . import test_split_stock_plan as stock_fixtures
from .split_engine import evaluate, number
from .split_inventory import apply_inventory
from .split_stock_overview import overview
from .split_supplies import corrections, signed_quantity
from .split_workflow import changed_state
from .split_exports import workbook


class SupplyTests(TestCase):
    setUp = fixtures.SplitCycleTests.setUp
    complete = fixtures.SplitCycleTests.complete
    stock_post = stock_fixtures.StockPlanTests.stock_post

    def supply_post(self):
        gid, group, post = self.stock_post()
        power = next(iter(group['powers'].values()))
        power.update(incoming_sql_indoor='10', incoming_sql_outdoor='12', incoming_indoor='10', incoming_outdoor='12')
        return gid, group, power, post

    def test_sql_correction_and_new_supplies_are_added_exactly_once(self):
        gid, group, power, post = self.supply_post()
        post.update(buy='5', supply_adjustment='-2', incoming_indoor_0='999', incoming_outdoor_0='999')
        self.scenario.state = changed_state(self.scenario, post)
        data = evaluate(self.scenario)
        self.assertEqual(data['errors'], [])
        self.assertEqual(data['totals']['closing'], 83)
        result = overview(self.scenario, data)
        self.assertEqual(result['groups'][0]['incoming_sets'], 10)
        self.assertEqual(result['totals']['new_supply'], 5)
        self.assertEqual(result['totals']['supply_adjustment'], -2)
        self.assertEqual(data['stock'][0]['incoming_sql_indoor'], 10)
        self.assertEqual(self.scenario.state['groups'][gid]['powers']['07']['incoming_sql_indoor'], '10')
        response = workbook(self.scenario)
        sheet = load_workbook(BytesIO(b''.join(response.streaming_content)), data_only=True)['Остатки']
        response.close()
        self.assertEqual(list(sheet.values)[1][-3:], (10, 12, -2))

    def test_correction_cannot_remove_more_than_existing_receipts(self):
        gid, group, power, post = self.supply_post()
        original = deepcopy(self.scenario.state)
        post.update(buy='50', supply_adjustment='-11', stock_indoor_0='999')
        with self.assertRaisesMessage(ValidationError, 'не может быть отрицательным'):
            changed_state(self.scenario, post)
        self.assertEqual(self.scenario.state, original)
        for invalid in ('', 'NaN', 'Infinity', '-0.0000001'):
            with self.assertRaises(ValidationError):
                signed_quantity(invalid)

    def test_refresh_updates_sql_only_and_preserves_manual_values(self):
        gid, group, power, post = self.supply_post()
        group.update(buy='5', supply_adjustment='-2')
        incomes = [('m0', 'IN', '07', 'Внутренний', 20, 0, date(2027, 2, 1)),
                   ('m1', 'OUT', '07', 'Наружный', 25, 0, date(2027, 2, 1))]
        apply_inventory(self.scenario, group, ([], incomes))
        self.assertEqual(power['incoming_sql_indoor'], '20')
        self.assertEqual(power['incoming_sql_outdoor'], '25')
        self.assertEqual(group['buy'], '5')
        self.assertEqual(group['supply_adjustment'], '-2')

    def test_negative_correction_conserves_six_digit_rounding(self):
        self.complete()
        group = next(iter(self.scenario.state['groups'].values()))
        power = next(iter(group['powers'].values()))
        power['share'] = '33.333333'
        group['powers']['09'] = {**deepcopy(power), 'share': '66.666667'}
        group['supply_adjustment'] = '-0.000001'
        values = corrections(group)
        self.assertEqual(sum(values.values()), Decimal('-0.000001'))
        self.assertTrue(all(value <= 0 for value in values.values()))

    def test_correction_requires_known_base_and_power_shares(self):
        gid, group, power, post = self.supply_post()
        power['incoming_sql_indoor'] = None
        post.update(supply_adjustment='1')
        with self.assertRaisesMessage(ValidationError, 'Сначала загрузите'):
            changed_state(self.scenario, post)
        power['incoming_sql_indoor'] = '10'
        power['share'] = None
        with self.assertRaisesMessage(ValidationError, 'доли мощностей'):
            changed_state(self.scenario, post)
