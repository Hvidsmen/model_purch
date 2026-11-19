from copy import deepcopy
from decimal import Decimal as D
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock,patch
from django.test import SimpleTestCase,TestCase
from django.utils import timezone
from openpyxl import load_workbook
from . import test_coefficient_iteration_calculation as fixture
from .services.coefficient_iteration_actual_2026 import sql_2026,quarter_result,Rates,calculate
from .services.coefficient_iteration_calculation import RateBook,empty_row,MERGED,MOSCOW,TT
from .services.coefficient_iteration_excel import workbook


class ActualQuarterTests(SimpleTestCase):
    def sample(self):
        row=empty_row('actual_2025',2025,MERGED,'Политики','Иванов')
        row.update(quarters=[D(8),D(0),D(24),D(999)],possible=[D(8),D(16),D(24),D(999)],
                   annual_quarters=[D(2),D(4),D(6),D(999)],annual_accrued=D(1011),annual_paid=D(1011),bonus=D(16),total=D(2058))
        return row

    def test_one_quarter_keeps_actual_blocked_payment_and_excludes_annual_and_q4(self):
        original=self.sample();before=deepcopy(original)
        for quarter in (1,2,3):
            rows,details=quarter_result([original],[{'period':f'{q} квартал','paid':q} for q in (1,2,3,4)]+[{'period':'Год'}],quarter)
            row=rows[0]
            self.assertEqual(row['total'],before['quarters'][quarter-1])
            self.assertEqual(row['possible'][quarter-1],before['possible'][quarter-1])
            self.assertEqual(row['annual_paid'],0);self.assertEqual(row['bonus'],0)
            self.assertEqual(row['annual_accrued'],D(quarter*2))
            self.assertEqual(len(details),1);self.assertEqual(details[0]['paid'],quarter)
        self.assertEqual(original,before)

    def test_native_batch_uses_2026_fact_and_september_cutoff_without_renaming_objects(self):
        from .services.coefficient_iteration_2025 import build_sql
        sql=sql_2026(build_sql())
        self.assertIn('@FactYear int=2026',sql)
        self.assertIn("DECLARE @DateTo date = CONVERT(date,'20261001');",sql)
        self.assertIn('#test2025_coeff',sql)
        self.assertNotIn('@FactYear<>2025',sql)
        self.assertIn('#iteration_actual_subdivisions',sql)
        self.assertIn('named.SubdivisionKey IN (SELECT SubdivisionKey FROM #iteration_actual_formula_keys)',sql)
        self.assertNotIn('DataWH.dbo.Subdivisions',sql)
        self.assertNotIn('DataWH.planning.PlanSales',sql)
        self.assertNotIn('FactTest2025',sql)
        self.assertIn('#iteration_report',sql)
        self.assertIn("r.Rule_=N'prepared-sales-since-2025-v1'",sql)
        self.assertEqual(sql_2026("WHERE [Год]=2025 AND Date_<'20260101'"),"WHERE [Год]=2026 AND Date_<'20270101'")

    def test_merged_rates_use_moscow_and_current_director_recipients(self):
        raw=RateBook(*fixture.inputs());rates=Rates(raw)
        self.assertEqual(rates.name_for_key(TT),MERGED)
        self.assertEqual(rates.rate_scope(MERGED,'Политики'),raw.rate_scope(MOSCOW,'Политики'))
        self.assertEqual(list(rates.directors_for(MERGED,2025)),list(raw.directors_for(MERGED,2026)))
        self.assertEqual({r['subdivision'] for r in rates.coefficients_2025([MERGED])},{MERGED})

    def test_actual_bases_preserve_native_execution_windows(self):
        from .services.coefficient_iteration_export_summary import summarize_plan_facts
        details=[{'branch':MERGED,'kind':'Политики','period':'2 квартал','department_plan':D(100),'department_fact':D(70)}]*2
        _,details=quarter_result([self.sample()],details,2)
        bases=summarize_plan_facts(details,2026)
        self.assertEqual(len(bases),1)
        self.assertEqual(bases[0]['execution'],D('.7'))
        self.assertEqual(bases[0]['plan'],D(100))

    def test_native_reader_limits_plan_and_merges_before_evaluating_gates(self):
        rates=RateBook(*fixture.inputs());cursor=Mock()
        plans=[fixture.row(sub,2026,month) for sub in (MOSCOW,TT) for month in (1,6,9,10)]
        with patch('admin_motivation.services.coefficient_iteration_2025.read_2025',return_value=([self.sample()],{})) as read, \
             patch('admin_motivation.services.coefficient_iteration_2025.read_details',return_value=([],[])), \
             patch('admin_motivation.services.coefficient_iteration_runner.directors_2025',return_value=([],[])):
            rows,details,source=calculate(cursor,plans,rates,{'payout_threshold':'.8'},{},Mock())
        selected=read.call_args.args[1]
        self.assertEqual(len(selected),6)
        self.assertEqual({row['branch_name'] for row in selected},{MERGED})
        self.assertTrue(all(row['date'].month<=9 for row in selected))
        query=cursor.execute.call_args_list[0].args
        self.assertIn('SELECT * INTO #iteration_actual_subdivisions',query[0])
        self.assertEqual(set(query[1:]),{MOSCOW,TT})
        self.assertEqual(source['through'],'2026-09-30')

    def test_actual_workbook_has_three_distinct_quarter_sheets_and_real_payments(self):
        results={}
        for q in (1,2,3):
            rows,_=quarter_result([self.sample()],[],q)
            results[f'actual_q{q}_2026']={'state':'completed','year':2026,'rows':rows,'title':'Факт','source':{},'plan_fact_summary':[]}
        run=SimpleNamespace(iteration_id=1,pk=2,state='completed',started_at=timezone.now(),completed_at=None,error='',configuration={},inputs={},results=results)
        output=workbook(run)
        book=load_workbook(output)
        for q,roman in ((1,'I'),(2,'II'),(3,'III')):
            sheet=book[f'Получатели {roman} кв 2026 факт']
            headers=[c.value for c in sheet[1]]
            self.assertIn(f'{q} кв.: премия с учётом выполнения, USD',headers)
            self.assertEqual(sheet.cell(2,sheet.max_column).value,[8,0,24][q-1])
            self.assertNotIn('4 кв.: премия с учётом выполнения, USD',headers)
        book.close();output.close()


