from django.core.exceptions import ValidationError
from django.test import SimpleTestCase,TestCase
from .services import main_formula_scope as scope


class MainScopeUnitTests(SimpleTestCase):
    def test_force_then_restore_preserves_original_rate_and_formula_expressions(self):
        source="SELECT COALESCE(sc.K0,defaults.MinCoeff),COALESCE(sc.VariationCalculate,N'Группа планов продаж') INTO #pf_base FROM rates; DELETE FROM #pf_base WHERE K0 IS NULL; SELECT * FROM #pf_base;"
        main=scope.force(source)
        self.assertIn(scope.PREFIX,main)
        self.assertEqual(scope.shareholder_source(main),source)
        self.assertEqual(scope.shareholder_source(None),None)
        with self.assertRaises(ValidationError):scope.force('SELECT 1')
        with self.assertRaises(ValidationError):scope.shareholder_source(main.replace(scope.END,'/*broken*/'))


    def test_forced_formula_leaves_native_missing_rate_unchanged(self):
        import sqlite3
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('CREATE TABLE rates(K0,VariationCalculate); INSERT INTO rates VALUES(NULL,NULL); CREATE TABLE defaults(MinCoeff); INSERT INTO defaults VALUES(0.0001);')
        source="SELECT COALESCE(sc.K0,defaults.MinCoeff),COALESCE(sc.VariationCalculate,N'Группа планов продаж') FROM rates sc CROSS JOIN defaults"
        main=scope.force(source)
        self.assertEqual(db.execute(main.replace("N'","'")).fetchone(),(.0001,'Подразделение(Глобально)'))
        restored=scope.shareholder_source(main)
        self.assertEqual(db.execute(restored.replace("N'","'")).fetchone(),(.0001,'Группа планов продаж'))


class MainScopeCompilerTests(TestCase):
    def compiled(self,variant='all_2026'):
        from .test_shareholder_regions import RegionalSQLTests
        return RegionalSQLTests.compiled(self,variant)

    def test_ordinary_variants_force_global_without_group_rate_fallback(self):
        for variant in ('main','all_2026','separate_2026'):
            sql=self.compiled(variant)
            self.assertIn(scope.PREFIX,sql)
            self.assertNotIn('__PORTAL_GROUP_RULES__',sql)
            self.assertNotIn('gr.Missing',sql)
            self.assertNotIn('gr.Formula',sql)
            self.assertIn('COALESCE(sc.K0',sql)

    def test_shareholder_removes_main_override_and_restores_frozen_group_choices(self):
        from .services.shareholder_sql import private_sql
        sql=private_sql(self.compiled(),'combined','scope-test',original_rules=True,regional_policy=True,planning_groups=True)
        self.assertNotIn(scope.MARKER,sql)
        self.assertIn('#share_group_rules gr',sql)
        self.assertIn('gr.Formula',sql)
        self.assertIn('gr.Missing',sql)

    def test_main_curators_keep_override_shareholder_curators_remove_it(self):
        from .test_curators import CuratorSQLTests
        from .services.curator_sql import pair_batch,START,END
        from .services.shareholder_sql import private_sql
        source=CuratorSQLTests.definition(self).replace('SELECT * INTO DataWH.motivation.FactAll2026',"SELECT COALESCE(sc.VariationCalculate,N'Подразделение') AS Formula_, * INTO DataWH.motivation.FactAll2026")
        source=scope.force(source)
        main=pair_batch(source,main_scope=True)
        self.assertIn(scope.MARKER,main)
        private=pair_batch(source,group_table='#share_group_rules')
        self.assertNotIn(scope.MARKER,private)
        # The appended curator body may contain another marker: remove it
        # before validating/removing the parent's main-only block.
        appended=self.compiled()+'\n'+START+'\n'+scope.force("SELECT COALESCE(sc.VariationCalculate,N'Подразделение')")+'\n'+END
        sql=private_sql(appended,'combined','curator-scope',planning_groups=True)
        self.assertNotIn(scope.MARKER,sql)
