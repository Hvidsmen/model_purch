from copy import deepcopy
from decimal import Decimal
from django.test import TestCase
from . import test_split_cycle as fixtures
from .split_engine import evaluate, number
from .split_plan_presentation import presentation
from .split_workflow import group_snapshot


class PlanPresentationTests(TestCase):
    setUp = fixtures.SplitCycleTests.setUp
    complete = fixtures.SplitCycleTests.complete

    def test_zero_sales_without_prices_do_not_erase_financial_totals(self):
        from .split_matrix_totals import plan_totals
        self.complete()
        group=next(iter(self.scenario.state['groups'].values()))
        lines=list(group['channels'].values())
        lines[1].update(input='0',markup=None)
        data=evaluate(self.scenario)
        zero=next(row for row in data['lines'] if row['input'] is lines[1])
        self.assertEqual(zero['revenue'],0)
        result=plan_totals(data,self.scenario.state)
        self.assertGreater(result['total']['revenue'],0)
        self.assertEqual(result['total']['revenue'],data['totals']['revenue'])
        self.assertEqual(result['total']['markup'],Decimal('1.5'))
        lines[0]['markup']=None
        self.assertIsNone(plan_totals(evaluate(self.scenario),self.scenario.state)['total']['revenue'])
        lines[0]['input']=None
        self.assertIsNone(plan_totals(evaluate(self.scenario),self.scenario.state)['total']['revenue'])
        for line in lines:line.update(input='0',markup=None)
        group['ddp']=None
        result=plan_totals(evaluate(self.scenario),self.scenario.state)
        self.assertEqual(result['total']['cost'],0)
        self.assertEqual(result['total']['revenue'],0)
        self.assertIsNone(result['total']['markup'])

    def test_reference_uses_directory_manufacturer_not_group_name(self):
        self.group.name = 'G [Wrong factory]'
        self.models[0].sales_plan_group = 'RAC'
        self.models[0].outdoor_block_type = 'INV'
        self.models[0].manufacturer = 'Actual factory'
        self.models[0].save()
        ref = group_snapshot(self.group)['ref']['details']
        self.assertEqual(ref['factory'], 'Actual factory')
        self.assertEqual(ref['sales_plan_group'], 'RAC')
        self.assertEqual(ref['outdoor_type'], 'INV')
        self.assertEqual(ref['brand'], 'Brand')
        group = next(iter(self.scenario.state['groups'].values()))
        group['ref'].pop('details', None)  # Existing scenarios need no recreation.
        row = next(iter(presentation(self.scenario, evaluate(self.scenario)).values()))
        self.assertEqual(row['reference']['factory'], 'Actual factory')

    def test_power_quantities_and_financials_sum_without_doubling_blocks(self):
        self.complete()
        group = next(iter(self.scenario.state['groups'].values()))
        power = next(iter(group['powers'].values()))
        power['share'] = '40'
        group['powers']['09'] = deepcopy(power)
        group['powers']['09']['share'] = '60'
        data = evaluate(self.scenario)
        self.assertEqual(data['errors'], [])
        before = deepcopy(self.scenario.state)
        rows = next(iter(presentation(self.scenario, data).values()))['powers']
        self.assertEqual(sum(row['total']['qty'] for row in rows), 30)
        self.assertAlmostEqual(rows[0]['total']['qty'], Decimal(12), places=4)
        self.assertAlmostEqual(rows[1]['total']['qty'], Decimal(18), places=4)
        self.assertEqual(sum(row['total']['cost'] for row in rows), Decimal(3000))
        self.assertEqual(sum(row['total']['revenue'] for row in rows), Decimal(4500))
        for cid, line in group['channels'].items():
            self.assertEqual(sum(row['channels'][cid]['qty'] for row in rows), number(line['input']))
        self.assertEqual(self.scenario.state, before)

    def test_invalid_plan_remains_unknown_and_zero_channels_show_zero(self):
        self.complete()
        group = next(iter(self.scenario.state['groups'].values()))
        lines = list(group['channels'].values())
        lines[0]['markup'] = None
        lines[1]['input'] = '0'
        row = next(iter(presentation(self.scenario, evaluate(self.scenario)).values()))['powers'][0]
        cells = list(row['channels'].values())
        self.assertIsNone(cells[0]['qty'])
        self.assertEqual(cells[1]['qty'], 0)
        self.assertIsNone(row['total']['qty'])
