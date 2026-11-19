from django.core.exceptions import ValidationError
from django.test import SimpleTestCase,TestCase
from . import test_coefficient_iteration_calculation as fixtures
from .services.coefficient_iteration_calculation import RateBook,MOSCOW,TT
from .services.coefficient_iteration_actual_2026 import policy_formulas,sql_2026
from .services.iteration_live_sales_authors import refresh_private,KEYS


class SourceContractTests(SimpleTestCase):
    def test_selected_formula_snapshot_deduplicates_segments_and_rejects_conflicts(self):
        rates=RateBook(*fixtures.inputs())
        row=[MOSCOW,'Политики','1. RAC','Бытовые кондиционеры','DAIKIN','Марка(Бренд)']
        self.assertEqual(policy_formulas({'coefficients':[row,row]},rates),[tuple(row)])
        other=[*row[:-1],'Подразделение']
        with self.assertRaisesMessage(ValidationError,'разные формулы'):
            policy_formulas({'coefficients':[row,other]},rates)
        # TT settings cannot override Moscow's merged calculation formula.
        self.assertEqual(policy_formulas({'coefficients':[row,[TT,*other[1:]]]},rates),[tuple(row)])

    def test_native_batch_uses_selected_private_formulas_and_defaults(self):
        from .services.coefficient_iteration_2025 import build_sql
        sql=sql_2026(build_sql())
        self.assertNotIn('DataWH.motivation.vSubdivisionMotiveCoeffTo',sql)
        self.assertIn('FROM #iteration_actual_policy_formulas old',sql)
        self.assertIn('FROM #iteration_defaults',sql)
        self.assertIn('FROM #iteration_actual_defaults',sql_2026("SELECT Date_,DefaultFormula FROM motivation.PortalFormulaDefaults WHERE Date_<'20260101'"))
        self.assertNotIn('FROM motivation.PortalFormulaDefaults',sql)
        self.assertIn("SET [Даичи_АвторСделкиНаименование]=NULLIF(LTRIM(RTRIM([Даичи_АвторСделкиНаименование])),N'')",sql)
        self.assertNotIn('PortalOriginalDealAuthor',sql)
        self.assertNotIn('INTO #iteration_live_sales_authors',sql)
        self.assertNotIn('FROM DataWH.dbo.Sales r',sql)
        self.assertNotIn('FROM DataWH.erp.',sql)

    def test_private_author_refresh_is_exact_and_never_changes_live_sales(self):
        sql=refresh_private('#private_sales')
        self.assertIn('FROM DataWH.dbo.Sales r',sql)
        self.assertIn('AuthorCount>1',sql)
        self.assertIn('THROW 50040',sql)
        self.assertIn("live_author.AuthorName,N''",sql)
        self.assertIn('r.ClosedDealDate>=@DateFrom',sql)
        self.assertIn('r.ClosedDealDate<@DateTo',sql)
        self.assertIn('GROUP BY',sql)
        self.assertIn('FROM #private_sales current_sale LEFT JOIN',sql)
        self.assertIn('DocumentNumber',KEYS)
        self.assertIn('IS NULL',sql)
        for forbidden in ('PortalOriginalDealAuthor','DataWH.erp','EmpAuthorID','EmpDealAuthorID','UPDATE DataWH'):
            self.assertNotIn(forbidden,sql)

    def test_live_map_reads_new_names_and_blank_values_with_null_document_keys(self):
        import sqlite3
        db=sqlite3.connect(':memory:')
        try:
            columns=','.join('['+field+'] TEXT' for field in KEYS)+',[Даичи_АвторСделкиНаименование] TEXT'
            for table in ('raw','prepared'):db.execute('CREATE TABLE '+table+'('+columns+')')
            identity=('branch','2026-03-01',None,None,'product','customer','2026-02-01','invoice')
            db.execute('INSERT INTO prepared VALUES ('+','.join('?' for _ in range(9))+')',(*identity,'Stale author'))
            source=refresh_private('prepared').split(';\nIF EXISTS',1)[0]
            source='CREATE TEMP TABLE author_map AS '+source.replace('INTO #iteration_live_sales_authors FROM DataWH.dbo.Sales','FROM raw').replace("N''","''")
            for names,expected_count,expected_name in ((['Current author'],1,'Current author'),([None],1,''),(['Alice','Bob'],2,'Alice'),(['Alice',None],2,'')):
                db.execute('DELETE FROM raw');db.execute('DROP TABLE IF EXISTS author_map')
                for name in names:db.execute('INSERT INTO raw VALUES ('+','.join('?' for _ in range(9))+')',(*identity,name))
                db.execute(source,{'DateFrom':'2026-01-01','DateTo':'2026-10-01'})
                self.assertEqual(db.execute('SELECT AuthorCount,AuthorName FROM author_map').fetchone(),(expected_count,expected_name))
            self.assertEqual(db.execute('SELECT [Даичи_АвторСделкиНаименование] FROM prepared').fetchone()[0],'Stale author')
        finally:db.close()

    def test_all_native_periods_read_same_prepared_author_without_legacy_fallback(self):
        from .services.coefficient_iteration_2025 import build_sql
        from .services.iteration_prepared_author import FIELD,normalize_private
        import sqlite3
        original=build_sql()
        for sql in (original,sql_2026(original)):
            self.assertIn('FROM DataWH.motivation.PortalPreparedSales',sql)
            self.assertNotIn('PortalOriginalDealAuthor',sql)
            self.assertEqual(sql.count(normalize_private('#portal_motivation_sales')),1)
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE private_fact(year INTEGER, ['+FIELD+'] TEXT, PortalOriginalDealAuthor TEXT)')
            db.executemany('INSERT INTO private_fact VALUES(?,?,?)',[(2025,'  Иванов  ','Другой'),(2026,'Петров','Старый'),(2026,' ','Автор только в старом поле')])
            db.execute(normalize_private('private_fact').replace("N''","''"))
            self.assertEqual(db.execute('SELECT ['+FIELD+'] FROM private_fact ORDER BY rowid').fetchall(),[('Иванов',),('Петров',),(None,)])
        finally:db.close()


