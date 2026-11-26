from types import SimpleNamespace
from unittest.mock import Mock
from django.db.models import QuerySet
from django.core.exceptions import ValidationError
from django.test import TestCase
from . import test_shareholder_regions as fixtures
from .models import GlobalCoeffVersion,Goods,SubdivisionCoeff,VariationCalculate
from .services import shareholder_regions as regions
import re,sqlite3


class ExactRegionalSettingsTests(TestCase):
    def test_saved_keys_and_formula_match_portal_goods_without_reclassification(self):
        version=fixtures.RegionalRatesTests.make_rates(self)
        QuerySet.update(GlobalCoeffVersion.objects.filter(pk=version.pk),status='draft')
        good=Goods.objects.create(goods_key='PROF-ACC-wildcard',planning_group_sales='3. PROF ACC',group='*',brand='*')
        formula,_=VariationCalculate.objects.get_or_create(variation_name='Подразделение')
        for original in list(SubdivisionCoeff.objects.filter(version=version)):
            original.pk=None;original.goods=good;original.variation_calculate=formula;original.save()
        QuerySet.update(GlobalCoeffVersion.objects.filter(pk=version.pk),status='approved')
        saved=regions.approved_rates()
        row=next(r for r in saved['coefficients'] if r['PGSales']==good.planning_group_sales)
        self.assertEqual((row['PGSales'],row['Group_'],row['Brand'],row['VariationCalculate']),
                         (good.planning_group_sales,good.group,good.brand,'Подразделение'))
        self.assertEqual(saved['key_format'],regions.KEY_FORMAT)
        # The former empty-brand substitution failed this exact native join.
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE rates(PGSales,Group_,Brand,VariationCalculate)')
        db.execute('INSERT INTO rates VALUES(?,?,?,?)',tuple(row[k] for k in ('PGSales','Group_','Brand','VariationCalculate')))
        actual=db.execute("SELECT COALESCE((SELECT VariationCalculate FROM rates WHERE PGSales=? AND Group_='*' AND Brand='*'),'Подразделение(Глобально)')",(good.planning_group_sales,)).fetchone()[0]
        self.assertEqual(actual,'Подразделение')

    def test_installation_normalizes_keys_with_the_installed_sql_functions(self):
        fixtures.RegionalRatesTests.make_rates(self)
        saved=regions.approved_rates();cursor=Mock()
        regions.install_rates(cursor,SimpleNamespace(configuration={'regional_rates':saved}),
                              'SELECT motivation.fn_CoeffGroupERP(p.PG,p.G),motivation.fn_CoeffBrand(p.G,p.B)')
        sql=cursor.execute.call_args.args[0]
        self.assertIn('Group_=DataWH.motivation.fn_CoeffGroupERP(PGSales,Group_)',sql)
        self.assertIn('Brand=DataWH.motivation.fn_CoeffBrand(Group_,Brand)',sql)

    def test_blank_portal_default_is_not_silently_frozen_as_global(self):
        fixtures.RegionalRatesTests.make_rates(self)
        saved=regions.approved_rates()
        self.assertTrue(all(row['DefaultFormula'] is None for row in saved['defaults']))
        self.assertTrue(all(row['fallback_variation']=='' for row in saved['versions']))
        old={**saved};old.pop('key_format')
        with self.assertRaisesMessage(ValidationError,'прежний формат ключей'):
            regions.install_rates(Mock(),SimpleNamespace(configuration={'regional_rates':old}))


class FrozenFallbackTests(TestCase):
    def test_exact_simple_formula_and_blank_default_do_not_become_global(self):
        fact="SELECT COALESCE(sc.VariationCalculate,(SELECT TOP(1) DefaultFormula FROM motivation.PortalFormulaDefaults),N'Подразделение') AS Formula FROM input sub CROSS JOIN input s LEFT JOIN rates sc ON s.ClosedDealDate>=sc.DateFrom;"
        plan="SELECT COALESCE(sc.VariationCalculate,(SELECT TOP(1) DefaultFormula FROM motivation.PortalFormulaDefaults),N'Подразделение') AS Formula FROM input s CROSS JOIN input t LEFT JOIN rates sc ON t.Date_>=sc.DateFrom;"
        patched=regions.fallback_formulas(fact+plan)
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.executescript('CREATE TABLE input(SubdivisionName,ClosedDealDate,Date_); CREATE TABLE rates(DateFrom,VariationCalculate); CREATE TABLE frozen(Date_,DefaultFormula); CREATE TABLE live(DefaultFormula);')
        db.execute("INSERT INTO input VALUES('Д-Питер','2026-06-01','2026-06-01')")
        db.execute("INSERT INTO frozen VALUES('2026-01-01',NULL)")
        db.execute("INSERT INTO live VALUES('Подразделение(Глобально)')")
        queries=re.sub(r'TOP\s*\(1\)\s*','',patched,flags=re.I).replace('#share_region_defaults','frozen').replace('motivation.PortalFormulaDefaults','live').replace("N'","'").split(';')
        def values():return [db.execute(q).fetchone()[0] for q in queries if q.strip()]
        self.assertEqual(values(),['Подразделение']*2)
        db.execute("UPDATE frozen SET DefaultFormula='Подразделение(Глобально)'")
        self.assertEqual(values(),['Подразделение(Глобально)']*2)
        db.execute("INSERT INTO rates VALUES('2026-01-01','Подразделение')")
        self.assertEqual(values(),['Подразделение']*2)
        db.execute('DELETE FROM rates');db.execute('UPDATE frozen SET DefaultFormula=NULL')
        db.execute("UPDATE input SET SubdivisionName='Д-Москва'")
        self.assertEqual(values(),['Подразделение(Глобально)']*2)

    def test_optimized_key_functions_are_used_in_regional_duplicate_checks(self):
        source=fixtures.RegionalSQLTests.compiled(self,'all_2026')
        source+='\nSELECT DataWH.motivation.fn_CoeffGroupERP(N\'3. PROF ACC\',N\'*\'),DataWH.motivation.fn_CoeffBrand(N\'*\',N\'*\');'
        from .services.shareholder_sql import private_sql
        sql=private_sql(source,'combined','keys',original_rules=True,regional_policy=True)
        self.assertIn('c.Group_=DataWH.motivation.fn_CoeffGroupERP(p.PlanningGroupSalesErp,p.GroupERP)',sql)
        self.assertIn('c.Brand=DataWH.motivation.fn_CoeffBrand(p.GroupERP,p.BrandName)',sql)
        self.assertIn('c.Brand=DataWH.motivation.fn_CoeffBrand(p.[ГруппаТоваров],p.[Марка])',sql)
