import re
import sqlite3
from django.test import SimpleTestCase
from .services.recalculation_2025_sql import template_source,patch_source
from .services.recalculation_2025_plan import ELIGIBLE,INVALID,PLAN_GUARD,EXCLUDED


class PlanClassificationTests(SimpleTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE plans(Subdivision,GoodsKey,Date_,Version_,AmountUSD REAL);
 CREATE TABLE goods(PlanningKey,PlanningGroupSalesERP,GroupERP,BrandName);
 INSERT INTO goods VALUES('A','1. RAC','Мобильные кондиционеры','Brand A'),
 ('B','1. RAC','Мобильные кондиционеры','Brand B'),('C','Other','Accessories','Brand C'),
 ('D','1. RAC','Бытовые кондиционеры','Brand D');
 INSERT INTO plans VALUES('M','A','20250101','Selected',100),('M','B','20250101','Selected',200),
 ('M','C','20250101','Selected',300),('M','D','20250101','Selected',400),
 ('TT','A','20250101','Selected',500),('M','A','20250101','Other plan',9000),
 ('M','A','20240101','Selected',9000);''')

    def inputs(self,source):
        pattern=r'\(\s*(SELECT\s+Subdivision\s*,PlanningGroupSalesERP\s*,Date_[\s\S]*?)\)\s+t\s+INNER JOIN'
        matches=list(re.finditer(pattern,source,re.I));self.assertEqual(len(matches),2)
        results=[]
        for match in matches:
            query=match[1].replace('DataWH.planning.PlanSales','plans').replace('DataWH.planning.Goods','goods').replace("N'","'")
            results.append(self.db.execute(query,{'PlanVersion':'Selected','DateFrom':'20250101','DateTo':'20260101'}).fetchall())
        return results

    def test_both_plan_kinds_keep_raw_erp_keys_used_by_fact_rate_lookup(self):
        before=self.inputs(template_source())
        after=self.inputs(patch_source(template_source()))
        expected={('M','1. RAC','Мобильные кондиционеры','Brand A'):100,
            ('M','1. RAC','Мобильные кондиционеры','Brand B'):200,
            ('M','Other','Accessories','Brand C'):300,
            ('M','1. RAC','Бытовые кондиционеры','Brand D'):400,
            ('TT','1. RAC','Мобильные кондиционеры','Brand A'):500}
        for rows in after:
            actual={(r[0],r[1],r[3],r[4]):r[5] for r in rows}
            self.assertEqual(actual,expected)
        for old,new in zip(before,after):
            self.assertEqual(sum(r[5] for r in old),sum(r[5] for r in new))
        # Reproduce the real mismatch: older plan input merged both mobile
        # brands into '', and erased the non-RAC goods key.
        self.assertTrue(any(r[3]=='Мобильные кондиционеры' and r[4]=='' and r[5]==300 for r in before[0]))
        self.assertTrue(any(r[1]=='Other' and r[3]=='' and r[4]=='' for r in before[0]))


class ParticipatingPlanValidationTests(SimpleTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.create_function('PATINDEX',2,lambda pattern,text:(re.search(r'[^0-9. ]',text).start()+1) if re.search(r'[^0-9. ]',text) else 0)
        self.db.execute('''CREATE TABLE pf(Подразделение,Канал,ТипКоэф,ГруппаПлановПродаж,
 АвторСделки,СуммаПланUSD REAL,K0 REAL,K1 REAL,K2 REAL,K3 REAL,K4 REAL)''')
        self.where=(INVALID+' AND '+ELIGIBLE).replace("N'","'")

    def add(self,branch='M',channel='Dealer',kind='Политики',pg='1. RAC',author='Нет факта для распределения',plan=100,rate=.01):
        self.db.execute('INSERT INTO pf VALUES (?,?,?,?,?,?,?,?,?,?,?)',(branch,channel,kind,pg,author,plan,*([rate]*5)))

    def invalid_count(self):return self.db.execute('SELECT COUNT(*) FROM pf WHERE '+self.where).fetchone()[0]

    def test_heat_and_dpp_policies_do_not_block_before_their_later_exclusion(self):
        self.add(pg='4. HEAT');self.add(pg='HEAT');self.add(channel='ДПП')
        self.assertEqual(self.invalid_count(),0)
        self.add();self.assertEqual(self.invalid_count(),1)

    def test_heat_and_dpp_sales_still_participate_and_need_complete_rates(self):
        self.add(channel='ДПП',kind='Продажи',pg='4. HEAT',author='Продажи',rate=None)
        self.assertEqual(self.invalid_count(),1)

    def test_other_existing_subdivision_exclusions_and_zero_plan_are_respected(self):
        for branch in EXCLUDED:self.add(branch=branch)
        self.add(plan=0)
        self.assertEqual(self.invalid_count(),0)

    def test_real_missing_author_or_rates_remain_errors(self):
        self.add();self.add(author='Known',rate=None)
        self.assertEqual(self.invalid_count(),2)
        self.add(author='Known')
        self.assertEqual(self.invalid_count(),2)

    def test_diagnostic_names_actual_goods_and_distinguishes_cause(self):
        for field in ('[Подразделение]','[ГруппаПлановПродаж]','[ГруппаТоваров]','[Марка]','[ТипКоэф]','[СуммаПланUSD]'):
            self.assertIn(field,PLAN_GUARD)
        self.assertIn('Нет факта авторов для распределения плана',PLAN_GUARD)
        self.assertIn('Нет полного набора K0–K4',PLAN_GUARD)
        self.assertIn('строк с проблемой',PLAN_GUARD)
