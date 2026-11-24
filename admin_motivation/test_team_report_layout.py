from contextlib import nullcontext
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from .services.manager_detail import manager_detail_report


class TeamReportLayoutTests(TestCase):
    def setUp(self):
        from .test_reports_next import ReportsNextTests
        ReportsNextTests.setUp(self)

    def test_targets_are_visible_before_totals_and_outside_collapsed_details(self):
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
        self.assertLess(targets,totals)
        self.assertLess(totals,details)
        self.assertEqual(html.count('nr-target-overview'),1)
        self.assertLess(targets,people)
        self.assertContains(response,'Итоги и выполнение плана')

    def test_shareholder_details_do_not_gain_the_extra_target_overview(self):
        from django.template.loader import render_to_string
        context={'shareholder_report':True,'kind':'team','ledger':{'currency':'USD'},'presentation':{'period_cards':[{'code':'2'}]}}
        html=render_to_string('admin_motivation/reports_next/_team_calculation_details.html',context)
        self.assertNotIn('nr-target-overview',html)

    def test_shareholder_team_has_visible_aligned_targets_before_totals(self):
        from django.template.loader import render_to_string
        from .models import ShareholderCalculation
        from datetime import date
        run = ShareholderCalculation(as_of=date(2026,6,24),state='completed')
        context = {'shareholder_report':True,'run':run,'kind':'team','ledger':{'currency':'USD'},
                   'presentation':{'team_target_rows':[{'cells':[{'label':'Всё подразделение','cards':[]}]}]}}
        html = render_to_string('admin_motivation/reports_next/shareholder_report.html',context)
        self.assertEqual(html.count('nr-target-overview'),1)
        self.assertIn('nr-target-matrix-row',html)
        self.assertLess(html.index('nr-target-overview'),html.index('nr-mockup-summary'))
        self.assertIn('shareholder-target-rows-20261125',html)