class FormulaSnapshotTests(TestCase):
    def setUp(self):fixtures.CalculationTests.setUp(self)

    def test_snapshot_contains_only_selected_version_formulas_not_other_revisions(self):
        from .models import SubdivisionCoeff,Goods,TypeCoeff,SegmentCoeff,VariationCalculate,GlobalCoeffVersion
        from .services.coefficient_iteration_runner import formulas_snapshot
        from datetime import date
        good=Goods.objects.create(goods_key='snapshot-formula-test',planning_group_sales='1. RAC',group='Бытовые кондиционеры',brand='DAIKIN')
        kind,_=TypeCoeff.objects.get_or_create(type_coeff_name='Политики')
        first,_=VariationCalculate.objects.get_or_create(variation_name='Марка(Бренд)')
        second,_=VariationCalculate.objects.get_or_create(variation_name='Подразделение')
        other=GlobalCoeffVersion.objects.create(effective_from=date(2026,1,1))
        rows=[]
        for i in (0,1):
            segment,_=SegmentCoeff.objects.get_or_create(segment_name='K'+str(i))
            for version,formula in ((self.version,first),(other,second)):
                rows.append(SubdivisionCoeff(version=version,subdivision=self.subs[0],goods=good,type_coeff=kind,
                    segment=segment,motivation_coeff=.1,manager_coeff=1,variation_calculate=formula))
        # Seed historical records directly; the public manager correctly blocks
        # edits to approved versions, while this test only reads their snapshot.
        from django.db.models import QuerySet
        QuerySet(model=SubdivisionCoeff).bulk_create(rows)
        snapshot=formulas_snapshot(self.version)
        self.assertEqual(snapshot['version_id'],self.version.pk)
        self.assertEqual(len(snapshot['coefficients']),1)
        self.assertEqual(snapshot['coefficients'][0][-1],'Марка(Бренд)')
