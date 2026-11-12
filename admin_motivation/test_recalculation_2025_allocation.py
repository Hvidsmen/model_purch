import sqlite3
import re
from .services.subdivision_scope import resolve_scope_sql
from django.test import SimpleTestCase
from .services.recalculation_2025_sql import ALLOCATION


class ZeroSumPolicyPlanTests(SimpleTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.create_function('PATINDEX',2,lambda pat,v:(m.start()+1) if (m:=re.search(r'[^0-9. ]',v)) else 0)
        self.db.create_function('UPPER',1,lambda v:v.upper() if v else v)
        self.db.execute('''CREATE TABLE facts(ПодразделениеДокумент,АвторСделки,
 ГруппаПлановПродаж,ГруппаТоваров,Марка,СуммаОтгрузокUSD REAL,ТипКоэф,Год)''')
        self.sql=ALLOCATION[ALLOCATION.index('SELECT t.*'):ALLOCATION.index('IF EXISTS(SELECT 1 FROM #percent_plan_menger')]
        self.sql=resolve_scope_sql(self.sql).replace('INTO #percent_plan_menger','').replace('#fact','(SELECT *,ПодразделениеДокумент AS Подразделение,NULL AS Канал FROM facts)').replace("N'","'")

    def add(self,branch,author,amount,brand='X',group='AC',pg='RAC',kind='Политики',year=2025):
        self.db.execute('INSERT INTO facts VALUES (?,?,?,?,?,?,?,?)',(branch,author,pg,group,brand,amount,kind,year))

    def shares(self):
        return {(r[0],r[1],r[2],r[3],r[4]):r[-1] for r in self.db.execute(self.sql)}

    def test_zero_sum_allocates_only_positive_author_net_in_same_original_branch(self):
        for branch,rows in [('M',[('A',100),('B',-100),('Zero',0)]),
                            ('TT',[('A',100),('C',200),('B',-300)])]:
            for author,amount in rows:self.add(branch,author,amount)
        values=self.shares()
        self.assertEqual(values['M','A','RAC','AC','X'],1)
        self.assertEqual(values['M','B','RAC','AC','X'],0)
        self.assertEqual(values.get(('M','Zero','RAC','AC','X'),0),0)
        self.assertAlmostEqual(values['TT','A','RAC','AC','X'],1/3)
        self.assertAlmostEqual(values['TT','C','RAC','AC','X'],2/3)
        self.assertEqual(values['TT','B','RAC','AC','X'],0)
        for branch in ('M','TT'):
            self.assertAlmostEqual(sum(value for key,value in values.items() if key[0]==branch),1)

    def test_positive_net_is_after_author_returns_not_gross_positive_invoices(self):
        self.add('M','A',150);self.add('M','A',-50)
        self.add('M','B',300);self.add('M','C',-400)
        values=self.shares()
        self.assertEqual(values['M','A','RAC','AC','X'],.25)
        self.assertEqual(values['M','B','RAC','AC','X'],.75)
        self.assertEqual(values['M','C','RAC','AC','X'],0)
        self.assertEqual(self.db.execute('SELECT SUM(СуммаОтгрузокUSD) FROM facts').fetchone()[0],0)

    def test_other_brands_goods_groups_and_years_do_not_supply_fallback_weights(self):
        self.add('M','A',100);self.add('M','B',-100)
        self.add('M','C',1000,brand='Y');self.add('M','D',1000,group='Other')
        self.add('M','E',1000,pg='Other');self.add('M','F',10000,year=2024)
        self.add('M','Sales',10000,kind='Продажи')
        values=self.shares()
        self.assertEqual(values['M','A','RAC','AC','X'],1)
        self.assertEqual(values['M','B','RAC','AC','X'],0)
        self.assertNotIn(('M','F','RAC','AC','X'),values)
        self.assertNotIn(('M','Sales','RAC','AC','X'),values)

    def test_nonzero_signed_total_preserves_previous_rule(self):
        self.add('M','A',100);self.add('M','B',-20)
        self.assertEqual(self.shares()['M','A','RAC','AC','X'],1.25)
        self.assertEqual(self.shares()['M','B','RAC','AC','X'],-.25)
        self.db.execute('DELETE FROM facts')
        self.add('M','A',-100);self.add('M','B',-200)
        self.assertAlmostEqual(self.shares()['M','A','RAC','AC','X'],1/3)

    def test_all_zero_author_nets_do_not_invent_equal_weights(self):
        self.add('M','A',100);self.add('M','A',-100)
        self.add('M','B',0)
        self.assertEqual(self.shares(),{})
