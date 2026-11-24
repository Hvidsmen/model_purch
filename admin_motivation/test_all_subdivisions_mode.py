from unittest.mock import patch
from django.test import TestCase,override_settings
from django.urls import reverse
from .services import calculation_jobs as jobs
from .services.calculation_scope import predicate


@override_settings(MOTIVATION_MAIN_ALL_SUBDIVISIONS=True)
class AllSubdivisionCalculationTests(TestCase):
    def setUp(self):
        from .test_calculation_jobs import CalculationJobsTests
        CalculationJobsTests.setUp(self)

    def test_generated_scope_covers_regions_and_retains_exclusions(self):
        from pathlib import Path
        definition=(Path(__file__).parent/'sql/review/sp_CalcBySub.refactored.sql').read_text()
        definition=definition[definition.index('CREATE OR ALTER PROCEDURE'):].replace('@Year','@FactYear')
        for variant in ('main','all_2026','separate_2026'):
            self.cursor.reset_mock();self.cursor.fetchone.return_value=(definition,None)
            self.cursor.fetchall.return_value=[('SubdivisionKey',),('SubdivisionName',)]
            with patch.object(jobs,'rules',return_value=[]):jobs.prepare(self.cursor,variant)
            view=next(c.args[0] for c in self.cursor.execute.call_args_list if c.args[0].startswith('ALTER VIEW motivation.'))
            self.assertIn("N'Вентиляция'",view)
            if variant=='separate_2026':self.assertIn("IN (N'Д-Москва',N'ТЕРМОТРЕЙД')",view)
            else:
                self.assertNotIn("IN (N'Д-Москва',N'ТЕРМОТРЕЙД')",view)
                self.assertNotIn("COALESCE(m.TargetName,s.SubdivisionName)=N'Д-Москва'",view)

    def test_metadata_records_scope_and_switch_restores_old_predicate(self):
        from .services.execution_tracking import parameters
        self.assertEqual(parameters(self.job)['calculation_subdivisions'],'all')
        self.assertEqual(predicate('branch'),'1=1')
        with override_settings(MOTIVATION_MAIN_ALL_SUBDIVISIONS=False):
            self.assertEqual(parameters(self.job)['calculation_subdivisions'],['Д-Москва','ТЕРМОТРЕЙД'])
            self.assertEqual(predicate('branch'),"branch=N'Д-Москва'")


class AllSubdivisionReportTests(TestCase):
    def setUp(self):
        from .test_reports_next import ReportsNextTests
        ReportsNextTests.setUp(self)
        self.run.parameters.update(transition_rule='moscow-tt-q4-heat-v1',calculation_subdivisions='all')
        self.run.save(update_fields=['parameters'])

    def test_new_scope_does_not_force_moscow_in_report_or_export_link(self):
        for kind in ('team','registry','mine','detail','executive'):
            response=self.client.get(reverse('motivation_report_next',args=[kind]))
            self.assertEqual(response.status_code,200)
            self.assertIn(self.fixture.subs[0].subdivision_key,[b['name'] for b in response.context['branches']])
            self.assertEqual(response.context['query'].getlist('subdivision'),[])
