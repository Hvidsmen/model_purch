import sqlite3
from pathlib import Path

from django.test import SimpleTestCase

from .services.pilot_formula import GLOBAL_SUBDIVISION, patch_calculation
from .services.payout_threshold import parameterize


SOURCE = """SELECT АвторСделки,
 IIF(CASE WHEN VariationCalculate='Подразделение' THEN
 SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
 / SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
 ELSE 0 END <0.8 OR
 SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,Год,ТипКоэф)
 / SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,Год,ТипКоэф) <0.8 OR
 SUM(СуммаОтгрузокUSD) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф)
 / SUM(IIF(СуммаПланUSD=0,1,СуммаПланUSD)) OVER(PARTITION BY Подразделение,АвторСделки,Год,ТипКоэф) <0.8,
 0, Potential) AS Paid FROM Source WHERE Квартал=3"""


class GlobalPayoutTests(SimpleTestCase):
    def payout(self, actual, threshold=.8, mode=GLOBAL_SUBDIVISION, plan=100):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE Source (Подразделение, АвторСделки, Год, ТипКоэф, VariationCalculate, СуммаОтгрузокUSD REAL, СуммаПланUSD REAL, Potential REAL, Квартал)')
            # Plan-only row plus many fact-only rows: a zero plan is not one unit.
            rows = [('A', 'Plan', 2026, 'Политики', mode, 0, plan, 0, 3)]
            rows += [('A', f'Employee{i}', 2026, 'Политики', mode, actual / 200, 0, 5, 3) for i in range(200)]
            # Other branches, years, worker types and periods must not affect Q3.
            rows += [('B', 'OtherBranch', 2026, 'Политики', mode, 0, 9999, 0, 3),
                     ('A', 'OtherYear', 2025, 'Политики', mode, 0, 9999, 0, 3),
                     ('A', 'OtherType', 2026, 'Продажи', mode, 0, 9999, 0, 3),
                     ('A', 'OtherQuarter', 2026, 'Политики', mode, 0, 9999, 0, 4)]
            db.executemany('INSERT INTO Source VALUES (?,?,?,?,?,?,?,?,?)', rows)
            sql = parameterize('CREATE PROC p AS ' + patch_calculation(SOURCE))
            sql = sql.split('AS SELECT', 1)[1]
            return dict(db.execute(('SELECT' + sql).replace("N'", "'"), {'PayoutThreshold': threshold}))['Employee0']

    def test_department_passes_despite_fact_only_rows_and_personal_plan(self):
        self.assertEqual(self.payout(100.32), 5)

    def test_department_below_threshold_is_blocked_and_equality_passes(self):
        self.assertEqual(self.payout(75), 0)
        self.assertEqual(self.payout(100, threshold=1), 5)
        self.assertEqual(self.payout(100, threshold=1.01), 0)

    def test_personal_formula_retains_its_existing_conditions(self):
        self.assertEqual(self.payout(100.32, mode='Подразделение'), 0)

    def test_zero_total_plan_does_not_divide_by_zero_or_grant_a_premium(self):
        self.assertEqual(self.payout(100, plan=0), 0)

    def test_full_review_procedure_supports_projected_performance_cases(self):
        path = Path(__file__).parent / 'sql/review/sp_CalcBySub.refactored.sql'
        if not path.exists():
            self.skipTest('Optional SQL review snapshot unavailable')
        result = parameterize(patch_calculation(path.read_text()))
        self.assertIn('COALESCE((', result)
        self.assertIn('< @PayoutThreshold', result)
