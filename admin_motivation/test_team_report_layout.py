from contextlib import nullcontext
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from .services.manager_detail import manager_detail_report


class TeamReportLayoutTests(TestCase):
    def setUp(self):
        from .test_reports_next import ReportsNextTests
        ReportsNextTests.setUp(self)

    def test_filters_and_totals_precede_targets_and_targets_stay_in_details(self):
        from .test_manager_detail import detail_rows
        source=manager_detail_report(detail_rows())
        with patch('admin_motivation.services.reports_next.current_run',return_value=nullcontext()),patch('admin_motivation.services.reports_next.load_report',return_value=source):
            response=self.client.get(reverse('motivation_report_next',args=['team']),{'run':'1','type':'Политики'})
        self.assertEqual(response.status_code,200)
        html=response.content.decode()
        self.assertIn('<details class="nr-filter-panel" open>',html)
        totals=html.index('nr-mockup-summary')
        details=html.index('<details class="nr-deep nr-team-calculation-details">')
        targets=html.index('nr-target-overview')
        people=html.index('data-team-tabs')
        self.assertLess(totals,details)
        self.assertLess(details,targets)
        self.assertLess(targets,people)
        self.assertContains(response,'Итоги и выполнение плана')

    def test_shareholder_details_do_not_gain_the_extra_target_overview(self):
        from django.template.loader import render_to_string
        context={'shareholder_report':True,'kind':'team','ledger':{'currency':'USD'},'presentation':{'period_cards':[{'code':'2'}]}}
        html=render_to_string('admin_motivation/reports_next/_team_calculation_details.html',context)
        self.assertNotIn('nr-target-overview',html)
