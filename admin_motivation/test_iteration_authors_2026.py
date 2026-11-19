from datetime import date
from decimal import Decimal as D
from unittest.mock import Mock
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from .test_coefficient_iteration_calculation import inputs,row
from .services.coefficient_iteration_calculation import RateBook,at_full_plan,MOSCOW,TT
from .services.coefficient_iteration_authors_2026 import allocate,read_fact
from .services.coefficient_iteration_export_summary import summarize_plan_facts

class AuthorAllocationTests(SimpleTestCase):
    def setUp(self):
        self.rates=RateBook(*inputs())
        self.rows,self.details=at_full_plan([row(year=2026,month=10),row(TT,2026,10,'50')],self.rates,2026,D(80),D(20))

    def fact(self,author,amount,**kwargs):
        return {'branch':MOSCOW,'author':author,'planning':'1. RAC','goods':'Бытовые кондиционеры','brand':'DAIKIN','channel':'ДИЛЕРСКИЙ','amount':amount,**kwargs}

    def test_author_plan_and_payout_totals_match_original_budget(self):
        rows,details=allocate(self.rows,self.details,[self.fact('Иванов','30'),self.fact('Петров','70',branch=TT)],self.rates)
        policies=[r for r in rows if r['kind']=='Политики']
        self.assertEqual({r['employee']:r['total'] for r in policies},{'Иванов':D('4.5'),'Петров':D('10.5')})
        self.assertEqual([r for r in rows if r['kind']!='Политики'],[r for r in self.rows if r['kind']!='Политики'])
        for kind in ('Политики',):
            self.assertEqual(sum(r['total'] for r in rows if r['kind']==kind),sum(r['total'] for r in self.rows if r['kind']==kind))
            self.assertEqual(sum(d['plan'] for d in details if d['kind']==kind),D(150))
        self.assertEqual(summarize_plan_facts(details,2026),summarize_plan_facts(self.details,2026))

    def test_returns_missing_authors_and_missing_basis_do_not_lose_money(self):
        facts=[self.fact('Иванов','100'),self.fact('Иванов','-120'),self.fact('Петров','30'),self.fact('','10')]
        rows,details=allocate(self.rows,self.details,facts,self.rates)
        people={r['employee'] for r in rows if r['kind']=='Политики'}
        self.assertNotIn('Иванов',people);self.assertIn('Петров',people);self.assertIn('Не распределено: автор не указан',people)
        rows,details=allocate(self.rows,self.details,[],self.rates)
        self.assertTrue(all('Не распределено' in r['employee'] for r in rows if r['kind']=='Политики'))
        self.assertEqual(sum(r['total'] for r in rows),sum(r['total'] for r in self.rows))

    def test_kind_exclusions_do_not_use_dpp_for_policy_weights(self):
        rows,_=allocate(self.rows,self.details,[self.fact('Обычный','10'),self.fact('ДПП','90',channel='ДПП')],self.rates)
        self.assertEqual({r['employee'] for r in rows if r['kind']=='Политики'},{'Обычный'})
        self.assertEqual([r for r in rows if r['kind']=='Продажи'],[r for r in self.rows if r['kind']=='Продажи'])

    def test_fact_reader_limits_q4_and_records_prepared_identity(self):
        cursor=Mock();cursor.fetchone.side_effect=[(1,2,3),('run-1','2026-11-19'),('run-1',)]
        cursor.fetchall.return_value=[(MOSCOW,'Иванов','1. RAC','Бытовые кондиционеры','DAIKIN','ДИЛЕРСКИЙ',D(10),0)]
        facts,source=read_fact(cursor,self.rates,date(2026,11,19))
        query=next(call.args for call in cursor.execute.call_args_list if 'SUM(s.Price-s.AmountUSDIt_ITW+s.BallsOuterUSD)' in call.args[0])
        self.assertEqual(query[1:3],(date(2026,10,1),date(2026,11,20)))
        self.assertIn('PortalPreparedSales',query[0]);self.assertIn('SUM(s.Price-s.AmountUSDIt_ITW+s.BallsOuterUSD)',query[0])
        self.assertEqual(source['prepared_run_id'],'run-1');self.assertEqual(facts[0]['author'],'Иванов')
        self.assertEqual(source['named_authors'],1);self.assertEqual(source['missing_author_rows'],0)
        self.assertIn('ERP',source['author_source'])
        queries=cursor.execute.call_args_list
        self.assertEqual(len(queries[2].args),1)
        self.assertIn('TOP(0)',queries[2].args[0])
        self.assertTrue(queries[3].args[0].startswith('INSERT INTO #iteration_document_author_keys'))
        self.assertEqual(queries[3].args[1:3],(date(2026,10,1),date(2026,11,20)))
        self.assertEqual(len(queries[4].args),1)

    def test_changed_prepared_snapshot_and_null_sums_fail(self):
        for missing,current in [(1,'run-1'),(0,'other')]:
            cursor=Mock();cursor.fetchone.side_effect=[(1,2,3),('run-1','stamp'),(current,)]
            cursor.fetchall.return_value=[(MOSCOW,'Иванов','1. RAC','*','*','ДИЛЕРСКИЙ',D(10),missing)]
            with self.assertRaises(ValidationError):read_fact(cursor,self.rates,date(2026,11,19))

from django.test import TestCase
from unittest.mock import patch
from . import test_coefficient_iteration_calculation as calculation_fixture
from .services.coefficient_iteration_runner import start,execute

class AuthorRunnerTests(TestCase):
    def setUp(self):calculation_fixture.CalculationTests.setUp(self)

    def test_new_author_run_freezes_basis_and_keeps_sales_aggregate(self):
        configuration={**self.config,'scenarios':['moscow_q4_2026'],'authors_q4_2026':True}
        run=start(self.iteration.pk,self.user,configuration,self.iteration.revision)
        facts=[{'branch':MOSCOW,'author':name,'planning':'1. RAC','goods':'Бытовые кондиционеры','brand':'DAIKIN','channel':'ДИЛЕРСКИЙ','amount':value} for name,value in [('Иванов','30'),('Петров','70')]]
        connection,cursor=Mock(),Mock()
        with patch('admin_motivation.services.coefficient_iteration_authors_2026.read_fact',return_value=(facts,{'rule':'test','prepared_run_id':'frozen','through':'2026-11-19','author_source':'ERP-документы','named_authors':2,'missing_author_rows':0})):
            execute(run,Mock(),connector=Mock(return_value=(connection,cursor)))
        self.assertEqual(run.state,'completed',run.results)
        rows=run.results['moscow_q4_2026']['rows']
        self.assertEqual({r['employee']:D(r['total']) for r in rows if r['kind']=='Политики'},{'Иванов':D(6),'Петров':D(14)})
        allocated={r['employee']:D(r['allocated_plan_rub']) for r in rows if r['kind']=='Политики'}
        self.assertEqual(allocated,{'Иванов':D(4800),'Петров':D(11200)})
        sales=[r for r in rows if r['kind']=='Продажи']
        self.assertEqual(len(sales),1);self.assertEqual(sales[0]['employee'],'');self.assertEqual(D(sales[0]['total']),D(40))
        self.assertEqual(run.inputs['author_fact_q4_2026']['rows'],facts)
        base=next(row for row in run.results['moscow_q4_2026']['plan_fact_summary'] if row['kind']=='Политики')
        self.assertEqual(D(base['plan']),D(200));self.assertEqual(D(base['fact']),D(200))
        connection.close.assert_called_once()
        response=self.client.get(self.url,{'run':run.pk})
        self.assertContains(response,'Именованных авторов: 2.')
        self.assertContains(response,'ERP-документы')
