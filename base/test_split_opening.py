from copy import deepcopy
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from django.test import SimpleTestCase
from .split_opening import opening_balance


class OpeningBalanceTests(SimpleTestCase):
    def scenario(self,gap=True):
        return SimpleNamespace(stock_date=date(2026,11,6) if gap else date(2026,12,31),start=date(2027,1,1),end=date(2027,12,31))

    def group(self):
        return {'powers':{'07':{'share':'100','stock_indoor':'15','stock_outdoor':'12','prior_indoor':'6','prior_outdoor':'0'}},
                'forecast':{'2026-11':'2','2026-12':'3'}}

    def test_opening_uses_only_stock_prior_and_forecast_without_plan_or_prices(self):
        group=self.group();before=deepcopy(group)
        row=opening_balance(self.scenario(),group)['rows'][0]
        self.assertEqual((row['opening_indoor'],row['opening_outdoor'],row['sets']),(Decimal(16),Decimal(7),Decimal(7)))
        group['buy']='999';group['powers']['07'].update(incoming_indoor='888',incoming_outdoor='777')
        self.assertEqual(opening_balance(self.scenario(),group)['rows'][0],row)
        self.assertEqual(before['powers']['07']['stock_indoor'],group['powers']['07']['stock_indoor'])

    def test_missing_forecast_or_prior_remains_unknown_not_zero(self):
        group=self.group();group['forecast']['2026-11']=None
        result=opening_balance(self.scenario(),group)
        self.assertIsNone(result['rows'][0]['sets']);self.assertTrue(result['issues']);self.assertIsNone(result['totals']['sets'])
        group=self.group();group['powers']['07']['prior_indoor']=None
        row=opening_balance(self.scenario(),group)['rows'][0]
        self.assertIsNone(row['opening_indoor']);self.assertEqual(row['opening_outdoor'],7)

    def test_zero_forecast_needs_no_power_distribution_and_gapless_needs_no_prior(self):
        group=self.group();group['powers']['07'].update(share=None,prior_indoor=None,prior_outdoor=None)
        result=opening_balance(self.scenario(gap=False),group)
        self.assertEqual(result['rows'][0]['sets'],12);self.assertEqual(result['rows'][0]['forecast'],0)
        group['forecast']={'2026-11':'0','2026-12':'0'};group['powers']['07'].update(prior_indoor='0',prior_outdoor='0')
        self.assertEqual(opening_balance(self.scenario(),group)['rows'][0]['sets'],12)

    def test_forecast_allocation_and_set_totals_do_not_mix_powers(self):
        group={'powers':{'07':{'share':'50','stock_indoor':'10','stock_outdoor':'0','prior_indoor':'0','prior_outdoor':'0'},
                         '09':{'share':'50','stock_indoor':'0','stock_outdoor':'10','prior_indoor':'0','prior_outdoor':'0'}},
               'forecast':{'2026-11':'0','2026-12':'0'}}
        self.assertEqual(opening_balance(self.scenario(),group)['totals']['sets'],0)
        group['forecast']['2026-11']='3'
        result=opening_balance(self.scenario(),group)
        self.assertEqual([row['forecast'] for row in result['rows']],[Decimal('1.5'),Decimal('1.5')])
        self.assertEqual(result['totals']['sets'],-3);self.assertTrue(result['deficit'])

    def test_partial_forecast_import_and_invalid_shares_do_not_invent_an_opening(self):
        group=self.group();group['forecast_imports']={'2026-11':{'start':'2026-11-10','end':'2026-11-30'}}
        self.assertIsNone(opening_balance(self.scenario(),group)['totals']['sets'])
        group=self.group();group['powers']['07']['share']='20'
        result=opening_balance(self.scenario(),group);self.assertIsNone(result['totals']['sets']);self.assertTrue(result['issues'])
