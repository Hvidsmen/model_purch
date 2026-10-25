from datetime import date
from unittest.mock import Mock
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_admin
from .models import GlobalCoeffVersion, VariationCalculate
from .services.formula_defaults import patch_defaults, save_default, export_defaults
from .services.pilot_formula import GLOBAL_SUBDIVISION, patch_calculation
from .services.versions import create_version
from .services.coefficient_coverage import check, plan_rows, fact_rows, plan_warning


class FormulaDefaultTests(TestCase):
    def setUp(self):
        from .test_sales_plans import SalesPlanTests
        fixture = SalesPlanTests()
        fixture.setUp()
        fixture.load()
        self.actor = authorize_admin(self.client)
        self.fixture = fixture
        self.version = create_version(date(2026, 1, 1), source_version=fixture.version.pk, fallback_variation=GLOBAL_SUBDIVISION)
        variation, _ = VariationCalculate.objects.get_or_create(variation_name=GLOBAL_SUBDIVISION)
        self.version.subdivision_coefficients.update(variation_calculate=variation)
        self.version.refresh_from_db()

    def test_defaults_are_saved_only_in_editable_draft_and_do_not_change_rates(self):
        before = list(self.version.subdivision_coefficients.values_list('pk', 'motivation_coeff'))
        saved = save_default(self.actor, self.version.pk, self.version.revision, 'Подразделение')
        self.assertEqual(saved.fallback_variation, 'Подразделение')
        self.assertEqual(list(saved.subdivision_coefficients.values_list('pk', 'motivation_coeff')), before)
        with self.assertRaises(ValidationError):
            save_default(self.actor, saved.pk, self.version.revision, GLOBAL_SUBDIVISION)
        with self.assertRaises(ValidationError):
            save_default(self.actor, self.fixture.version.pk, self.fixture.version.revision, GLOBAL_SUBDIVISION)
        with self.assertRaises(ValidationError):
            save_default(self.actor, saved.pk, saved.revision, 'unknown')

    def test_coverage_distinguishes_missing_keys_partial_segments_and_explicit_formulas(self):
        rows = list(plan_rows(self.fixture.scenario))
        result = check(self.version, rows, 'Политики')
        self.assertEqual(result['issues'], [])
        row = self.version.subdivision_coefficients.filter(type_coeff__type_coeff_name='Политики', subdivision=self.fixture.subs[0]).first()
        row.delete()
        result = check(self.version, rows, 'Политики')
        self.assertEqual(result['missing'], 1)
        self.assertFalse(result['issues'][0]['uses_fallback'])  # Other cells still specify the exact formula.
        row_source = dict(rows[0], brand='New brand without settings')
        missing = check(self.version, [row_source], 'Политики')['issues'][0]
        self.assertTrue(missing['uses_fallback'])
        self.assertEqual(missing['formula'], GLOBAL_SUBDIVISION)
        old = self.fixture.version.subdivision_coefficients.first().variation_calculate
        self.version.subdivision_coefficients.update(variation_calculate=old)
        self.assertGreater(check(self.version, rows, 'Политики')['formula_mismatches'], 0)
        self.assertIn('examples', plan_warning(self.version, self.fixture.scenario))

    def test_export_includes_blank_legacy_boundary_without_changing_coefficients(self):
        cursor = Mock()
        export_defaults(cursor, [self.fixture.version, self.version])
        rows = cursor.executemany.call_args.args[1]
        self.assertEqual(rows, [(date(2001, 1, 1), None), (date(2026, 1, 1), GLOBAL_SUBDIVISION)])

    def test_fact_diagnosis_uses_live_classification_functions_and_bound_filters(self):
        cursor, connection = Mock(), Mock()
        cursor.fetchone.return_value = ('SELECT motivation.fn_CoeffGroupERP(p.PG,p.G),motivation.fn_CoeffBrand(p.G,p.B)', 1, 2)
        cursor.description = [('subdivision',), ('date',), ('planning',), ('group',), ('brand',), ('amount',), ('documents',)]
        cursor.fetchmany.return_value = []
        connector = Mock(return_value=(connection, cursor))
        attack = "x'); DELETE FROM Sales;--"
        self.assertEqual(fact_rows(2026, attack, connector), [])
        sql, *args = cursor.execute.call_args.args
        self.assertIn('motivation.fn_CoeffBrand(p.GroupERP,p.BrandName)', sql)
        self.assertNotIn(attack, sql)
        self.assertEqual(args, [date(2026, 1, 1), date(2027, 1, 1), attack])
        connection.commit.assert_not_called()
        connection.close.assert_called_once()

    def test_editor_and_diagnostic_pages_show_setting_and_missing_items(self):
        response = self.client.get(reverse('global_coeff_admin_motivation'), {'version': self.version.pk})
        self.assertContains(response, 'Сохранить формулу по умолчанию')
        response = self.client.get(reverse('motivation_coefficient_coverage'), {'version': self.version.pk, 'plan': self.fixture.scenario.pk, 'run': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Проверено сочетаний')
        self.assertContains(response, GLOBAL_SUBDIVISION)

    def test_approved_default_is_immutable_and_export_keeps_numeric_rates(self):
        from .services.approval import approve_review, review_approval
        from .services.sql_export import export_motivation, COEFFICIENT_INSERT
        before = sorted(self.version.subdivision_coefficients.values_list('motivation_coeff', flat=True))
        approved = approve_review(review_approval(self.version)['token'])
        with self.assertRaises(ValidationError):
            save_default(self.actor, approved.pk, approved.revision, 'Подразделение')
        approved.fallback_variation = 'Подразделение'
        with self.assertRaises(ValidationError):
            approved.save()
        cursor, connection = Mock(), Mock()
        export_motivation(Mock(return_value=(connection, cursor)), manage_connection=False)
        coefficient_rows = [call.args[1] for call in cursor.executemany.call_args_list if call.args[0] == COEFFICIENT_INSERT][0]
        self.assertEqual(sorted(row[6] for row in coefficient_rows), before)
        default_rows = [call.args[1] for call in cursor.executemany.call_args_list if 'PortalFormulaDefaults' in call.args[0]][0]
        self.assertEqual(default_rows, [(date(2026, 1, 1), GLOBAL_SUBDIVISION)])
        connection.commit.assert_not_called()


class FormulaSQLTests(TestCase):
    def test_fallback_keeps_exact_formula_rates_and_uses_each_selects_date(self):
        template = "SELECT COALESCE(sc.VariationCalculate,CASE WHEN p.PG='RAC' THEN 'Группа планов продаж' ELSE 'Подразделение' END) AS VariationCalculate, COALESCE(sc.K0,0.008*@default_coeff) AS rate FROM Source {alias} LEFT JOIN Coeff sc ON {alias}.{date} BETWEEN sc.DateFrom AND sc.DateTo"
        source = template.format(alias='s', date='ClosedDealDate') + ' UNION ALL ' + template.format(alias='t', date='Date_')
        changed, count = patch_defaults(source, required=True)
        self.assertEqual(count, 2)
        self.assertIn('fd.Date_<=s.ClosedDealDate', changed)
        self.assertIn('fd.Date_<=t.Date_', changed)
        self.assertEqual(changed.count('COALESCE(sc.K0,0.008*@default_coeff)'), 2)
        self.assertEqual(changed.count('COALESCE(sc.VariationCalculate,'), 2)
        self.assertIn('ORDER BY fd.Date_ DESC', changed)

    def test_unknown_join_fails_closed_instead_of_borrowing_another_date(self):
        source = 'SELECT COALESCE(sc.VariationCalculate,N\'Подразделение\') FROM Unknown UNION ALL SELECT COALESCE(sc.VariationCalculate,N\'Подразделение\') FROM Known s LEFT JOIN C sc ON s.Date_ BETWEEN sc.DateFrom AND sc.DateTo'
        with self.assertRaises(ValidationError):
            patch_defaults(source, True)
        with self.assertRaises(ValidationError):
            patch_defaults('SELECT 1', True)
        self.assertEqual(patch_defaults('SELECT 1'), ('SELECT 1', 0))

    def test_global_formula_extension_still_prepares_with_lookup_subquery(self):
        source = """WITH base AS (SELECT COALESCE(sc.VariationCalculate,N'Подразделение') AS VariationCalculate
FROM Sales s LEFT JOIN Coeff sc ON s.ClosedDealDate>=sc.DateFrom AND s.ClosedDealDate<sc.DateTo)
SELECT IIF(SUM(Fact) OVER(PARTITION BY SubdivisionName,АвторСделки)/SUM(Plan_) OVER(PARTITION BY SubdivisionName,АвторСделки)<0.8,0,
CASE WHEN VariationCalculate='Подразделение' THEN SUM(Fact) OVER(PARTITION BY SubdivisionName,АвторСделки) ELSE 0 END) FROM base"""
        changed, count = patch_defaults(source, True)
        self.assertEqual(count, 1)
        changed = patch_calculation(changed)
        self.assertIn(GLOBAL_SUBDIVISION, changed)
        self.assertIn('PortalFormulaDefaults', changed)

    def test_date_selection_exact_priority_and_legacy_reset_execute_without_rate_changes(self):
        import sqlite3
        source = """SELECT s.Date_,s.Branch,COALESCE(sc.VariationCalculate,
CASE WHEN s.PG='RAC' THEN 'Группа планов продаж' ELSE 'Подразделение' END) AS Formula,
COALESCE(sc.K0,0.008) AS Rate FROM Sales s LEFT JOIN Coeff sc
ON s.Date_>=sc.DateFrom AND s.Date_<sc.DateTo AND s.Branch=sc.Branch ORDER BY s.Date_,s.Branch"""
        changed, _ = patch_defaults(source, True)
        # Adapt only T-SQL's Unicode prefix and TOP syntax for a real SQL execution.
        portable = changed.replace("N'", "'").replace('SELECT TOP (1)', 'SELECT').replace('ORDER BY fd.Date_ DESC)', 'ORDER BY fd.Date_ DESC LIMIT 1)')
        with sqlite3.connect(':memory:') as database:
            database.execute("ATTACH DATABASE ':memory:' AS motivation")
            database.execute('CREATE TABLE motivation.PortalFormulaDefaults(Date_,DefaultFormula)')
            database.executemany('INSERT INTO motivation.PortalFormulaDefaults VALUES (?,?)', [('2001-01-01', None), ('2026-01-01', GLOBAL_SUBDIVISION), ('2027-01-01', None)])
            database.execute('CREATE TABLE Sales(Date_,PG,Branch)')
            database.executemany('INSERT INTO Sales VALUES (?,?,?)', [('2025-07-01','RAC','A'), ('2026-07-01','RAC','A'), ('2026-07-01','RAC','B'), ('2027-07-01','RAC','A')])
            database.execute('CREATE TABLE Coeff(DateFrom,DateTo,Branch,VariationCalculate,K0)')
            database.execute("INSERT INTO Coeff VALUES ('2026-01-01','2027-01-01','B','Подразделение',0.123)")
            rows = database.execute(portable).fetchall()
        self.assertEqual([row[2] for row in rows], ['Группа планов продаж', GLOBAL_SUBDIVISION, 'Подразделение', 'Группа планов продаж'])
        self.assertEqual([row[3] for row in rows], [.008, .008, .123, .008])
