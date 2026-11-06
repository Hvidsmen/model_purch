from copy import copy
from decimal import Decimal
from pathlib import Path
import re
import sqlite3
from unittest.mock import MagicMock
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase
from .models import MotivationCalculationSchedule
from .services.calculation_pipeline import validate_equivalence, validate_pair, main_projection, cache_setup
from .services.calculation_jobs import calculation_command, monitor
from .services.shared_sales_preparation import patch as share, SHARED_MARKER
from .services.calculation_sales_isolation import patch as isolate_sales
from .services.pilot_calculation import patch as pilot
from .services.calculation_variants import full_year_patch
from .services.transition_2026 import patch as transition
from .services.annual_accrual import patch_calculation as accrue
from .test_pilot_calculation import procedure
from .test_director_participation import SOURCE
from .test_calculation_performance import source


class FastPipelineTests(SimpleTestCase):
    def job(self, variant='main'):
        return MotivationCalculationSchedule(name='Pipeline',variant=variant,fact_year=2026,
            plan_version="Plan O'Brien",quarter_share=80,year_share=20,payout_threshold=Decimal('.8'))

    def test_all_remaining_sql_tokens_match_between_main_and_combined(self):
        base = procedure().replace(' AS\n',' AS\n'+SOURCE,1)
        main, combined = accrue(transition(pilot(base))), accrue(transition(full_year_patch(base)))
        validate_equivalence(main,combined)
        with self.assertRaises(ValidationError):
            validate_equivalence(main,combined.replace('СуммаМотивацииUSDГод,СуммаМотивацииRURГод','СуммаМотивацииUSDГод*2,СуммаМотивацииRURГод'))
        with self.assertRaises(ValidationError):
            validate_equivalence(main,combined.replace('Квартал=4 OR @FactYear<>2026','Квартал=3 OR @FactYear<>2026'))
        with self.assertRaises(ValidationError):
            validate_equivalence(main,combined.replace("N'ДПП'", "N'Другой канал'"))
        extra = '\nUPDATE DataWH.motivation.ReportMotivationByDealAuthor SET Остаток=1;'
        with self.assertRaises(ValidationError):
            validate_equivalence(main+extra,combined+extra)
        path = Path(__file__).parent/'sql/review/sp_CalcBySub.refactored.sql'
        base = path.read_text().replace('@Year','@FactYear')
        validate_equivalence(accrue(transition(pilot(base))),accrue(transition(full_year_patch(base))))

    def test_pair_must_have_identical_parameters(self):
        main, combined = self.job(),self.job('all_2026')
        validate_pair(main,combined)
        for field, value in [('fact_year',2025),('plan_version','Other'),('quarter_share',70),('payout_threshold',Decimal('.9'))]:
            changed = copy(combined); setattr(changed,field,value)
            with self.assertRaises(ValidationError):
                calculation_command(main,changed)

    def test_main_has_only_two_full_calculations_and_keeps_three_independent_receipts(self):
        main,combined = self.job(),self.job('all_2026')
        sql = calculation_command(main,combined)
        self.assertNotIn('EXEC motivation.PortalCalcBySub',sql)
        self.assertEqual(sql.count('EXEC motivation.PortalCalcAll2026'),1)
        self.assertEqual(sql.count('EXEC motivation.PortalCalcSeparate2026'),1)
        self.assertEqual(sql.count('DECLARE @portalRun'),3)
        self.assertEqual(sql.count('sp_getapplock'),1)
        self.assertLess(sql.index(str(main.key)),sql.index('EXEC motivation.PortalCalcAll2026'))
        self.assertLess(sql.index('EXEC motivation.PortalCalcAll2026'),sql.index('SELECT * INTO DataWH.motivation.Fact'))
        completed = sql.index("State_=N'completed',CompletedAt=SYSUTCDATETIME() WHERE RunId=@portalRun")
        self.assertLess(sql.index('SELECT * INTO DataWH.motivation.Fact'),completed)
        self.assertLess(completed,sql.index('EXEC motivation.PortalCalcSeparate2026'))
        # Only the outer owner releases the lock, on success or failure.
        self.assertEqual(sql.count('sp_releaseapplock'),2)
        self.assertIn('BEGIN TRANSACTION;',sql)
        self.assertIn('IF XACT_STATE() <> 0 ROLLBACK;',sql)

    def test_standalone_main_keeps_original_calculation(self):
        sql = calculation_command(self.job())
        self.assertIn('EXEC motivation.PortalCalcBySub',sql)
        self.assertNotIn('SchemaOnly',sql)

    def test_projection_copies_exact_values_in_both_currencies_without_summing(self):
        from .services.calculation_variants import TABLES
        sql = main_projection()
        with sqlite3.connect(':memory:') as db:
            db.create_function('LOWER',1,lambda s:s.lower())
            for table in TABLES:
                db.execute(f'CREATE TABLE {table}All2026 (КварталМотивации text, USD text, RUB text)')
                db.executemany(f'INSERT INTO {table}All2026 VALUES (?,?,?)',[
                    ('1 Квартал','1.23456789','91.23456789'),
                    ('4 Квартал(1-4 кв)','42.123456789','3888.123456789'),
                    ('Год','10.987654321','1000.987654321')])
            for match in re.finditer(r'SELECT \* INTO (?P<target>[\w.]+) FROM (?P<source>[\w.]+)(?P<where>[^;]*);',sql):
                target, origin = (match[name].replace('DataWH.motivation.','') for name in ('target','source'))
                db.execute(f'CREATE TABLE {target} AS SELECT * FROM {origin}'+match['where'].replace("N'","'"))
                expected = db.execute(f'SELECT * FROM {origin}'+match['where'].replace("N'","'")).fetchall()
                self.assertEqual(db.execute(f'SELECT * FROM {target}').fetchall(),expected)
                self.assertEqual(len(expected),2 if target=='ReportMotivationByDealAuthor' else 3)

    def test_prepared_sales_guard_does_not_skip_later_coefficient_initialization(self):
        sql = share(isolate_sales(source()))
        self.assertIn(SHARED_MARKER,sql)
        self.assertIn('FactYear=@FactYear AND Ready=1',sql)
        self.assertIn('UPDATE #portal_shared_sales_state SET Ready=1 WHERE FactYear=@FactYear',sql)
        self.assertLess(sql.index('переиспользована:'),sql.index('DECLARE @default_coeff'))
        self.assertEqual(share(sql),sql)
        self.assertIn('CROSS JOIN (SELECT 1 AS SchemaOnly)',cache_setup(2026))

    def test_unknown_preparation_is_left_on_original_safe_path(self):
        original = isolate_sales(source()).replace('UPDATE s SET Value_','DECLARE @unsafe int; UPDATE s SET Value_')
        self.assertEqual(share(original),original)

    def test_rowversion_sales_keeps_original_select_into_path(self):
        original = isolate_sales(source())
        cursor = MagicMock()
        cursor.fetchone.return_value = (50,1)
        self.assertEqual(share(original,cursor),original)
        cursor.fetchone.return_value = (50,0)
        self.assertIn(SHARED_MARKER,share(original,cursor))
        original = isolate_sales(source()).replace('SELECT * FROM fact;', 'SELECT * FROM #document_sales;')
        self.assertEqual(share(original),original)

    def test_successful_history_exposes_step_timing_messages(self):
        from datetime import datetime
        connection,cursor = MagicMock(),MagicMock()
        cursor.fetchone.return_value = (datetime(2026,11,6,9),datetime(2026,11,6,9,20),1,'done',None,180)
        cursor.fetchall.return_value = [(1,20261106,90000,2000,1,'Подготовка продаж: 1000 мс; Расчёт all_2026: 100 сек.')]
        data = monitor(self.job(),lambda *args:(connection,cursor))
        self.assertIn('f.run_status=h.run_status',cursor.execute.call_args.args[0])
        self.assertIn('Подготовка продаж',data['history'][0]['message'])
        connection.close.assert_called_once()
