"""Replay generated rate selectors, minimums and coalescing against small data."""
import re
import sqlite3
from django.test import SimpleTestCase
from .services.recalculation_2025_sql import patch_source, SETUP
from .test_recalculation_2025 import source


class FileRateFallbackTests(SimpleTestCase):
    def setUp(self):
        self.sql=patch_source(source())
        pattern=(r'OUTER APPLY \(SELECT TOP\(1\) c\.PGSales[\s\S]*?'
            r'FROM #test2025_coeff c(?P<lookup>[\s\S]*?)\) chosen'
            r'(?P<mins>[\s\S]*?)CROSS APPLY \(SELECT (?P<values>[\s\S]*?)\) sc')
        self.lookups=list(re.finditer(pattern,self.sql))
        self.assertEqual(len(self.lookups),4)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE coeff(PGSales,Group_,Brand,TypeCoeff,DateFrom,DateTo,
 Subdivision,K0 REAL,K1 REAL,K2 REAL,K3 REAL,K4 REAL,VariationCalculate);
 CREATE TABLE products(ProductCode,PlanningGroupSalesERP,GroupERP,BrandName);
 CREATE TABLE subs(SubdivisionKey,SubdivisionName);
 CREATE TABLE sales(ProductCode,Subdivision,ClosedDealDate);
 CREATE TABLE plans(PlanningGroupSalesERP,GroupERP,[Марка(Бренд)],Date_,Subdivision);
 INSERT INTO subs VALUES('M','Branch');''')
        # Min across every K of a kind/group, not one minimum per segment.
        rows=[('RAC','AC','Exact','Политики',[.03,.02,.01,.04,.05]),
              ('RAC','AC','Exact','Продажи',[.08,.07,.06,.09,.1]),
              ('VRF','VRF','*','Политики',[.004,.004,.004,.004,.004]),
              ('VRF','VRF','*','Продажи',[.04,.04,.04,.04,.04]),
              ('RAC','*','*','Политики',[.015]*5),
              ('RAC','AC','*','Политики',[.018]*5)]
        for pg,goods,brand,kind,values in rows:
            self.db.execute('INSERT INTO coeff VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (pg,goods,brand,kind,'20250101','20251231','Branch',*values,'Formula'))
        self.db.execute('CREATE TABLE rates AS SELECT * FROM coeff')
        self.refresh_minima()

    def refresh_minima(self):
        self.db.execute('DROP TABLE IF EXISTS group_min');self.db.execute('DROP TABLE IF EXISTS table_min')
        start=SETUP.index('SELECT r.PGSales,r.TypeCoeff,MIN(')
        stop=SETUP.index('CREATE UNIQUE INDEX IX_test2025_group_min')
        group=SETUP[start:stop].replace('INTO #test2025_group_min','').replace('#test2025_rates','rates')
        group=group.replace('CROSS APPLY (VALUES(r.K0),(r.K1),(r.K2),(r.K3),(r.K4)) v(Coeff_)',
            'CROSS JOIN json_each(json_array(r.K0,r.K1,r.K2,r.K3,r.K4)) v').replace('v.Coeff_','v.value')
        self.db.execute('CREATE TABLE group_min AS '+group)
        table=SETUP[SETUP.index('SELECT TypeCoeff,MIN(Minimum_)'):].strip()
        table=table.replace('INTO #test2025_table_min','').replace('#test2025_group_min','group_min')
        self.db.execute('CREATE TABLE table_min AS '+table)

    def query(self,index,pg,goods,brand):
        self.db.execute('DELETE FROM sales');self.db.execute('DELETE FROM plans');self.db.execute('DELETE FROM products')
        self.db.execute('INSERT INTO products VALUES (?,?,?,?)',('P',pg,goods,brand))
        self.db.execute('INSERT INTO sales VALUES (?,?,?)',('P','M','20250601'))
        self.db.execute('INSERT INTO plans VALUES (?,?,?,?,?)',(pg,goods,brand,'20250601','M'))
        match=self.lookups[index]
        lookup='SELECT c.rowid FROM coeff c '+match['lookup']+' LIMIT 1'
        base=('sales s JOIN products p ON p.ProductCode=s.ProductCode JOIN subs sub ON sub.SubdivisionKey=s.Subdivision'
              if index<2 else 'plans t JOIN subs s ON s.SubdivisionKey=t.Subdivision')
        query='SELECT '+match['values']+' FROM '+base+' LEFT JOIN coeff chosen ON chosen.rowid=('+lookup+') '+match['mins']
        query=query.replace('#test2025_group_min','group_min').replace('#test2025_table_min','table_min').replace("N'","'")
        self.last_query=query
        return self.db.execute(query).fetchall()

    def test_exact_rates_preserve_distinct_k_values_in_fact_and_plan(self):
        for index in (0,2):
            rows=self.query(index,'RAC','AC','Exact')
            self.assertEqual(len(rows),1);self.assertEqual(rows[0][-5:],(.03,.02,.01,.04,.05))

    def test_goods_and_group_wildcards_precede_minimum_fallback(self):
        for index in (0,2):
            self.assertEqual(self.query(index,'RAC','AC','Other')[0][-5:],(.018,)*5)
            self.assertEqual(self.query(index,'RAC','Other','Other')[0][-5:],(.015,)*5)

    def test_missing_exact_sales_uses_same_kind_group_minimum_for_all_k(self):
        for index in (1,3):
            row=self.query(index,'RAC','Other','Other')[0]
            self.assertEqual(row[-5:],(.06,)*5)
            self.assertEqual(row[:3],('RAC','Other','Other'))

    def test_missing_group_uses_file_table_minimum_without_mixing_kinds(self):
        for index in range(4):
            row=self.query(index,'OtherPG','Other','Other')[0]
            self.assertEqual(row[-5:],(.004,)*5 if index%2==0 else (.04,)*5)

    def test_exact_zero_is_not_replaced_by_minimum(self):
        self.db.execute("UPDATE coeff SET K0=0 WHERE Brand='Exact' AND TypeCoeff='Политики'")
        self.assertEqual(self.query(0,'RAC','AC','Exact')[0][-5],0)

    def test_genuinely_absent_kind_still_has_no_invented_rate(self):
        self.db.execute("DELETE FROM coeff WHERE TypeCoeff='Продажи'")
        self.db.execute("DELETE FROM rates WHERE TypeCoeff='Продажи'");self.refresh_minima()
        self.assertEqual(self.query(1,'OtherPG','Other','Other')[0][-5:],(None,)*5)
        self.assertIn('КоэфициентМотивации IS NULL OR K0 IS NULL',self.sql)

    def test_real_plan_type_projection_binds_for_exact_group_and_table_fallback(self):
        from .services.recalculation_2025_sql import validate_rate_projection
        validate_rate_projection(self.sql)
        for index in range(4):
            for pg,goods,brand in [('RAC','AC','Exact'),('RAC','Other','Other'),('OtherPG','Other','Other')]:
                self.query(index,pg,goods,brand)
                # Execute the real downstream plan expression, with the generated
                # rate projection as sc. Previously this failed: no TypeCoeff.
                rows=self.db.execute('WITH sc AS ('+self.last_query+') SELECT TRIM(sc.TypeCoeff) TypeCoeff_ FROM sc').fetchall()
                self.assertEqual(rows,[('Политики' if index%2==0 else 'Продажи',)])

    def test_projection_contract_rejects_missing_type_before_sql_installation(self):
        from django.core.exceptions import ValidationError
        from .services.recalculation_2025_sql import validate_rate_projection
        damaged=self.sql.replace("N'Политики' AS TypeCoeff,",'').replace("N'Продажи' AS TypeCoeff,",'')
        with self.assertRaisesMessage(ValidationError,'typecoeff'):
            validate_rate_projection(damaged)

    def test_projection_contract_checks_all_other_downstream_columns(self):
        from django.core.exceptions import ValidationError
        from .services.recalculation_2025_sql import validate_rate_projection
        for name in ('PGSales','Group_','Brand','VariationCalculate','K0','K1','K2','K3','K4'):
            with self.subTest(column=name),self.assertRaises(ValidationError):
                validate_rate_projection(self.sql.replace(' AS '+name, ' AS Missing_'+name))
