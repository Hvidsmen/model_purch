from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from .models import MotivationCalculationSchedule
from .operation_views import JobForm, calculations


class CalculationCenterFormTests(SimpleTestCase):
    def test_center_excludes_additional_variant_but_report_form_still_accepts_it(self):
        center = JobForm(plan_versions={}, main_only=True)
        self.assertEqual(center.fields['variant'].clean('main'), 'main')
        with self.assertRaises(ValidationError):
            center.fields['variant'].clean('all_2026')
        report = JobForm(plan_versions={})
        self.assertEqual(report.fields['variant'].clean('all_2026'), 'all_2026')


class CalculationCenterScopeTests(TestCase):
    def test_center_requests_only_main_jobs(self):
        main = MotivationCalculationSchedule.objects.create(name='Ежедневный расчёт', variant='main')
        extra = MotivationCalculationSchedule.objects.create(name='Все кварталы 2026', variant='all_2026')
        request = RequestFactory().get('/admin_motivationadministration/calculations/')
        with patch('admin_motivation.operation_views.admin_required'), patch(
                'admin_motivation.operation_views.loaded_plan_versions', return_value={}), patch(
                'admin_motivation.operation_views.render', return_value=HttpResponse()) as render:
            calculations(request)
        self.assertEqual([job.pk for job in render.call_args.args[2]['jobs']], [main.pk])
        self.assertTrue(MotivationCalculationSchedule.objects.filter(pk=extra.pk).exists())
        self.assertFalse(render.call_args.args[2]['form'].fields['variant'].valid_value('all_2026'))

    def test_posting_a_hidden_service_job_cannot_start_it_from_center(self):
        extra = MotivationCalculationSchedule.objects.create(name='Все кварталы 2026', variant='all_2026')
        request = RequestFactory().post('/admin_motivationadministration/calculations/', {'job': str(extra.pk), 'action': 'run'})
        with patch('admin_motivation.operation_views.admin_required'), patch(
                'admin_motivation.operation_views.loaded_plan_versions', return_value={}), patch(
                'admin_motivation.operation_views.calculation_jobs.start_job') as start:
            with self.assertRaises(Http404):
                calculations(request)
        start.assert_not_called()
