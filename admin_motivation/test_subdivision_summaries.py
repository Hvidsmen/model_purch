from decimal import Decimal as D
from django.test import SimpleTestCase
from django.template.loader import render_to_string
from .services.reports_next_presenter import present,subdivision_summaries
from . import test_presentation_branch_summary as fixtures
from .test_quarter_totals import premium


class SubdivisionSummaryTests(SimpleTestCase):
    def ledger(self):
        ledger=fixtures.BranchSummaryExcelTests().ledger()
        for row in ledger['rows']:row['annual_accrual']=row['amount']/4
        return ledger

    def test_selected_branches_types_periods_and_annual_reconcile(self):
        ledger=self.ledger();display=present('registry',ledger,ledger,['1','2'])
        groups=subdivision_summaries(ledger,display['period_columns'])
        self.assertEqual([group['branch'] for group in groups],['Д-Москва','Термотрейд'])
        self.assertEqual([group['total'] for group in groups],[185,360])
        self.assertEqual([group['annual_accrual'] for group in groups],[45,90])
        self.assertEqual([group['people'] for group in groups],[1,1])
        for group in groups:
            self.assertEqual([row['kind'] for row in group['types']],ledger['kinds'])
            self.assertEqual(sum(row['total'] for row in group['types']),group['total'])
        self.assertEqual(sum(group['total'] for group in groups),display['total'])
        self.assertEqual(sum(group['annual_accrual'] for group in groups),display['annual_accrual'])
        display['subdivision_summaries']=groups
        html=render_to_string('admin_motivation/reports_next/_subdivision_summary.html',{'ledger':ledger,'presentation':display})
        self.assertIn('data-subdivision-summary="Д-Москва"',html)
        self.assertIn('data-subdivision-summary="Термотрейд"',html)
        self.assertEqual(html.count('ИТОГО по подразделению'),2)

    def test_no_unselected_rows_or_double_counted_year_contribution(self):
        ledger=self.ledger();ledger['rows']=[row for row in ledger['rows'] if row['subdivision']=='Термотрейд' and row['code']=='2' and row['kind']=='Продажи']
        year=premium('5','Продажи','0',branch='Термотрейд');year['annual_accrual']=D(999)
        ledger['rows'].append(year)
        groups=subdivision_summaries(ledger,[{'code':'2'},{'code':'5'}])
        self.assertEqual(len(groups),1);self.assertEqual(groups[0]['total'],80)
        self.assertEqual(groups[0]['annual_accrual'],20)
        self.assertIsNone(groups[0]['periods'][1]['annual_accrual'])
        ledger['rows'][0]['annual_accrual']=None
        self.assertIsNone(subdivision_summaries(ledger,[{'code':'2'}])[0]['annual_accrual'])
