from decimal import Decimal as D
from io import BytesIO
from unittest.mock import patch
from django.test import SimpleTestCase,TestCase
from django.core.cache import cache
from openpyxl import load_workbook
from .models import CoefficientIteration,CoefficientIterationRun
from .services.coefficient_iteration_export_summary import summarize_plan_facts
from .services.coefficient_iteration_export_data import export_snapshot
from .services.coefficient_iteration_excel import workbook
from .services.coefficient_iteration_calculation import empty_row
from .services.coefficient_iteration_runner import safe


class SummaryTests(SimpleTestCase):
    def test_same_director_base_counted_once_and_goods_summed(self):
        row=dict(branch='Branch',kind='Директора',date='2027-01-01',planning='RAC',goods='A',plan='100',fact='100',segments=['100','0','0','0','0'])
        rows=[dict(row,employee='One'),dict(row,employee='Two'),dict(row,goods='B',plan='50',fact='50',segments=['50','0','0','0','0'])]
        result=summarize_plan_facts(rows,2027)
        self.assertEqual(len(result),1);self.assertEqual(result[0]['plan'],D(150))
        self.assertEqual(result[0]['segments'][0],D(150))

    def test_actual_department_base_is_not_multiplied_by_authors_or_windows(self):
        row=dict(branch='Branch',kind='Политики',period='2 квартал',department_plan='100',department_fact='90')
        result=summarize_plan_facts([row,row,dict(row,period='Год',department_plan='400',department_fact='300')],2025)
        self.assertEqual([r['plan'] for r in result],[D(100),D(400)])
        self.assertEqual(result[0]['execution'],D('.9'))

    def test_old_director_missing_department_base_stays_blank(self):
        result=summarize_plan_facts([dict(branch='B',kind='Директора',period='Год',formula='Бренд',plan='100',fact='90')],2025)
        self.assertIsNone(result[0]['plan'])


class CompactReaderTests(TestCase):
    def test_large_frozen_snapshot_produces_small_export_and_caches_legacy_summary(self):
        from base.testing import authorize_test_case
        user=authorize_test_case(self);cache.clear()
        iteration=CoefficientIteration.objects.create(owner=user,payload={},confirmed=True)
        detail=dict(branch='B',kind='Продажи',date='2027-01-01',planning='RAC',goods='A',plan='100',fact='100',segments=['100','0','0','0','0'])
        row=empty_row('full_2027',2027,'B','Продажи')
        run=CoefficientIterationRun.objects.create(iteration=iteration,state='completed',configuration={},
            inputs={'subdivisions':{'1':'B'},'subdivision_names':{'1':'Shown name'},'plans':{'2027':{'id':7,'title':'Frozen 2027','rows':[{'unused':'x'*100}]*10000}}},
            results=safe({'full_2027':{'state':'completed','year':2027,'rows':[row],'details':[detail]*10000}}))
        run=CoefficientIterationRun.objects.defer('inputs','results').get(pk=run.pk)
        compact=export_snapshot(run)
        self.assertEqual(run.get_deferred_fields(),{'inputs','results'})
        self.assertEqual(compact.inputs['plans']['2027']['title'],'Frozen 2027')
        self.assertNotIn('rows',compact.inputs['plans']['2027'])
        self.assertNotIn('details',compact.results['full_2027'])
        self.assertEqual(len(compact.results['full_2027']['plan_fact_summary']),1)
        with patch('admin_motivation.services.coefficient_iteration_export_data.array_records',side_effect=AssertionError('cached download must not scan source rows')):
            export_snapshot(run)
        stream=workbook(compact)
        try:
            book=load_workbook(stream)
            self.assertEqual(set(book.sheetnames),{'Сводка 2027','Получатели 2027','План-факт 2027','Параметры'})
            self.assertEqual(book['План-факт 2027'].max_row,2)
            self.assertEqual(book['План-факт 2027']['A2'].value,'Shown name')
            self.assertEqual(book['План-факт 2027']['D2'].value,100)
            self.assertLess(sum(sheet.max_row for sheet in book),100)
            book.close()
        finally:stream.close()
