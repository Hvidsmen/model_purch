from collections import namedtuple
from datetime import datetime
from unittest.mock import Mock
import sqlite3
from django.test import SimpleTestCase
from .services.prepared_sales_projection import columns_for
from .services import prepared_sales as sales


class PreparedProjectionTests(SimpleTestCase):
    def cursor(self):
        cursor=Mock()
        Row=namedtuple('Row','name')
        cursor.fetchall.return_value=[Row(name) for name in ('ClosedDealDate','Subdivision','ProductCode',
            'Price','AmountUSDIt_ITW','BallsOuterUSD','Даичи_АвторСделкиНаименование','UnusedPayload')]
        return cursor

    def test_narrow_copy_keeps_unqualified_money_and_produces_same_totals(self):
        query='SELECT s.Subdivision,SUM(s.Price-AmountUSDIt_ITW+BallsOuterUSD) FROM #portal_motivation_sales s GROUP BY s.Subdivision'
        projection,count=columns_for(query+' WHERE ClosedDealDate>=@Start AND ProductCode IS NOT NULL',self.cursor())
        self.assertNotIn('UnusedPayload',projection);self.assertEqual(count,6)
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE source(ClosedDealDate,Subdivision,ProductCode,Price,AmountUSDIt_ITW,BallsOuterUSD,Даичи_АвторСделкиНаименование,UnusedPayload)')
            db.executemany('INSERT INTO source VALUES(?,?,?,?,?,?,?,?)',[
                ('20260101','M','P',110,20,10,'A','x'*10000),
                ('20260201','M','P',-50,5,2,'A','y'*10000)])
            db.execute('CREATE TABLE narrow AS SELECT '+projection+' FROM source')
            self.assertEqual(db.execute(query.replace('#portal_motivation_sales','narrow')).fetchall(),
                db.execute(query.replace('#portal_motivation_sales','source')).fetchall())

    def test_stars_dynamic_sql_or_unknown_metadata_keep_complete_copy(self):
        for sql in ('SELECT s.* FROM #portal_motivation_sales s','SELECT * FROM #portal_motivation_sales','SELECT DISTINCT * FROM #portal_motivation_sales',
                    'SELECT [s].* FROM #portal_motivation_sales [s]',
                    "EXEC sys.sp_executesql N'SELECT Price FROM #portal_motivation_sales'"):
            cursor=self.cursor();self.assertEqual(columns_for(sql,cursor),('*',None));cursor.execute.assert_not_called()
        cursor=self.cursor();cursor.fetchall.return_value=[]
        self.assertEqual(columns_for('SELECT Price',cursor),('*',None))

    def test_historical_author_field_remains_available(self):
        projection,_=columns_for('SELECT Price',self.cursor(),historical_author=True)
        self.assertIn('[PortalOriginalDealAuthor]',projection)
        self.assertIn('[Даичи_АвторСделкиНаименование]',projection)

    def test_consumer_has_range_filter_narrow_columns_and_timing(self):
        canonical=sales.canonical_preparation()
        sql=sales.patch(canonical+'\n;WITH fact AS (SELECT s.Price-AmountUSDIt_ITW+BallsOuterUSD AS amount FROM #portal_motivation_sales s) SELECT * FROM fact;',cursor=self.cursor())
        self.assertNotIn('YEAR(ClosedDealDate)=@FactYear',sql)
        self.assertIn('ClosedDealDate<DATEFROMPARTS(@FactYear+1,1,1)',sql)
        self.assertIn('SELECT [ClosedDealDate],[Subdivision],[Price],[AmountUSDIt_ITW],[BallsOuterUSD] INTO #portal_motivation_sales',sql)
        self.assertIn('Подготовленный факт: строк=',sql)
        self.assertNotIn('UnusedPayload',sql)

    def test_year_range_preserves_boundary_rows_and_uses_date_index(self):
        with sqlite3.connect(':memory:') as db:
            db.create_function('YEAR',1,lambda value:int(value[:4]) if value else None)
            db.execute('CREATE TABLE sales(ClosedDealDate TEXT,Amount NUMERIC)')
            db.executemany('INSERT INTO sales VALUES (?,?)',[(date,amount) for date,amount in
                [('20241231',10),('20250101',20),('20251231',-30),('20260101',40),(None,50)]])
            db.execute('CREATE INDEX ix_sales_date ON sales(ClosedDealDate)')
            before=db.execute("SELECT * FROM sales WHERE ClosedDealDate>='20250101' AND YEAR(ClosedDealDate)=2025").fetchall()
            query="SELECT * FROM sales WHERE ClosedDealDate>='20250101' AND ClosedDealDate<'20260101'"
            self.assertEqual(db.execute(query).fetchall(),before)
            self.assertIn('SEARCH',str(db.execute('EXPLAIN QUERY PLAN '+query).fetchall()).upper())
