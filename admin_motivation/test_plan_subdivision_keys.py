from decimal import Decimal as D
from datetime import date
from copy import deepcopy
from django.test import TestCase,SimpleTestCase
from . import test_sales_plans as fixtures
from .services.sales_plans import SQL,source_sql
from .services.coefficient_iteration_calculation import RateBook,at_full_plan,plan_rows,MOSCOW,TT,MERGED
from .test_coefficient_iteration_calculation import inputs,row


class ImportIdentityTests(TestCase):
    setUp=fixtures.SalesPlanTests.setUp
    rows=fixtures.SalesPlanTests.rows
    def load(self):
        count=fixtures.SalesPlanTests.load(self)
        self.scenario.refresh_from_db();return count

    def test_same_name_preserves_two_keys_and_exact_amounts_and_segments(self):
        self.cursor.description.append(('SubdivisionKey',))
        first,second=self.rows()
        self.cursor.fetchall.return_value=[('Same name',*first[1:],'KEY-A'),('Same name',*second[1:],'KEY-B')]
        self.load()
        line=self.scenario.lines.get(subdivision='Same name')
        self.assertEqual(line.amount_usd,D(1500))
        self.assertEqual(set(line.source_subdivisions),{'KEY-A','KEY-B'})
        self.assertEqual(D(line.source_subdivisions['KEY-A']['amount']),D(1000))
        self.assertEqual(D(line.source_subdivisions['KEY-B']['segments']['O1']),D(100))
        self.assertEqual(self.scenario.lines.get(subdivision='').amount_usd,D(1500))
        expanded=plan_rows(self.scenario,2026)
        self.assertEqual({r['branch'] for r in expanded},{'KEY-A','KEY-B'})
        self.assertEqual({r['branch_name'] for r in expanded},{'Same name'})
        self.assertEqual(sum(r['amount'] for r in expanded),D(1500))

    def test_legacy_plan_is_not_assigned_a_key_by_guessing_its_name(self):
        self.load()
        from django.core.exceptions import ValidationError
        with self.assertRaisesMessage(ValidationError,'SubdivisionKey'):plan_rows(self.scenario,2026)

    def test_key_parts_change_invalidates_input_summary(self):
        self.load();self.scenario.refresh_from_db()
        self.assertTrue(self.scenario.summary_signature)
        self.scenario.lines.exclude(subdivision='').update(source_subdivisions={'A':{'amount':'1000','segments':{'O0':'1000','O1':'0','O2':'0','O3':'0','O4':'0'}}})
        self.scenario.refresh_from_db();self.assertFalse(self.scenario.summary_signature)


class KeyCalculationTests(SimpleTestCase):
    def test_moscow_tt_use_keys_even_when_keys_do_not_equal_names(self):
        payload,mappings,_,people=inputs()
        rates=RateBook(payload,mappings,{1:'MSK-ID',2:'TT-ID'},people,{1:MOSCOW,2:TT})
        plans=[{**row(year=2026,month=10),'branch':'MSK-ID','branch_name':MOSCOW},
               {**row(TT,2026,10,'50'),'branch':'TT-ID','branch_name':TT}]
        calculated,_=at_full_plan(plans,rates,2026,D(80),D(20))
        self.assertEqual({r['branch'] for r in calculated},{MERGED})
        self.assertEqual(next(r['total'] for r in calculated if r['kind']=='Политики'),D(15))
        self.assertEqual(list(rates.directors_for(TT,2025)),[('directors_0_0',3)])
        self.assertEqual({r['subdivision'] for r in rates.coefficients_2025([MOSCOW,TT])},{MOSCOW,TT})

    def test_equal_names_merge_plan_and_director_base_for_distinct_keys(self):
        payload,mappings,_,people=inputs()
        for i,block in enumerate(payload['branches']['blocks']):
            block['labels']=['A','B'];mappings[f'branches_{i}_0']={'subdivision':1};mappings[f'branches_{i}_1']={'subdivision':2}
        for value in mappings.values():
            if 'employee' in value:value['subdivisions']=[1,2]
        rates=RateBook(payload,mappings,{1:'KEY-A',2:'KEY-B'},people,{1:'Same name',2:'Same name'})
        calculated,_=at_full_plan([{**row(),'branch':'KEY-A'},{**row(amount='50'),'branch':'KEY-B'}],rates,2027,D(80),D(20))
        policies={r['branch']:r['total'] for r in calculated if r['kind']=='Политики'}
        self.assertEqual(policies,{'Same name':D(15)})
        directors=[r for r in calculated if r['kind']=='Директора']
        self.assertEqual(len(directors),2)
        self.assertEqual(sum(r['total'] for r in directors),D(105))

    def test_import_and_native_batch_join_and_partition_by_keys(self):
        from .services.coefficient_iteration_2025 import build_sql
        for source in ('planning','erp'):
            sql=source_sql(source)
            self.assertIn('plan_.SubdivisionKey=f.SubdivisionKey',sql)
            self.assertIn('GROUP BY s.SubdivisionKey, s.SubdivisionName',sql)
        sql=build_sql()
        self.assertIn('ON t.Subdivision = s.SubdivisionKey',sql)
        self.assertIn('n.SubdivisionKey=p.Subdivision',sql)
        self.assertIn('d.SubdivisionName IN',sql)
        self.assertIn('s.SubdivisionName AS PortalSubdivisionKey',sql)
        self.assertIn('SET [ПодразделениеДокумент]=[Подразделение]',sql)
        self.assertIn('named.SubdivisionName=c.Subdivision',sql)


