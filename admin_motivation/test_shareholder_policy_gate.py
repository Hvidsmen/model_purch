import re
import sqlite3

from django.test import SimpleTestCase
from .services import shareholder_regions as regions


class PolicyGateCoverageTests(SimpleTestCase):
    def test_policy_base_uses_quarter_inputs_with_each_payment_window(self):
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('''CREATE TABLE pf_base(Подразделение TEXT,Год INT,Квартал INT,ТипКоэф TEXT,СуммаПланUSD REAL,СуммаОтгрузокUSD REAL);
CREATE TABLE windows(Period_ INT,Quarter_ INT);''')
        windows=[(2,1),(2,2),(3,3),(4,1),(4,2),(4,3),(4,4),(5,1),(5,2),(5,3),(5,4)]
        db.executemany('INSERT INTO windows VALUES(?,?)',windows)
        for branch in regions.REGIONS:
            for quarter,plan,fact in [(1,100,100),(2,300,200),(3,200,200),(4,400,100)]:
                db.execute('INSERT INTO pf_base VALUES(?,2026,?,?,?,?)',(branch,quarter,'Политики',plan,fact))
                db.execute('INSERT INTO pf_base VALUES(?,2026,?,?,?,?)',(branch,quarter,'Продажи',999,999))
        sql=regions.POLICY_GATES_SQL.replace('INTO #share_region_policy ','').replace('#pf_base','pf_base').replace("N'","'")
        sql=re.sub(r'JOIN \(VALUES.*?\) w\(Period_,Quarter_\)','JOIN windows w',sql)
        rows=db.execute(sql).fetchall()
        for branch in regions.REGIONS:
            self.assertEqual({period:value for name,year,period,value in rows if name==branch},
                             {2:.75,3:1,4:.6,5:.6})
        self.assertFalse(any(period==1 for branch,year,period,value in rows))

    def test_each_paid_period_requires_policy_base_on_correct_qualified_scope(self):
        for period in (2,3,4,5):
            sql=regions.triple_failure(period,'payout_scope')
            self.assertIn('FROM #share_region_policy c',sql)
            self.assertIn('c.[Подразделение]=payout_scope.[Подразделение]',sql)
            self.assertIn('c.[Год]=payout_scope.[Год]',sql)
            self.assertIn('c.Period_='+str(period),sql)
        with self.assertRaises(ValueError):regions.triple_failure(1)
