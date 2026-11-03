import re
import sqlite3
from datetime import date
from pathlib import Path
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from .services.calculation_sales_isolation import patch, WORKING_TABLE, MARKER
from .services.calculation_performance import patch as optimize
from .services.calculation_variants import isolate
from .test_calculation_performance import source


class SalesIsolationTests(SimpleTestCase):
    def test_main_and_extra_read_enriched_private_sales_only(self):
        for sql in (source(), isolate(source()), optimize(source())):
            with self.subTest(optimized='portal_sales_keys' in sql):
                changed = patch(sql)
                self.assertEqual(changed.count('FROM DataWH.dbo.Sales'), 1)
                self.assertEqual(changed.count('FROM '+WORKING_TABLE+' s LEFT JOIN'), 2)
                self.assertIn(';WITH fact AS (SELECT * FROM '+WORKING_TABLE+')', changed)
                self.assertLess(changed.index(MARKER), changed.index('INTO #document_sales'))
                self.assertEqual(patch(changed), changed)

    def test_unknown_preparation_and_unqualified_sales_fail_closed(self):
        for sql in (source().replace('@FactYear','@Year'),
                    source().replace('vPriceHistoryERP','unknown_source'),
                    source().replace('UPDATE s SET Value_', 'UPDATE other SET Value_'),
                    source().replace('DataWH.dbo.Sales','Sales')):
            with self.assertRaises(ValidationError):
                patch(sql)
        self.assertEqual(patch('CREATE PROC x AS SELECT 1'), 'CREATE PROC x AS SELECT 1')

    def test_working_set_enrichment_matches_old_inputs_and_shared_sales_is_unchanged(self):
        with sqlite3.connect(':memory:') as db:
            db.create_function('YEAR',1,lambda value: date.fromisoformat(value).year if value else None)
            db.create_function('DATEFROMPARTS',3,lambda y,m,d: date(y,m,d).isoformat())
            db.execute('CREATE TABLE Sales (DocumentKey,ModelCodeERP,ClosedDealDate,Value_,DiscountMotivation)')
            initial = [('One','A','2026-03-31',7,.1),('Two','B','2026-10-01',9,.2),('Old','A','2025-12-30',4,.3)]
            db.executemany('INSERT INTO Sales VALUES (?,?,?,?,?)',initial)
            db.execute('CREATE TABLE document_sales (DocumentKey,Value_)')
            db.execute("INSERT INTO document_sales VALUES ('One',42)")
            db.execute('CREATE TABLE prices (ModelCode,Value_)')
            db.execute("INSERT INTO prices VALUES ('A',.8)")
            db.execute('CREATE TEMP TABLE baseline AS SELECT * FROM Sales')
            def enrich(table):
                db.execute(f'UPDATE {table} SET Value_=(SELECT Value_ FROM document_sales d WHERE d.DocumentKey={table}.DocumentKey) WHERE YEAR(ClosedDealDate)=2026')
                db.execute(f'UPDATE {table} SET DiscountMotivation=(SELECT Value_ FROM prices p WHERE p.ModelCode={table}.ModelCodeERP) WHERE YEAR(ClosedDealDate)=2026')
            enrich('baseline')
            sql = patch(optimize(source()))
            copy = re.search(r'SELECT \* INTO '+WORKING_TABLE+r'[\s\S]*?;', sql)[0]
            copy = copy.replace('SELECT * INTO '+WORKING_TABLE, 'CREATE TEMP TABLE working AS SELECT *').replace('DataWH.dbo.Sales','Sales')
            db.execute(copy, {'FactYear':2026})
            enrich('working')
            self.assertEqual(db.execute('SELECT * FROM working ORDER BY DocumentKey').fetchall(),
                             db.execute('SELECT * FROM baseline WHERE YEAR(ClosedDealDate)=2026 ORDER BY DocumentKey').fetchall())
            self.assertEqual(db.execute('SELECT * FROM Sales').fetchall(), initial)

    def test_review_source_shapes_when_available(self):
        root = Path(__file__).parent/'sql'/'review'
        for name in ('sp_CalcBySub.original.sql','sp_CalcBySub.refactored.sql'):
            path = root/name
            if not path.exists():
                continue
            sql = path.read_text().replace('] AS', '] @FactYear int AS').replace('@Year int','@FactYear int')
            changed = patch(optimize(sql))
            self.assertIn(MARKER, changed)
            self.assertEqual(changed.count('FROM DataWH.dbo.Sales'), 1)
