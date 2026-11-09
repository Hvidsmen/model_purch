from decimal import Decimal
from django.test import SimpleTestCase
from .split_matrix_totals import aggregate, enrich_history, plan_totals


class MatrixTotalsTests(SimpleTestCase):
    def test_fact_zero_is_blank_but_unknown_and_nonzero_are_preserved(self):
        from django.template.loader import render_to_string
        from .templatetags.split_format import fact_number, fact_cash
        self.assertEqual(fact_number(Decimal('0')), '')
        self.assertEqual(fact_cash(Decimal('0')), '')
        self.assertEqual(fact_number(None), '—')
        self.assertEqual(fact_number(Decimal('-2')), '-2')
        cell = {'qty': Decimal(0), 'revenue': Decimal(0), 'cost': Decimal(0), 'markup': None}
        self.assertEqual(render_to_string('base/_split_history_metrics.html', {'cell':cell}).strip(), '')
        self.assertIn('0,00', render_to_string('base/_split_history_metrics.html', {'cell':cell, 'plan_metrics':True}))

    def test_weighted_markup_and_unknown_values(self):
        cells = [{'qty': Decimal(2), 'revenue': Decimal(200), 'cost': Decimal(100)},
                 {'qty': Decimal(3), 'revenue': Decimal(1200), 'cost': Decimal(400)}]
        self.assertEqual(aggregate(cells)['markup'], Decimal('2.8'))
        self.assertEqual(aggregate(cells)['qty'], 5)
        cells[0]['revenue'] = None
        self.assertIsNone(aggregate(cells)['revenue'])
        self.assertIsNone(aggregate(cells)['markup'])
        self.assertEqual(aggregate(cells)['cost'], 500)

    def test_plan_group_channel_and_grand_totals(self):
        lines = [{'gid': g, 'cid': c, 'quantity': Decimal(q), 'revenue': Decimal(q)*30, 'cost': Decimal(q)*10}
                 for g,c,q in [('a','x',2),('a','y',3),('b','x',7)]]
        result = plan_totals({'lines': lines}, {'groups': {'a':{},'b':{}}, 'channels': {'x':{},'y':{}}})
        self.assertEqual(result['groups']['a']['qty'],5)
        self.assertEqual(result['channels']['x']['qty'],9)
        self.assertEqual(result['total']['qty'],12)
        self.assertEqual(result['total']['markup'],3)

    def test_history_totals_sum_groups_channels_and_months_without_double_counting(self):
        def cell(q): return {'qty': Decimal(q), 'revenue': Decimal(q)*20, 'cost': Decimal(q)*10}
        history = {'years':[2025], 'groups':[{'years':[{'year':2025, 'annual':[cell(q),cell(q*2)],
                   'months':[{'month':f'2025-{m:02}', 'cells':[cell(q if m==1 else 0),cell(q*2 if m==1 else 0)]} for m in range(1,13)]}]} for q in (2,3)]}
        total = enrich_history(history)
        self.assertEqual(history['groups'][0]['years'][0]['total']['qty'],6)
        self.assertEqual(total['years'][0]['annual'][0]['qty'],5)
        self.assertEqual(total['years'][0]['months'][0]['total']['qty'],15)
        self.assertEqual(total['years'][0]['total']['qty'],15)
