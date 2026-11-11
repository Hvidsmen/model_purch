import sqlite3
from unittest.mock import patch,Mock
from django.test import SimpleTestCase,TestCase
from django.http import QueryDict
from django.urls import reverse
from .services.period_authors import restrict_sql,quarters
from .services.reports import bind_sql
from . import test_reports_next as fixtures
from .services.manager_detail import manager_detail_report
from .test_manager_detail import detail_rows


class PeriodAuthorSqlTests(SimpleTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        columns='[Подразделение] TEXT,[АвторСделки] TEXT,[ТипКоэф] TEXT,[Год] INTEGER,[Квартал] INTEGER,[ГруппаПлановПродаж] TEXT,[ГруппаТоваров] TEXT,[Марка] TEXT,paid REAL'
        self.db.execute('CREATE TABLE fact('+columns+')');self.db.execute('CREATE TABLE report_rows('+columns+')')
        self.base=('M','Q1 only','Политики',2026,1,'RAC','AC','X',0)
        for author,quarter in [('Q1 only',1),('Q4 zero paid',4),('Other branch',4),('Other goods',4),('Zero net',4)]:
            branch='TT' if author=='Other branch' else 'M'
            brand='Y' if author=='Other goods' else 'X'
            self.db.execute('INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?)',(branch,author,'Политики',2026,quarter,'RAC','AC',brand,0))
        for author in ['Q1 only','Q4 zero paid','Other branch','Other goods','Zero net','No fact']:
            self.db.execute('INSERT INTO report_rows VALUES (?,?,?,?,?,?,?,?,?)',('M',author,'Политики',2026,4,'RAC','AC','X',0))

    def query(self,codes,brand=None):
        sql=restrict_sql('SELECT * FROM report_rows',codes).replace('DataWH.motivation.Fact','fact').replace("N'","'")
        bound,args=bind_sql(sql,{'gp_sales':[],'GPGoods':[],'Brend':[brand] if brand else []},expand_aliases=False)
        return {row[1] for row in self.db.execute(bound,args)}

    def test_current_q4_authors_not_q1_history_or_other_branch(self):
        self.assertEqual(self.query(['4','5'],'X'),{'Q4 zero paid','Zero net'})
        self.assertEqual(self.query(['5'],'X'),{'Q4 zero paid','Zero net'})
        self.assertEqual(self.query(None,'X'),{'Q4 zero paid','Zero net'})

    def test_cohort_respects_product_filters_and_any_physical_fact_presence(self):
        self.assertEqual(self.query(['4']),{'Q4 zero paid','Zero net','Other goods'})
        self.assertEqual(self.db.execute("SELECT SUM(paid) FROM fact WHERE [АвторСделки]='Zero net'").fetchone()[0],0)

    def test_other_year_or_type_does_not_supply_presence(self):
        self.db.execute("UPDATE fact SET [Год]=2025 WHERE [АвторСделки]='Q4 zero paid'")
        self.db.execute("UPDATE fact SET [ТипКоэф]='Продажи' WHERE [АвторСделки]='Zero net'")
        self.assertEqual(self.query(['4'],'X'),set())

    def test_physical_period_does_not_use_cumulative_execution_windows(self):
        self.assertEqual(quarters(['2'],'all_2026'),[2])
        self.assertEqual(quarters(['5'],'all_2026'),[4])
        self.assertEqual(quarters(['2','3'],'separate_2026'),[2,3])
    def test_loader_embeds_the_saved_fact_filter_and_binds_user_values(self):
        from .services.reports import load_report
        with patch('admin_motivation.services.subdivision_aliases.rules',return_value=[]),patch(
                'admin_motivation.services.reports.connect_database',return_value=(Mock(),Mock())),patch(
                'admin_motivation.services.reports.fetch',return_value=[]) as fetch:
            load_report('manager-detail',{'TypeCoeff_':'Политики','Subdivision':['M'],
                'DealAuthor':["Name' OR 1=1"],'Years':['2026'],'Currency':'USD',
                'gp_sales':[],'GPGoods':[],'Brend':['X']},premium_period=['4','5'],summary_only=True,period_authors_only=True)
        sql=fetch.call_args.args[1];args=fetch.call_args.args[2]
        self.assertIn('DataWH.motivation.Fact AS activity',sql)
        self.assertIn('activity.[Квартал] IN (4)',sql)
        self.assertIn("Name' OR 1=1",args);self.assertNotIn("Name' OR 1=1",sql)
        self.assertNotIn('HAVING',sql)


class TeamCohortRoutingTests(TestCase):
    setUp=fixtures.ReportsNextTests.setUp

    def test_team_and_its_export_enable_period_cohort_filter(self):
        from contextlib import nullcontext
        data=manager_detail_report(detail_rows())
        url=reverse('motivation_report_next',args=['team'])
        with patch('admin_motivation.services.reports_next.current_run',return_value=nullcontext()),patch(
                'admin_motivation.services.reports_next.load_report',return_value=data) as load:
            response=self.client.get(url,{'run':'1','period':'4'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(all(call.kwargs.get('period_authors_only') for call in load.call_args_list))
        with patch('admin_motivation.services.reports_next.current_run',return_value=nullcontext()),patch(
                'admin_motivation.services.reports_next.load_report',return_value=data) as load:
            response=self.client.get(url,{'run':'1','period':'4','export':'xlsx'})
            self.assertEqual(response.status_code,200)
            b''.join(response.streaming_content);response.close()
        self.assertTrue(all(call.kwargs.get('period_authors_only') for call in load.call_args_list))
