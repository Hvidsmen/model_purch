from django.test import SimpleTestCase
from django.http import QueryDict
from .test_pilot_calculation import procedure
from .services.pilot_calculation import main_patch
from .services.reports_next import selected_periods
from .services.calculation_pipeline import main_projection,validate_equivalence
from .services.transition_2026 import patch as transition
from .services.calculation_variants import full_year_patch


class MainQuarterTests(SimpleTestCase):
    def test_main_includes_early_quarters_and_preserves_annual_guards(self):
        sql=main_patch(procedure())
        self.assertNotIn('WHERE 1=0 AND',sql)
        self.assertIn('WHERE Квартал=1',sql)
        self.assertIn('IIF(Квартал=4,СуммаМотивацииUSDГод,0)',sql)
        self.assertIn('IF 1=0\nINSERT INTO',sql)

    def test_all_and_individual_period_filters(self):
        self.assertEqual(selected_periods(QueryDict('period=all')),list('12345'))
        for code in '12345':self.assertEqual(selected_periods(QueryDict('period='+code)),[code])

    def test_accelerated_projection_copies_all_quarters(self):
        sql=main_projection()
        self.assertNotIn('SUBSTRING',sql)
        self.assertIn('FROM DataWH.motivation.ReportMotivationByDealAuthorAll2026;',sql)

    def test_switch_restores_q4_calculation_filters_and_projection(self):
        from django.test import override_settings
        from .services.report_periods import pilot_enabled
        with override_settings(MOTIVATION_MAIN_ALL_QUARTERS=False):
            sql=main_patch(procedure())
            self.assertEqual(sql.count('WHERE 1=0 AND'),3)
            self.assertEqual(selected_periods(QueryDict('period=all')),['4','5'])
            self.assertTrue(pilot_enabled('manager-detail',None))
            self.assertIn('SUBSTRING',main_projection())

    def test_local_file_can_disable_temporary_mode(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from django.test import override_settings
        from .services.main_quarter_mode import enabled
        with TemporaryDirectory() as root,override_settings(BASE_DIR=Path(root)):
            folder=Path(root)/'.local';folder.mkdir()
            (folder/'motivation-main-periods.json').write_text('{"all_quarters":false}',encoding='utf-8-sig')
            self.assertFalse(enabled())
            self.assertEqual(main_patch(procedure()).count('WHERE 1=0 AND'),3)