class ActualRunnerTests(TestCase):
    def setUp(self):fixture.CalculationTests.setUp(self)

    def test_actual_native_calculation_runs_once_for_three_sheets(self):
        from .services.coefficient_iteration_runner import start,execute
        config={**self.config,'formula_version':self.version.pk,'scenarios':[f'actual_q{q}_2026' for q in (1,2,3)]}
        row=empty_row('actual_2025',2025,MERGED,'Политики','Иванов')
        row.update(quarters=[D(8),D(0),D(24),0],possible=[D(8),D(16),D(24),0],annual_quarters=[D(2),D(4),D(6),0])
        run=start(self.iteration.pk,self.user,config,self.iteration.revision)
        connection=Mock()
        with patch('admin_motivation.services.coefficient_iteration_actual_2026.calculate',return_value=([row],[],{'prepared_run_id':'frozen-2026'})) as calc:
            execute(run,Mock(),connector=Mock(return_value=(connection,Mock())))
        self.assertEqual(run.state,'completed',run.results);self.assertEqual(calc.call_count,1)
        self.assertEqual([D(run.results[f'actual_q{q}_2026']['rows'][0]['total']) for q in (1,2,3)],[D(8),D(0),D(24)])
        connection.close.assert_called_once()
        from django.urls import reverse
        response=self.client.get(reverse('motivation_coefficient_iteration_calculations',args=[self.iteration.pk]),{'run':run.pk})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'I квартал 2026 · фактическое выполнение')
        self.assertContains(response,'II квартал 2026 · фактическое выполнение')
        self.assertContains(response,'III квартал 2026 · фактическое выполнение')
        self.assertContains(response,'Иванов')
        self.assertEqual([item['quarters'] for item in response.context['presentation']],[[1],[2],[3]])
