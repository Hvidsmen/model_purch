from copy import deepcopy
from django.test import TestCase
from . import test_split_cycle as fixtures
from .split_engine import evaluate
from .split_stock_overview import overview


class CompactStockTests(TestCase):
    setUp = fixtures.SplitCycleTests.setUp
    complete = fixtures.SplitCycleTests.complete

    def test_sets_are_matched_per_power_not_across_group_totals(self):
        self.complete()
        group = next(iter(self.scenario.state['groups'].values()))
        first = next(iter(group['powers'].values()))
        first.update(stock_indoor='10', stock_outdoor='1', share='50', incoming_indoor='7', incoming_outdoor='2')
        second = deepcopy(first)
        second.update(stock_indoor='1', stock_outdoor='10', incoming_indoor='2', incoming_outdoor='7')
        group['powers']['09'] = second
        result = overview(self.scenario, evaluate(self.scenario))
        row = result['groups'][0]
        self.assertEqual(row['stock_sets'], 2)
        self.assertEqual(result['totals']['stock_sets'], 2)
        self.assertEqual(row['incoming_sets'], 4)
        self.assertEqual(row['prior_sets'], 0)
        self.assertEqual([power['stock_sets'] for power in row['powers']], [1, 1])

    def test_missing_component_is_unknown_but_explicit_zero_is_known(self):
        self.complete()
        power = next(iter(next(iter(self.scenario.state['groups'].values()))['powers'].values()))
        power['stock_indoor'] = None
        power['incoming_indoor'] = '0'
        power['incoming_outdoor'] = '20'
        result = overview(self.scenario, evaluate(self.scenario))
        self.assertIsNone(result['groups'][0]['stock_sets'])
        self.assertIsNone(result['totals']['stock_sets'])
        self.assertEqual(result['groups'][0]['incoming_sets'], 0)
