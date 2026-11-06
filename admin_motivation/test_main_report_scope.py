from unittest.mock import patch
from urllib.parse import urlsplit

from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from . import test_reports_next


class MainReportScopeTests(TestCase):
    def setUp(self):
        test_reports_next.ReportsNextTests.setUp(self)
        self.run.parameters['transition_rule'] = 'moscow-tt-q4-heat-v1'
        self.run.save(update_fields=['parameters'])

    def test_current_calculation_choices_and_export_are_moscow_only(self):
        for kind in ('team', 'registry', 'mine', 'detail', 'executive'):
            url = reverse('motivation_report_next', args=[kind])
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['branches'], [{'name': 'Д-Москва', 'selected': True}])
            self.assertEqual(response.context['query'].getlist('subdivision'), ['Д-Москва'])
            export = QueryDict(urlsplit(response.context['export_url']).query)
            self.assertEqual(export.getlist('subdivision'), ['Д-Москва'])
            reset = self.client.get(url + response.context['reset_url'])
            self.assertEqual(reset.context['selected_branches'], ['Д-Москва'])

    def test_foreign_branch_cannot_reach_report_loader_or_excel(self):
        url = reverse('motivation_report_next', args=['team'])
        with patch('admin_motivation.services.reports_next.actual_reports') as load:
            for params in ({'run': '1'}, {'export': 'xlsx'}):
                response = self.client.get(url, {**params, 'subdivision': ['Д-Москва', 'Д-Питер']})
                self.assertEqual(response.status_code, 400)
            load.assert_not_called()
