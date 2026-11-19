from django.core.exceptions import ValidationError
from django.test import SimpleTestCase,TestCase
from . import test_coefficient_iteration_calculation as fixtures
from .services.coefficient_iteration_calculation import RateBook,MOSCOW,TT
from .services.coefficient_iteration_actual_2026 import policy_formulas,sql_2026
from .services.iteration_document_authors import create_keys,fill_keys,MAP_SQL,enrich_private,RESOLVED


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
        self.assertIn('da.AuthorCount=1',sql)
        self.assertEqual(sql.count('INTO #iteration_document_authors'),1)
        self.assertIn('UPDATE s SET s.Даичи_АвторСделкиНаименование=da.AuthorName',sql)

    def test_document_lookup_includes_corrections_without_product_line_fanout(self):
        self.assertIn('Документы.РеализацияТоваровУслуг',MAP_SQL)
        self.assertIn('Документы.КорректировкаРеализации',MAP_SQL)
        self.assertIn('rtu.Ссылка=kr.ДокументОснование_Многосостав',MAP_SQL)
        self.assertIn('COUNT(DISTINCT AuthorName)',MAP_SQL)
        self.assertIn('GROUP BY DocumentKey',MAP_SQL)
        self.assertNotIn('РеализацияТоваровУслугТовары',MAP_SQL)
        self.assertNotIn('ModelCodeERP',MAP_SQL)
        self.assertIn('s.PortalOriginalDealAuthor',RESOLVED)
        self.assertLess(RESOLVED.index('s.PortalOriginalDealAuthor'),RESOLVED.index('da.AuthorCount=1'))
        self.assertIn('TOP(0)',create_keys('private_sales'))
        self.assertTrue(fill_keys('private_sales','s.ClosedDealDate>=?').startswith('INSERT INTO'))
        sql=enrich_private('#private_sales')
        self.assertIn("=N'' AND da.AuthorCount=1",sql)
        self.assertNotIn('UPDATE DataWH',sql)


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
