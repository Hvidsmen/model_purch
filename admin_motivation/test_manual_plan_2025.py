import re,sqlite3
from django.test import SimpleTestCase
from .services.sales_plans import source_sql


class ManualPlan2025Tests(SimpleTestCase):
    def test_only_manual_2025_missing_usd_becomes_zero(self):
        sql=source_sql('planning')
        expression=re.search(r'(SUM\(CASE WHEN YEAR\(ps.Date_\)=2025[\s\S]*?END\)) AmountUSD',sql)[1]
        connection=sqlite3.connect(':memory:')
        connection.create_function('YEAR',1,lambda value:int(value[:4]))
        try:
            connection.execute('CREATE TABLE input(Date_ TEXT, AmountUSD REAL)')
            connection.executemany('INSERT INTO input VALUES (?,?)',[
                ('2025-01-21',None),('2025-01-21',None),
                ('2025-01-22',None),('2025-01-22',125.5),('2025-01-22',-5.5),
                ('2026-01-21',None),('2027-01-21',None)])
            rows=dict(connection.execute('SELECT ps.Date_,'+expression+' FROM input ps GROUP BY ps.Date_'))
        finally:connection.close()
        self.assertEqual(rows['2025-01-21'],0)
        self.assertEqual(rows['2025-01-22'],120)
        self.assertIsNone(rows['2026-01-21']);self.assertIsNone(rows['2027-01-21'])
        erp=source_sql('erp')
        self.assertNotIn('THEN COALESCE(ps.Сумма,0)',erp)
        self.assertIn('SUM(ps.Сумма) AmountUSD',erp)
