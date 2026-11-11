from unittest.mock import Mock
from django.test import SimpleTestCase
from .services.calculation_live_progress import read,phase


class LiveProgressTests(SimpleTestCase):
    def test_read_is_bound_to_job_and_reports_phase_blocker_without_sql_text(self):
        cursor=Mock();cursor.fetchone.return_value=(74,'suspended','SELECT','LCK_M_S',100,81,200,300,
            'PortalCalcSeparate2026','SELECT * INTO #pf_base FROM motivation.PlanFactSeparate2026 -- private')
        result=read(cursor,'PortalMotivation_test')
        self.assertIn('Раздельный сценарий',result['stage'])
        self.assertIn('плана и факта',result['stage'])
        self.assertIn('81',result['waiting'])
        self.assertNotIn('private',str(result))
        self.assertEqual(cursor.execute.call_args.args[1],'PortalMotivation_test')
        self.assertIn('CONVERT(binary(16),j.job_id)',cursor.execute.call_args.args[0])
        self.assertEqual(result['session_id'],74)

    def test_permissions_failure_keeps_safe_agent_status(self):
        cursor=Mock();cursor.execute.side_effect=RuntimeError('private password')
        self.assertIn('нет доступа',read(cursor,'Job')['stage'])
        self.assertNotIn('password',str(read(cursor,'Job')))

    def test_known_phases_and_waits(self):
        self.assertIn('подготовленного',phase('SELECT * INTO #portal_motivation_sales FROM motivation.PortalPreparedSales'))
        self.assertIn('квартальных',phase('SELECT * INTO motivation.ReportMotivationByDealAuthorAll2026 FROM #pf_base'))
        cursor=Mock();cursor.fetchone.return_value=(74,'suspended','SELECT','PAGEIOLATCH_SH',10,0,20,30,'PortalCalcAll2026','SELECT * FROM #fact')
        result=read(cursor,'Job')
        self.assertIn('Объединённый',result['stage']);self.assertIn('диска',result['waiting'])
