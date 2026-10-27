from pathlib import Path
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from .services.pilot_calculation import patch,MARKER


def procedure():
    # A small public fixture, independent of private SQL review snapshots.
    return '''CREATE PROC motivation.sp_CalcBySub AS
SELECT '1 Квартал' КварталМотивации, 0 Остаток, 0 ОстатокRUR
INTO DataWH.motivation.ReportMotivationByDealAuthor FROM #pf_base WHERE Квартал=1
UNION ALL SELECT '2 Квартал(1-2 кв)' КварталМотивации,0,0 FROM #pf_base WHERE Квартал IN(1,2)
UNION ALL SELECT '3 Квартал' КварталМотивации,0,0 FROM #pf_base WHERE Квартал IN(3)
UNION ALL SELECT '4 Квартал(1-4 кв)' КварталМотивации,0,0 FROM #pf_base WHERE Квартал IN(1,2,3,4)
UNION ALL SELECT 'Год' КварталМотивации,0,0 FROM #pf_base WHERE Квартал IN(1,2,3,4);
INSERT INTO DataWH.motivation.ReportMotivationByDealAuthor SELECT 'Год' КварталМотивации,1,1
FROM DataWH.motivation.ReportMotivationByDealAuthor WHERE КварталМотивации <> N'Год';'''


class PilotCalculationTests(SimpleTestCase):
    def test_early_outputs_disabled_but_q4_and_year_keep_full_year_inputs(self):
        sql=patch(procedure())
        self.assertEqual(sql.count('WHERE 1=0 AND'),3)
        self.assertEqual(sql.count('WHERE Квартал IN(1,2,3,4)'),2)
        self.assertIn(MARKER+'\nIF 1=0\nINSERT INTO',sql)
        self.assertEqual(procedure().count('WHERE 1=0'),0)

    def test_unknown_and_duplicate_structures_rejected(self):
        for sql in ('CREATE PROC x AS SELECT 1',procedure().replace("'3 Квартал'","'Other'"),patch(procedure())):
            with self.assertRaises(ValidationError):patch(sql)

    def test_private_review_shapes_when_available(self):
        root=Path(__file__).parent/'sql'/'review'
        paths=[root/'sp_CalcBySub.refactored.sql',root/'20261015'/'sp_CalcBySub.user_revision.sql']
        existing=[path for path in paths if path.exists()]
        if not existing:self.skipTest('Private review snapshots are optional')
        for path in existing:
            with self.subTest(name=path.name):
                sql=patch(path.read_text())
                self.assertEqual(sql.count('WHERE 1=0 AND'),3)

    def test_union_keeps_only_q4_and_annual_rows_from_all_four_input_quarters(self):
        import sqlite3
        prefix=patch(procedure()).split(MARKER)[0].split(' AS\n',1)[1]
        prefix=prefix.replace('INTO DataWH.motivation.ReportMotivationByDealAuthor','')
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE pf_base (Квартал)')
            db.executemany('INSERT INTO pf_base VALUES (?)',[(quarter,) for quarter in (1,2,3,4)])
            rows=db.execute(prefix.replace('#pf_base','pf_base')).fetchall()
        self.assertEqual(len(rows),8)
        self.assertEqual({row[0] for row in rows},{'4 Квартал(1-4 кв)','Год'})
        self.assertTrue(all(row[1:]==(0,0) for row in rows))

    def test_cumulative_q4_uses_q4_subdivision_correction(self):
        import sqlite3,re
        from .services.manager_correction import SQL
        expression=re.search(r'CROSS APPLY \(SELECT (CASE[\s\S]*?END) AS PeriodId\) p',SQL)[1].replace("N'","'")
        with sqlite3.connect(':memory:') as db:
            db.create_function('LOWER',1,lambda value:value.lower())
            db.execute('CREATE TABLE rows ([КварталМотивации])')
            db.executemany('INSERT INTO rows VALUES (?)',[(value,) for value in ('4 Квартал','4 Квартал(1-4 кв)','Год','40 Квартал')])
            self.assertEqual([row[0] for row in db.execute('SELECT '+expression+' FROM rows r')],[4,4,5,None])