class KeyPresentationTests(TestCase):
    def test_html_and_excel_show_name_while_preserving_distinct_key_totals(self):
        from base.testing import authorize_test_case
        from django.urls import reverse
        from io import BytesIO
        from openpyxl import load_workbook
        from .models import CoefficientIteration,CoefficientIterationRun
        from .services.coefficient_iteration_calculation import empty_row
        from .services.coefficient_iteration_runner import safe
        user=authorize_test_case(self)
        iteration=CoefficientIteration.objects.create(owner=user,payload={},confirmed=True)
        rows=[empty_row('full_2027',2027,code,'Политики') for code in ('KEY-A','KEY-B')]
        rows[0].update(total=D(10),annual_paid=D(10));rows[1].update(total=D(20),annual_paid=D(20))
        run=CoefficientIterationRun.objects.create(iteration=iteration,state='completed',configuration={},
            inputs={'plans':{'2027':{'rows':[{'branch':code,'branch_name':'Одинаковое название'} for code in ('KEY-A','KEY-B')]}}},
            results=safe({'full_2027':{'state':'completed','year':2027,'title':'2027','rows':rows}}))
        url=reverse('motivation_coefficient_iteration_calculations',args=[iteration.pk])
        response=self.client.get(url,{'run':run.pk})
        self.assertContains(response,'Одинаковое название')
        self.assertNotContains(response,'<td>KEY-A</td>')
        exported=self.client.get(url,{'run':run.pk,'export':'xlsx'})
        book=load_workbook(BytesIO(b''.join(exported.streaming_content)))
        self.assertEqual(book['Сводка 2027']['A2'].value,'Одинаковое название')
        self.assertEqual(book['Подразделения']['A2'].value,'KEY-A')
        self.assertEqual(book['Подразделения']['A3'].value,'KEY-B')
        book.close();exported.close()


class BusinessScopeMoneyTests(SimpleTestCase):
    def setup_book(self):
        payload,mappings,_,people=inputs()
        for i,block in enumerate(payload['directors']['blocks']):block['labels']=['Директор '+str(i)]
        for value in mappings.values():
            if 'employee' in value:value['subdivisions']=[1,2]
        rates=RateBook(payload,mappings,{1:'Д-Астрахань',2:'Д-Волгоград'},people,
                       {1:'Д-Астрахань',2:'Д-Астрахань'})
        return rates

    def test_signed_source_plans_net_before_premium_and_keep_receipts(self):
        rates=self.setup_book()
        rows,details=at_full_plan([row('Д-Астрахань',amount='100'),row('Д-Волгоград',amount='-20')],rates,2027,D(80),D(20))
        policy=next(r for r in rows if r['kind']=='Политики')
        self.assertEqual(policy['total'],D(8))
        self.assertEqual({r['branch'] for r in rows},{'Д-Астрахань'})
        self.assertEqual({r['key'] for r in details[0]['source_subdivisions']},{'Д-Астрахань','Д-Волгоград'})

    def test_2025_director_gate_uses_combined_plan_not_individual_keys(self):
        from unittest.mock import Mock
        from .services.coefficient_iteration_runner import directors_2025
        cursor=Mock();cursor.fetchall.return_value=[('Д-Астрахань',2,'1. RAC','Бытовые кондиционеры','DAIKIN','O1',D(90),D(0))]
        snapshot={'directors':[],'corrections':[],'aliases':{},'default':'Подразделение(Глобально)'}
        plans=[row('Д-Астрахань',2025,4,'100'),row('Д-Волгоград',2025,4,'100')]
        rows,_=directors_2025(cursor,plans,self.setup_book(),{'quarter_share':'80','year_share':'20','payout_threshold':'.8'},snapshot)
        self.assertEqual(len(rows),2)
        for result in rows:
            self.assertEqual(result['quarters'][1],0)
            self.assertGreater(result['annual_accrued'],0)
            self.assertEqual(result['annual_paid'],0)
