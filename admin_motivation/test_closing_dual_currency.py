from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from . import test_distribution_stages as fixtures
from .models import PeriodMotivationCoefficient
from .services import premium_distribution as distribution
from .services.closing_coefficient import confirm
from .services.closing_details import impact
from .services.closing_dual_currency import funds


class DualFundTests(SimpleTestCase):
    def test_frozen_snapshot_precedes_new_calculation_and_legacy_requires_exact_source(self):
        comparisons={c:impact([dict(kind='Продажи',author='A',payment='quarter',before=str(v))]) for c,v in [('USD',10),('RUB',900)]}
        batch=SimpleNamespace(source={'funds_by_currency':{'RUB':[{'kind':'Продажи','amount':'875'}]},'workflow':{'coefficient_revision':2}},funds=[{'kind':'Продажи','amount':'10'}],currency='USD',execution_id=1,state='draft')
        record=SimpleNamespace(revision=2)
        self.assertEqual(funds(batch,comparisons,SimpleNamespace(pk=99),record)['sales_rub'],875)
        batch.source.pop('funds_by_currency')
        self.assertIsNone(funds(batch,comparisons,SimpleNamespace(pk=99),record)['sales_rub'])
        self.assertEqual(funds(batch,comparisons,SimpleNamespace(pk=1),record)['sales_rub'],900)
        record.revision=3
        self.assertIsNone(funds(batch,comparisons,SimpleNamespace(pk=1),record)['sales_rub'])


class DualCurrencyTests(TestCase):
    setUp=fixtures.ClosingWorkflowTests.setUp

    def test_new_allocation_freezes_both_actual_currencies(self):
        confirm(self.ceo,self.branch,2025,4,1)
        def source(execution,subdivision,period,currency,**kwargs):
            multiplier=Decimal(90) if currency=='RUB' else Decimal(1)
            f=[dict(row,amount=str(distribution.cash(row['amount'])*multiplier)) for row in self.funds]
            r=[dict(row,original=str(distribution.cash(row['original'])*multiplier),amount=str(distribution.cash(row['amount'])*multiplier)) for row in self.rows]
            return f,r
        with patch.object(distribution,'read_source',side_effect=source):
            batch=distribution.create(self.ceo,self.run,self.branch,4,'USD')
        self.assertEqual(batch.funds[0]['amount'],'100.00')
        self.assertEqual(batch.source['funds_by_currency']['RUB'][0]['amount'],'9000.00')
        self.assertEqual(batch.currency,'USD')

    def test_dual_endpoint_populates_both_currencies_even_before_allocation(self):
        confirm(self.ceo,self.branch,2025,4,2)
        def baseline(year,period,currency):
            return [dict(branch='Closing',kind='Продажи',author='A',before='900' if currency=='RUB' else '10',payment='quarter')],self.run
        with patch('admin_motivation.closing_details_views.baseline',side_effect=baseline),patch('admin_motivation.services.closing_plan_fact.load',return_value={}):
            response=self.client.get(reverse('motivation_closing_comparisons'),{'year':2025,'period':4,'dual':'1'})
        self.assertEqual(response.status_code,200)
        row=response.json()['rows'][str(self.branch.pk)]
        self.assertEqual(Decimal(row['before_usd']),10)
        self.assertEqual(Decimal(row['before_rub']),900)
        self.assertEqual(Decimal(row['sales_usd']),20)
        self.assertEqual(Decimal(row['sales_rub']),1800)
        page=self.client.get(reverse('motivation_distributions'),{'year':2025,'period':4})
        self.assertNotContains(page,'id="closing-currency"')
        self.assertContains(page,'data-fund-value="sales_rub"')
