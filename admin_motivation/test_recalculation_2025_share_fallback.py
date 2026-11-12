import re
import sqlite3
from django.test import SimpleTestCase
from .services.recalculation_2025_sql import ALLOCATION,patch_source,template_source
from .services.recalculation_2025_share_fallback import BASE_QUERY,BROAD_SHARES,SHARE_JOIN


class HistoricalAuthorShareFallbackTests(SimpleTestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.create_function('UPPER',1,lambda v:v.upper() if v else v)
        self.db.create_function('PATINDEX',2,lambda pat,text:(m.start()+1) if (m:=re.search(r'[^0-9. ]',text)) else 0)
        self.db.execute('''CREATE TABLE facts(ПодразделениеДокумент,АвторСделки,
 ГруппаПлановПродаж,ГруппаТоваров,Марка,СуммаОтгрузокUSD REAL,ТипКоэф,Год,Подразделение,Канал)''')
        self.db.execute('CREATE TABLE target(SubdivisionKey,PGSales,Group_,Brand,AmountUSD REAL)')

    def add(self,branch,author,pg,goods,brand,amount,kind='Политики',year=2025,channel='Dealer'):
        self.db.execute('INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?)',
            (branch,author,pg,goods,brand,amount,kind,year,branch,channel))

    def adapt(self,sql):
        from .services.subdivision_scope import resolve_scope_sql
        return resolve_scope_sql(sql).replace("N'","'").replace('#fact','facts').replace('#test2025_share_base','base')

    def build(self):
        self.db.execute('DROP TABLE IF EXISTS shares');self.db.execute('DROP TABLE IF EXISTS base')
        exact=ALLOCATION[ALLOCATION.index('SELECT t.*'):ALLOCATION.index('IF EXISTS(SELECT 1 FROM #percent_plan_menger')]
        exact=exact.replace('INTO #percent_plan_menger','')
        self.db.execute('CREATE TABLE shares AS '+self.adapt(exact))
        self.db.execute('CREATE TABLE base AS '+self.adapt(BASE_QUERY))
        self.db.execute('INSERT INTO shares '+self.adapt(BROAD_SHARES))

    def allocate(self,branch,pg,goods,brand,plan=120):
        self.db.execute('DELETE FROM target')
        self.db.execute('INSERT INTO target VALUES (?,?,?,?,?)',(branch,pg,goods,brand,plan))
        # Adapt only APPLY's correlated scope choice into a scalar LEFT JOIN;
        # execute the actual ordered predicate and outer fallback join.
        match=re.search(r'OUTER APPLY \((SELECT TOP\(1\)[\s\S]*?)\) selected_share\s*(LEFT JOIN[\s\S]*)',SHARE_JOIN)
        pick=match[1].replace('SELECT TOP(1)','SELECT')+' LIMIT 1'
        join=match[2].replace('pm.AllocationLevel=selected_share.AllocationLevel', 'pm.AllocationLevel=('+pick+')')
        join=join.replace('#percent_plan_menger','shares')
        source=patch_source(template_source())
        author=re.search(r"COALESCE\(\[АвторСделки\],N'Не распределён'\)",source)[0]
        amount=re.search(r't\.AmountUSD\*COALESCE\(pm\.Percent_,1\)',source)[0]
        sql='SELECT '+author+','+amount+',pm.AllocationLevel FROM target t JOIN target sc ON sc.rowid=t.rowid JOIN target s ON s.rowid=t.rowid '+join
        return self.db.execute(self.adapt(sql)).fetchall()

    def test_exact_scope_precedes_broader_pools_and_never_duplicates_plan(self):
        self.add('M','Exact','RAC','AC','X',50)
        self.add('M','OtherBrand','RAC','AC','Y',950)
        self.add('M','OtherGoods','RAC','Other','Y',9000)
        self.add('TT','Foreign','RAC','AC','X',10000)
        self.build()
        self.assertEqual(self.allocate('M','RAC','AC','X'),[('Exact',120,0)])

    def test_missing_brand_uses_goods_without_brand_within_same_subdivision(self):
        self.add('M','A','RAC','AC','X',30);self.add('M','B','RAC','AC','Y',70)
        self.add('M','Other','RAC','Other','Y',10000);self.add('TT','Foreign','RAC','AC','Z',10000)
        self.build()
        self.assertEqual(sorted(self.allocate('M','RAC','AC','Missing')), [('A',36,1),('B',84,1)])

    def test_missing_goods_then_missing_planning_group_select_expected_whole_scope(self):
        self.add('M','A','RAC','AC','X',30);self.add('M','B','RAC','Other','Y',70)
        self.add('M','C','VRF','VRF','Z',100)
        self.build()
        self.assertEqual(sorted(self.allocate('M','RAC','Missing','Missing')), [('A',36,2),('B',84,2)])
        rows=self.allocate('M','MissingPG','Missing','Missing')
        self.assertEqual(sorted(rows), [('A',18,3),('B',42,3),('C',60,3)])
        self.assertEqual(sum(row[1] for row in rows),120)

    def test_zero_signed_total_uses_positive_net_before_broadening(self):
        self.add('M','A','RAC','AC','X',100);self.add('M','B','RAC','AC','X',-100)
        self.add('M','C','RAC','AC','Y',1000)
        self.build()
        self.assertEqual(sorted(self.allocate('M','RAC','AC','X')), [('A',120,0),('B',0,0)])

    def test_broad_zero_sum_uses_author_net_and_weights_sum_to_one(self):
        self.add('M','A','RAC','AC','X',150);self.add('M','A','RAC','AC','X',-50)
        self.add('M','B','RAC','AC','Y',300);self.add('M','C','RAC','AC','Y',-400)
        self.build()
        self.assertEqual(sorted(self.allocate('M','RAC','AC','Missing')), [('A',30,1),('B',90,1),('C',0,1)])

    def test_no_subdivision_authors_keeps_full_unallocated_plan_without_cross_branch(self):
        self.add('TT','Foreign','RAC','AC','X',1000)
        self.build()
        self.assertEqual(self.allocate('M','RAC','AC','X'),[('Не распределён',120,None)])
        self.assertIn('0 [СуммаМотивацииUSD]',patch_source(template_source()))

    def test_zero_author_nets_and_ineligible_or_other_year_kind_do_not_supply_broad_weights(self):
        self.add('M','Zero','RAC','AC','X',100);self.add('M','Zero','RAC','AC','X',-100)
        self.add('M','WrongYear','RAC','AC','X',1000,year=2024)
        self.add('M','Sales','RAC','AC','X',1000,kind='Продажи')
        self.add('M','Heat','4. HEAT','Heat','X',1000)
        self.add('M','DPP','RAC','AC','X',1000,channel='ДПП')
        self.build()
        self.assertEqual(self.allocate('M','New','New','New'),[('Не распределён',120,None)])


    def test_dpp_and_heat_do_not_enter_exact_author_weights(self):
        self.add('M','Eligible','RAC','AC','X',100)
        self.add('M','DPP','RAC','AC','X',900,channel='ДПП')
        self.add('M','Heat','5. HEAT','Heat','X',1000)
        self.build()
        self.assertEqual(self.allocate('M','RAC','AC','X'),[('Eligible',120,0)])
