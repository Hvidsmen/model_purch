import re
import sqlite3
from django.test import SimpleTestCase
from django.core.exceptions import ValidationError
from .services import prepared_sales, prepared_sales_discounts as discounts


class DiscountCacheTests(SimpleTestCase):
    def source(self):
        return prepared_sales.canonical_preparation().replace(prepared_sales.WORKING_TABLE,'DataWH.motivation.PortalPreparedSalesStage').replace(
            'WHERE YEAR(s.ClosedDealDate)=@FactYear;',"WHERE s.ClosedDealDate>='20250101';")

    def select(self,sql):
        match=re.search(r'UPDATE s\s+SET s.DiscountMotivation\s*=([\s\S]+?);',sql)
        return 'SELECT s.id,'+match.group(1)+' ORDER BY s.id'

    def portable(self,sql):
        return sql.replace('DataWH.motivation.PortalPreparedSalesStage','sales').replace('DataWH.dbo.vPriceHistoryERP','prices').replace(
            'DataWH.dbo.getCurrancyRateUSD','usd_rate').replace('#portal_prepared_fx','portal_prepared_fx')

    def test_original_case_retained_and_unknown_shapes_rejected(self):
        source=self.source();patched=discounts.patch(source)
        self.assertEqual(discounts.patch(patched),patched)
        before=self.select(source).split('FROM')[0]
        after=self.select(patched).split('FROM')[0].replace('fx.RateUSD',discounts.RATE)
        self.assertEqual(before,after)
        with self.assertRaises(ValidationError):discounts.patch(source.replace(discounts.RATE,'unknown_rate()'))

    def test_results_and_one_rate_call_per_distinct_date_currency(self):
        conn=sqlite3.connect(':memory:');calls=[]
        def rate(day,currency):
            calls.append((day,currency))
            return {'USD':1.0,'EUR':1.1,None:1.0,'ZERO':0.0,'MISSING':None}[currency]
        conn.create_function('usd_rate',2,rate)
        conn.executescript('''CREATE TABLE sales(id INTEGER,ModelCodeERP TEXT,ВидЦены TEXT,
            даичи_ВидЦеныВходнаяСтоимостьНаименование TEXT,DateInvoice TEXT,ClosedDealDate TEXT,
            Price REAL,AmountUSDIt_ITW REAL,BallsOuterUSD REAL,Quantity REAL,Discount REAL);
            CREATE TABLE prices(ModelCode TEXT,PriceType TEXT,DateFrom TEXT,DateTo TEXT,Price REAL,CurrencyCode TEXT);''')
        for model,currency,price in [('A','EUR',100),('B',None,100),('C','ZERO',100),('D','MISSING',100),('E','USD',0)]:
            conn.execute('INSERT INTO prices VALUES (?,?,?,?,?,?)',(model,'entry','20250101','20251231',price,currency))
        cases=[('A','',80,0,0,1,20),('A','',80,0,5,1,20),('A','retail',80,0,5,1,20),
            ('A','retail',80,0,5,1,100),('A','retail',0,0,5,1,20),('A','retail',80,80,5,1,20),
            ('A','',80,0,5,0,20),('A','',80,0,None,1,20),('B','',80,0,5,1,20),
            ('C','',80,0,5,1,20),('D','',80,0,5,1,20),('E','',80,0,5,1,20),
            ('absent','',80,0,5,1,20),('A',None,80,0,5,1,20)]
        for index,(model,kind,price,amount,balls,quantity,discount) in enumerate(cases):
            conn.execute('INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (index,model,kind,'entry','20250101' if index%2 else '20251231','20250601',price,amount,balls,quantity,discount))
        source=self.source();expected=conn.execute(self.portable(self.select(source))).fetchall()
        patched=discounts.patch(source)
        cache=patched.split(discounts.MARKER)[1].split('UPDATE s')[0]
        cache='\n'.join(line for line in cache.splitlines() if not line.startswith('IF OBJECT_ID'))
        cache=re.sub(r'SELECT([\s\S]*?)\s+INTO\s+(#\w+)\s+FROM',lambda m:'CREATE TEMP TABLE '+m[2]+' AS SELECT'+m[1]+' FROM',cache)
        calls.clear();conn.executescript(self.portable(cache))
        count=conn.execute('SELECT COUNT(*) FROM portal_prepared_fx_keys').fetchone()[0]
        self.assertEqual(len(calls),count)
        actual=conn.execute(self.portable(self.select(patched))).fetchall()
        self.assertEqual(actual,expected)
        self.assertEqual(len(calls),count)
        self.assertLess(count,len(cases));conn.close()
