from copy import deepcopy
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle as fixtures
from .split_engine import allocate, evaluate, number, planning_months
from .split_workflow import changed_state


class StockPlanTests(TestCase):
    setUp = fixtures.SplitCycleTests.setUp
    complete = fixtures.SplitCycleTests.complete

    def stock_post(self):
        self.complete()
        gid, group = next(iter(self.scenario.state['groups'].items()))
        post = {'action': 'stock', 'group': gid, 'channel': next(iter(group['channels'])),
                'revision': self.scenario.revision, 'buy': '0'}
        for index, power in enumerate(group['powers'].values()):
            for field in ('stock', 'prior', 'incoming'):
                for kind in ('indoor', 'outdoor'):
                    post[f'{field}_{kind}_{index}'] = power[field + '_' + kind]
        return gid, group, post

    def test_group_plan_saves_stock_and_recalculates_closing_in_same_revision(self):
        gid, group, post = self.stock_post()
        post.update(plan_mode='group', plan_group='90', stock_indoor_0='120')
        response = self.client.post(reverse('split_scenario', args=[self.scenario.pk]) + '?tab=stock', post)
        self.assertEqual(response.status_code, 302)
        old_revision = self.scenario.revision
        self.scenario.refresh_from_db()
        lines = self.scenario.state['groups'][gid]['channels']
        self.assertEqual([number(line['input']) for line in lines.values()], [30, 60])
        self.assertEqual(self.scenario.revision, old_revision + 1)
        self.assertEqual(evaluate(self.scenario)['totals']['closing'], 10)
        self.assertEqual(next(iter(self.scenario.state['groups'][gid]['powers'].values()))['stock_indoor'], '120')

    def test_channels_edit_preserves_other_group_prices_and_distribution(self):
        gid, group, post = self.stock_post()
        self.scenario.state['groups']['other'] = deepcopy(group)
        post['plan_mode'] = 'channels'
        for cid, value in zip(group['channels'], ['45', '55']):
            post['plan_channel_' + cid] = value
        result = changed_state(self.scenario, post)
        self.assertEqual(result['groups']['other'], group)
        for cid, line in result['groups'][gid]['channels'].items():
            self.assertEqual(line['months'], group['channels'][cid]['months'])
            self.assertEqual(line['markup'], group['channels'][cid]['markup'])
        self.assertEqual(sum(number(x['input']) for x in result['groups'][gid]['channels'].values()), 100)

    def test_group_preserves_locked_channel_and_invalid_total_is_atomic(self):
        gid, group, post = self.stock_post()
        next(iter(group['channels'].values()))['lock_qty'] = True
        post.update(plan_mode='group', plan_group='90')
        result = changed_state(self.scenario, post)
        self.assertEqual([number(x['input']) for x in result['groups'][gid]['channels'].values()], [10, 80])
        post.update(plan_group='9', stock_indoor_0='500')
        original = deepcopy(self.scenario.state)
        with self.assertRaises(ValidationError):
            changed_state(self.scenario, post)
        self.assertEqual(self.scenario.state, original)

    def test_channel_edit_scales_absolute_months_and_adds_annual_actual(self):
        gid, group, post = self.stock_post()
        self.scenario.state['volume_mode'] = 'annual'
        post['plan_mode'] = 'channels'
        for cid, line in group['channels'].items():
            old = number(line['input'])
            line.update(input=str(old + 5), fact='5', allocation='quantity')
            line['month_quantities'] = dict(zip(planning_months(self.scenario), map(str, allocate(old, [number(1)] * 12))))
            post['plan_channel_' + cid] = str(old * 2)
        result = changed_state(self.scenario, post)
        for line in result['groups'][gid]['channels'].values():
            self.assertEqual(sum(map(number, line['month_quantities'].values())), number(line['input']) - Decimal(5))

    def test_ordinary_stock_save_ignores_unapplied_plan_fields(self):
        gid, group, post = self.stock_post()
        post.update(plan_mode='unchanged', plan_group='999')
        self.assertEqual(changed_state(self.scenario, post)['groups'][gid]['channels'], group['channels'])
        post['plan_mode'] = 'channels'
        with self.assertRaises(ValidationError):
            changed_state(self.scenario, post)
