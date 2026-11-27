from contextlib import nullcontext
from decimal import Decimal
from unittest.mock import patch
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from . import test_distribution_stages as fixtures
from .services import closing_plan_fact


class ClosingPlanFactTests(TestCase):
    setUp=fixtures.ClosingWorkflowTests.setUp

    def test_source_counts_department_once_and_uses_selected_window(self):
        cache.clear()
        data={'department_performance':[
            {'Подразделение':'Closing','Год':2025,'КварталМотивации':'4 Квартал','Plan_':1000,'Fact_':800},
            {'Подразделение':'Closing','Год':2025,'КварталМотивации':'Итого','Plan_':9999,'Fact_':9999},
            {'Подразделение':'Closing','Год':2024,'КварталМотивации':'4 Квартал','Plan_':1,'Fact_':1}]}
        with patch('admin_motivation.services.reports_next.current_run',side_effect=lambda execution:nullcontext()),patch('admin_motivation.services.reports.load_report',return_value=data) as load:
            actual=closing_plan_fact.load(self.run,4)
            self.assertEqual(closing_plan_fact.load(self.run,4),actual)
        self.assertEqual(actual,{'closing':{'plan':Decimal(1000),'fact':Decimal(800)}})
        self.assertEqual(load.call_count,1)
        self.assertEqual(load.call_args.kwargs['premium_period'],['4'])

    def test_zero_and_missing_fact_are_not_percentages(self):
        for fact in (None,Decimal(0)):
            self.assertEqual(closing_plan_fact.shares({'before':100,'after':120},{'fact':fact}),{'before_fact_percent':None,'after_fact_percent':None})
        self.assertEqual(closing_plan_fact.shares({'before':100,'after':120},{'fact':Decimal(-1000)}),{'before_fact_percent':Decimal(-10),'after_fact_percent':Decimal(-12)})
        self.assertEqual(closing_plan_fact.shares({'before':100,'after':120},{'fact':Decimal(1000)}),{'before_fact_percent':Decimal(10),'after_fact_percent':Decimal(12)})

    def test_rub_selected_does_not_divide_rubles_by_usd(self):
        from .services.closing_coefficient import confirm
        confirm(self.ceo,self.branch,2025,4,2)
        def baseline(year,period,currency):
            return [dict(branch='Closing',kind='Политики',author='Policy',payment='quarter',before='9000' if currency=='RUB' else '100')],self.run
        with patch('admin_motivation.closing_details_views.baseline',side_effect=baseline),patch.object(closing_plan_fact,'load',return_value={'closing':{'plan':2000,'fact':Decimal(1000)}}):
            response=self.client.get(reverse('motivation_closing_comparisons'),{'year':2025,'period':4,'currency':'RUB'})
        self.assertEqual(response.status_code,200)
        row=response.json()['rows'][str(self.branch.pk)]
        self.assertEqual(Decimal(row['before']),9000)
        self.assertEqual(Decimal(row['after']),18000)
        self.assertEqual(Decimal(row['before_fact_percent']),10)
        self.assertEqual(Decimal(row['after_fact_percent']),20)
        self.assertEqual(response.json()['plan_fact_currency'],'USD')

    def test_unavailable_plan_fact_preserves_payouts_without_inventing_zero(self):
        rows=[dict(branch='Closing',kind='Политики',author='Policy',payment='quarter',before='100')]
        with patch('admin_motivation.closing_details_views.baseline',return_value=(rows,self.run)),patch.object(closing_plan_fact,'load',side_effect=ValueError('unavailable')),self.assertLogs('admin_motivation.closing_details_views',level='ERROR'):
            response=self.client.get(reverse('motivation_closing_comparisons'),{'year':2025,'period':4})
        row=response.json()['rows'][str(self.branch.pk)]
        self.assertEqual(Decimal(row['before']),100)
        self.assertIsNone(row['fact']);self.assertIsNone(row['plan'])
        self.assertIsNone(row['before_fact_percent'])
        self.assertTrue(response.json()['plan_fact_error'])
