import re,sqlite3
from django.test import SimpleTestCase
from .services.sales_plans import source_sql


class MissingPlanAmountTests(SimpleTestCase):
    def test_missing_usd_is_zero_in_both_sources_for_every_year(self):
        for source,column in (('planning','AmountUSD'),('erp','Сумма')):
            with self.subTest(source=source):
                sql=source_sql(source)
                expression=re.search(r'(SUM\(COALESCE\(ps\.'+column+r',0\)\)) AmountUSD',sql)[1]
                connection=sqlite3.connect(':memory:')
                try:
                    connection.execute('CREATE TABLE input(Date_ TEXT, '+column+' REAL)')
                    connection.executemany('INSERT INTO input VALUES (?,?)',[
                        ('2025-01-21',None),('2025-01-21',None),
                        ('2025-01-22',None),('2025-01-22',125.5),('2025-01-22',-5.5),
                        ('2026-01-21',None),('2027-01-21',None),('2028-01-21',None)])
                    rows=dict(connection.execute('SELECT ps.Date_,'+expression+' FROM input ps GROUP BY ps.Date_'))
                finally:connection.close()
                self.assertEqual(rows,{'2025-01-21':0,'2025-01-22':120,'2026-01-21':0,'2027-01-21':0,'2028-01-21':0})
