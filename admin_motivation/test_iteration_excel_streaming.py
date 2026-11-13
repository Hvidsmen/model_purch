from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from openpyxl import Workbook,load_workbook
from django.test import SimpleTestCase,TestCase
from django.urls import reverse
from django.utils import timezone
from base.testing import authorize_test_case
from .models import CoefficientIteration,CoefficientIterationRun
from .services.coefficient_iteration_calculation import empty_row
from .services.coefficient_iteration_excel import table,result_table,workbook
from .services.coefficient_iteration_runner import safe


class StreamingWorkbookTests(SimpleTestCase):
    def test_large_single_pass_sheet_keeps_all_rows_and_safe_text(self):
        class Rows:
            used=False
            def __iter__(self):
                if self.used:raise AssertionError('Rows consumed twice')
                self.used=True
                for i in range(5000):yield ['=1+1' if i==0 else f'row-{i}',D(i)/100,None]
        book=Workbook(write_only=True)
        sheet=table(book,'Data',['Name','USD','Empty'],Rows(),text_columns=(1,))
        self.assertEqual(len(sheet.row_dimensions),1)  # constant-size layout metadata
        stream=BytesIO();book.save(stream);book.close();stream.seek(0)
        loaded=load_workbook(stream)
        sheet=loaded['Data']
        self.assertEqual(sheet.max_row,5001);self.assertEqual(sheet['A2'].value,'=1+1')
        self.assertEqual(sheet['A2'].data_type,'s');self.assertEqual(sheet['A5001'].value,'row-4999')
        self.assertAlmostEqual(sheet['B5001'].value,49.99)
        self.assertEqual(sheet.auto_filter.ref,'A1:C5001');self.assertEqual(sheet.freeze_panes,'D2')
        self.assertEqual(sheet.sheet_format.defaultRowHeight,30)
        self.assertFalse(sheet.sheet_view.showGridLines);loaded.close()

    def test_result_outlines_and_amounts_survive_streaming_header(self):
        row=empty_row('full_2027',2027,'Branch','Продажи')
        row.update(quarters=[D(8),D(16),D(24),D(32)],possible=[D(10),D(20),D(30),D(40)],
                   annual_quarters=[D(2),D(4),D(6),D(8)],annual_accrued=D(20),annual_paid=D(20),total=D(100))
        book=Workbook(write_only=True);result_table(book,'Summary',[row],2027)
        stream=BytesIO();book.save(stream);book.close();stream.seek(0)
        loaded=load_workbook(stream);sheet=loaded['Summary']
        self.assertTrue(sheet.column_dimensions['D'].hidden);self.assertTrue(sheet.column_dimensions['E'].collapsed)
        self.assertEqual(sheet['D2'].value,10);self.assertEqual(sheet['E2'].value,8)
        self.assertEqual(sheet.cell(2,sheet.max_column).value,100);loaded.close()

    def test_partial_run_exports_sources_errors_and_percentage_formats(self):
        row=empty_row('full_2027',2027,'KEY','Политики');row.update(total=D(10),annual_paid=D(10))
        run=SimpleNamespace(iteration_id=1,pk=2,state='failed',started_at=timezone.now(),completed_at=None,error='2025 failed',
            configuration={},inputs={'plans':{'2027':{'rows':[{'branch':'KEY','branch_name':'Shown name','amount':'100',
                'segments':['100','0','0','0','0'],'planning':'RAC','channel':'Dealer'}]}},
                'payload':{'branches':{'blocks':[{'kind':'Политики','column':1,'labels':['Branch'],
                  'entries':[{'key':['RAC','*','*'],'rates':['.001']*5}]}]},'directors':{'blocks':[]}}},
            results={'full_2027':{'state':'completed','year':2027,'rows':[row], 'details':[]},
                     'actual_2025':{'state':'failed','year':2025,'title':'2025','error':'2025 failed'}})
        with patch('admin_motivation.services.coefficient_iteration_excel.Workbook',wraps=Workbook) as factory:
            stream=workbook(run)
        factory.assert_called_once_with(write_only=True)
        try:
            loaded=load_workbook(stream)
            self.assertEqual(loaded['Источники плана']['C2'].value,'KEY')
            self.assertEqual(loaded['Источники плана']['D2'].value,'Shown name')
            self.assertEqual(loaded['Источники плана']['I2'].value,100)
            self.assertEqual(loaded['Снимок ставок']['I2'].number_format,'0.0000%')
            self.assertAlmostEqual(loaded['Снимок ставок']['I2'].value,.001)
            self.assertEqual(loaded['Параметры'].column_dimensions['B'].width,110)
            self.assertTrue(any('2025 failed' in str(cell.value) for row in loaded['Параметры'] for cell in row))
            loaded.close()
        finally:stream.close()
        self.assertTrue(stream.closed)


class FastExportViewTests(TestCase):
    def test_completed_export_does_not_build_calculation_form(self):
        user=authorize_test_case(self)
        iteration=CoefficientIteration.objects.create(owner=user,payload={},confirmed=True)
        run=CoefficientIterationRun.objects.create(iteration=iteration,state='failed',inputs={},configuration={},results={})
        url=reverse('motivation_coefficient_iteration_calculations',args=[iteration.pk])
        with patch('admin_motivation.coefficient_iteration_run_views.CalculationForm',side_effect=AssertionError('Export must bypass the form')):
            response=self.client.get(url,{'run':run.pk,'export':'xlsx'})
        self.assertEqual(response.status_code,200)
        data=b''.join(response.streaming_content);response.close()
        loaded=load_workbook(BytesIO(data));self.assertIn('Параметры',loaded.sheetnames);loaded.close()
