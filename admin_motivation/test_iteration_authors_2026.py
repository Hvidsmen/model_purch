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

    def test_fact_reader_uses_only_unified_prepared_author(self):
        cursor=Mock()
        cursor.fetchone.side_effect=[(1,2,3),('snapshot','completed'),('snapshot',)]
        cursor.fetchall.return_value=[(MOSCOW,'Иванов','1. RAC','Бытовые кондиционеры','DAIKIN','ДИЛЕРСКИЙ',D(10),0)]
        facts,source=read_fact(cursor,self.rates,date(2026,11,19))
        query=cursor.execute.call_args_list[2].args
        self.assertEqual(query[1:3],(date(2026,10,1),date(2026,11,20)))
        self.assertIn('FROM DataWH.motivation.PortalPreparedSales s',query[0])
        self.assertIn('s.Даичи_АвторСделкиНаименование',query[0])
        for forbidden in ('DataWH.dbo.Sales','s.PortalOriginalDealAuthor','DataWH.erp','EmpAuthorID','Автор документа продажи'):
            self.assertNotIn(forbidden,query[0])
        self.assertEqual(cursor.execute.call_count,4)
        self.assertEqual(facts[0]['author'],'Иванов')
        self.assertEqual(source['named_authors'],1);self.assertEqual(source['missing_author_rows'],0)
        self.assertEqual(source['author_source'],'DataWH.motivation.PortalPreparedSales.Даичи_АвторСделкиНаименование')
        self.assertEqual(source['source_table'],'DataWH.motivation.PortalPreparedSales')
        self.assertEqual(source['prepared_run_id'],'snapshot')

    def test_missing_or_changed_prepared_snapshot_is_rejected(self):
        cursor=Mock();cursor.fetchone.return_value=(None,None,None)
        with self.assertRaisesMessage(ValidationError,'Подготовьте факт'):read_fact(cursor,self.rates,date(2026,11,19))
        cursor.fetchone.side_effect=[(1,2,3),None]
        with self.assertRaisesMessage(ValidationError,'Нет совместимого'):read_fact(cursor,self.rates,date(2026,11,19))
        cursor.fetchone.side_effect=[(1,2,3),('before','completed'),('after',)]
        cursor.fetchall.return_value=[]
        with self.assertRaisesMessage(ValidationError,'изменился'):read_fact(cursor,self.rates,date(2026,11,19))

    def test_unknown_amount_is_rejected_and_blank_author_is_preserved(self):
        cursor=Mock();cursor.fetchone.side_effect=[(1,2,3),('snapshot','completed'),(1,2,3),('snapshot','completed'),('snapshot',)];cursor.fetchall.return_value=[(MOSCOW,'','1. RAC','*','*','ДИЛЕРСКИЙ',D(10),1)]
        with self.assertRaises(ValidationError):read_fact(cursor,self.rates,date(2026,11,19))
        cursor.fetchall.return_value=[(MOSCOW,'','1. RAC','*','*','ДИЛЕРСКИЙ',D(10),0)]
        facts,source=read_fact(cursor,self.rates,date(2026,11,19))
        self.assertEqual(facts[0]['author'],'');self.assertEqual(source['missing_author_rows'],1)

from django.test import TestCase
from unittest.mock import patch
from . import test_coefficient_iteration_calculation as calculation_fixture
from .services.coefficient_iteration_runner import start,execute

class AuthorRunnerTests(TestCase):
    def setUp(self):calculation_fixture.CalculationTests.setUp(self)

    def test_new_author_run_freezes_basis_and_keeps_sales_aggregate(self):
        configuration={**self.config,'scenarios':['moscow_q4_2026'],'authors_q4_2026':True}
        run=start(self.iteration.pk,self.user,configuration,self.iteration.revision)
        from datetime import datetime,timezone as dt_timezone
        run.started_at=datetime(2026,11,18,22,30,tzinfo=dt_timezone.utc)
        run.save(update_fields=['started_at'])
        facts=[{'branch':MOSCOW,'author':name,'planning':'1. RAC','goods':'Бытовые кондиционеры','brand':'DAIKIN','channel':'ДИЛЕРСКИЙ','amount':value} for name,value in [('Иванов','30'),('Петров','70')]]
        connection,cursor=Mock(),Mock()
        with patch('admin_motivation.services.coefficient_iteration_authors_2026.read_fact',return_value=(facts,{'rule':'test','through':'2026-11-19','source_table':'DataWH.motivation.PortalPreparedSales','author_field':'Даичи_АвторСделкиНаименование','author_source':'DataWH.motivation.PortalPreparedSales.Даичи_АвторСделкиНаименование','named_authors':2,'missing_author_rows':0})) as reader:
            execute(run,Mock(),connector=Mock(return_value=(connection,cursor)))
        self.assertEqual(reader.call_args.args[2],date(2026,11,19))
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
        self.assertContains(response,'Источник авторов: DataWH.motivation.PortalPreparedSales.Даичи_АвторСделкиНаименование')
