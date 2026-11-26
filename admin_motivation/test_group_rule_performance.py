from django.test import TestCase,SimpleTestCase
from .services import planning_group_rules as rules
from .services import group_rule_performance as speed
from .test_planning_group_rules import SOURCE


class GroupRulePerformanceSQLTests(SimpleTestCase):
    def test_rule_and_all_minima_are_looked_up_once_per_join(self):
        sql=rules.patch(SOURCE,table='group_rules',minimum_source='rates',optimized=True)
        self.assertEqual(sql.count('OUTER APPLY'),2)
        self.assertEqual(sql.count('SELECT MAX(gd.Date_)'),1)
        self.assertEqual(sql.count('MIN(grc.K0)'),1)
        self.assertIn('MIN(grc.K4)',sql)
        self.assertLess(sql.index(') grmin'),sql.index(';'))
        self.assertNotIn('(SELECT TOP(1) gr.Missing',sql)

    def test_interval_aggregation_keeps_overlaps_nulls_and_independent_minima(self):
        import sqlite3
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('CREATE TABLE rates(DateFrom,DateTo,Subdivision,PGSales,TypeCoeff,K0,K1);')
        values=[('2026-01-01','2026-07-01','A','PROF','Политики',.02,.01),
                ('2026-01-01','2026-07-01','A','PROF','Политики',.01,.03),
                ('2026-06-01','2027-01-01','A','PROF','Политики',.005,None),
                ('2026-01-01','2027-01-01','B','PROF','Политики',.0001,.0001),
                ('2026-01-01','2027-01-01','A','PROF','Продажи',0,0)]
        db.executemany('INSERT INTO rates VALUES(?,?,?,?,?,?,?)',values)
        db.execute('CREATE TABLE cache AS SELECT DateFrom,DateTo,Subdivision,PGSales,TypeCoeff,MIN(K0) K0,MIN(K1) K1 FROM rates GROUP BY DateFrom,DateTo,Subdivision,PGSales,TypeCoeff')
        for on in ('2026-01-01','2026-06-15','2026-07-01','2027-01-01'):
            for end in ('<','<='):
                query="SELECT MIN(K0),MIN(K1) FROM {} WHERE Subdivision='A' AND PGSales='PROF' AND TypeCoeff='Политики' AND ?>=DateFrom AND ?"+end+'DateTo'
                self.assertEqual(db.execute(query.format('rates'),(on,on)).fetchone(),db.execute(query.format('cache'),(on,on)).fetchone())


class GroupRulePerformanceCompilerTests(TestCase):
    def test_private_cache_uses_frozen_region_rates_and_is_built_once(self):
        from .test_shareholder_regions import RegionalSQLTests
        from .services.shareholder_sql import private_sql,temporary_tables
        source=RegionalSQLTests.compiled(self,'all_2026')
        sql=private_sql(source,'combined','performance',original_rules=True,regional_policy=True,planning_groups=True)
        self.assertEqual(sql.count('INTO #share_group_minima'),1)
        self.assertNotIn('__PORTAL_GROUP_MIN_SOURCE_',sql)
        start=sql.index('-- __PORTAL_GROUP_MINIMA_CACHE__')
        end=sql.index('CREATE INDEX IX_share_group_minima',start)
        self.assertIn('FROM #share_region_coefficients',sql[start:end])
        self.assertIn('GROUP BY DateFrom,DateTo',sql[start:end])
        self.assertIn('#share_group_minima',temporary_tables(sql))
        self.assertIn('THROW 50087',sql)
