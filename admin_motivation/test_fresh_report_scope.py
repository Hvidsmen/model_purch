from copy import copy
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse


class FreshReportScopeTests(TestCase):
    def setUp(self):
        from .test_reports_next import ReportsNextTests
        ReportsNextTests.setUp(self)
        self.run.parameters.update(transition_rule='moscow-tt-q4-heat-v1',calculation_subdivisions=['Д-Москва','ТЕРМОТРЕЙД'])
        self.run.save(update_fields=['parameters'])

    def test_opening_filters_uses_latest_agent_scope_before_show(self):
        latest=copy(self.run)
        latest.parameters={**self.run.parameters,'calculation_subdivisions':'all'}
        for kind in ('team','mine','detail','registry'):
            with patch('admin_motivation.services.execution_tracking.observe_latest',return_value=latest) as audit,patch('admin_motivation.services.reports_next.actual_reports') as load:
                response=self.client.get(reverse('motivation_report_next',args=[kind]))
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.context['execution'].parameters['calculation_subdivisions'],'all')
            self.assertIn(self.fixture.subs[0].subdivision_key,[b['name'] for b in response.context['branches']])
            self.assertEqual(response.context['query'].getlist('subdivision'),[])
            audit.assert_called_once();load.assert_not_called()

    def test_old_actual_scope_is_not_artificially_widened(self):
        with patch('admin_motivation.services.execution_tracking.observe_latest',return_value=self.run):
            response=self.client.get(reverse('motivation_report_next',args=['team']))
        self.assertEqual(response.context['branches'],[{'name':'Д-Москва','selected':True}])
