import uuid
from unittest.mock import patch
from django.urls import reverse
from django.utils import timezone
from .test_premium_distribution import DistributionTests
from .models import MotivationExecution
from .premium_distribution_views import StartForm,latest_calculation


class LatestDistributionTests(DistributionTests):
    def test_calculation_selector_removed(self):
        form=StartForm(user=self.user)
        self.assertNotIn('execution',form.fields)
        self.assertEqual(form.fields['period'].initial,4)
        self.assertNotContains(self.client.get(reverse('motivation_distributions')),'name="execution"')

    def test_period_selects_namespace_and_ignores_posted_execution(self):
        for period,variant in ((1,'main'),(4,'main'),(5,'main')):
            with patch('admin_motivation.services.execution_tracking.latest_for_report',return_value=self.execution) as latest,patch('admin_motivation.services.premium_distribution.create',return_value=self.batch) as create:
                response=self.client.post(reverse('motivation_distributions'),{'execution':'999999','period':period,'subdivision':self.branch.pk,'currency':'USD'})
                self.assertEqual(response.status_code,302)
                self.assertEqual(latest.call_args.kwargs['variant'],variant)
                self.assertEqual(create.call_args.kwargs['execution'],self.execution)

    def test_latest_completed_excludes_scenarios_and_failed_runs(self):
        for state,variant in (('failed','main'),('completed','separate_2026')):
            MotivationExecution.objects.create(run_id=uuid.uuid4(),job_key=uuid.uuid4(),state=state,started_at=timezone.now(),completed_at=timezone.now(),parameters={'fact_year':2026,'variant':variant})
        self.assertEqual(latest_calculation('main'),self.execution)
        self.assertIsNone(latest_calculation('all_2026'))

    def test_absent_period_result_shows_error_without_creating_snapshot(self):
        with patch('admin_motivation.services.execution_tracking.latest_for_report',return_value=None),patch('admin_motivation.services.premium_distribution.create') as create:
            response=self.client.post(reverse('motivation_distributions'),{'period':1,'subdivision':self.branch.pk,'currency':'USD'})
            self.assertContains(response,'Нет завершённого расчёта')
            create.assert_not_called()
