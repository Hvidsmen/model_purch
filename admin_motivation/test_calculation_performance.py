import re
import sqlite3
from pathlib import Path
from django.test import SimpleTestCase
from .services.calculation_performance import patch, MARKER


def source():
    return '''CREATE PROCEDURE motivation.sp_CalcBySub @FactYear int AS
SELECT DocumentKey,Номенклатура,Value_,ROW_ID
INTO #document_sales FROM (
 SELECT DataWH.dbo.makeDocumentKeyDate(rtu.Дата,rtu.Номер) DocumentKey,
 rtut.Номенклатура, rtu.Value_,
 ROW_NUMBER() OVER(PARTITION BY DataWH.dbo.makeDocumentKeyDate(rtu.Дата,rtu.Номер),rtut.Номенклатура ORDER BY rtut.Line_) ROW_ID
 FROM invoices rtu JOIN items rtut ON rtu.Id=rtut.DocumentId
) t WHERE ROW_ID=1
UNION ALL
SELECT DocumentKey,Номенклатура,Value_,ROW_ID FROM (
 SELECT DataWH.dbo.makeDocumentKeyDate(kr.Дата,kr.Номер) DocumentKey,
 krt.Номенклатура,kr.Value_,
 ROW_NUMBER() OVER(PARTITION BY DataWH.dbo.makeDocumentKeyDate(kr.Дата,kr.Номер),krt.Номенклатура ORDER BY krt.Line_) ROW_ID
 FROM corrections kr JOIN correction_items krt ON kr.Id=krt.DocumentId
) t WHERE ROW_ID=1
UPDATE s SET Value_=ds.Value_
FROM DataWH.dbo.Sales s LEFT JOIN #document_sales ds ON s.DocumentKey=ds.DocumentKey
UPDATE s SET DiscountMotivation=phe.Value_
FROM DataWH.dbo.Sales s LEFT JOIN DataWH.dbo.vPriceHistoryERP phe ON s.ModelCodeERP=phe.ModelCode
DECLARE @default_coeff float=0.71
;WITH fact AS (SELECT * FROM DataWH.dbo.Sales) SELECT * FROM fact;
'''


class CalculationPerformanceTests(SimpleTestCase):
    def test_restricts_updates_and_both_document_branches(self):
        changed = patch(source())
        self.assertIn(MARKER, changed)
        self.assertEqual(changed.count('WHERE YEAR(s.ClosedDealDate)=@FactYear'), 2)
        self.assertEqual(changed.count('WHERE EXISTS (SELECT 1 FROM #portal_sales_keys'), 2)
        self.assertIn('makeDocumentKeyDate(kr.Дата,kr.Номер)', changed)
        self.assertNotIn('YEAR(rtu.Дата)', changed)
        self.assertEqual(patch(changed), changed)
        self.assertEqual(changed.split(';WITH fact')[1], source().split(';WITH fact')[1])

    def test_unknown_structure_stays_unmodified(self):
        for sql in (source().replace('@FactYear', '@Year'),
                    source().replace('vPriceHistoryERP', 'different_source'),
                    source().replace('UPDATE s SET Value_', 'UPDATE other SET Value_'),
                    source().replace(') t WHERE ROW_ID=1', ' WHERE active=1) t WHERE ROW_ID=1')):
            self.assertEqual(patch(sql), sql)

    def test_document_subset_keeps_old_invoices_corrections_and_ranking(self):
        # Execute the generated document selection. Match keys are independent
        # of document year; repeated sales cannot multiply document partitions.
        with sqlite3.connect(':memory:') as db:
            db.create_function('document_key', 2, lambda day, number: day + ':' + number)
            db.execute('CREATE TABLE portal_sales_keys (DocumentKey,ModelCodeERP)')
            db.executemany('INSERT INTO portal_sales_keys VALUES (?,?)',
                           [('2025-12-01:old','A'),('2025-12-01:old','A'),('2026-07-01:corr','B')])
            for table in ('invoices','corrections'):
                db.execute(f'CREATE TABLE {table} (Id,Дата,Номер,Value_)')
            for table in ('items','correction_items'):
                db.execute(f'CREATE TABLE {table} (DocumentId,Номенклатура,Line_)')
            db.executemany('INSERT INTO invoices VALUES (?,?,?,?)', [(1,'2025-12-01','old',42),(2,'2024-01-01','unused',99)])
            db.executemany('INSERT INTO items VALUES (?,?,?)', [(1,'A',1),(1,'A',2),(2,'A',1)])
            db.execute("INSERT INTO corrections VALUES (3,'2026-07-01','corr',-5)")
            db.execute("INSERT INTO correction_items VALUES (3,'B',1)")
            def execute(sql):
                start = sql.index('SELECT DocumentKey,Номенклатура,Value_,ROW_ID')
                query = sql[start:sql.index('UPDATE s SET')]
                query = query.replace('INTO #document_sales','').replace('#portal_sales_keys','portal_sales_keys')
                query = query.replace('DataWH.dbo.makeDocumentKeyDate','document_key')
                return db.execute(query).fetchall()
            expected = [row for row in execute(source()) if (row[0],row[1]) in {
                ('2025-12-01:old','A'),('2026-07-01:corr','B')}]
            self.assertCountEqual(execute(patch(source())), expected)
            self.assertEqual(len(expected), 2)

    def test_review_snapshot_preparation_is_supported(self):
        root = Path(__file__).parent/'sql'/'review'
        for filename in ('sp_CalcBySub.original.sql','sp_CalcBySub.refactored.sql'):
            path = root/filename
            if not path.exists():
                continue
            sql = path.read_text().replace('] AS', '] @FactYear int AS').replace('@Year int', '@FactYear int')
            changed = patch(sql)
            self.assertIn(MARKER, changed)
            self.assertEqual(changed.count('WHERE YEAR(s.ClosedDealDate)=@FactYear'), 2)
