from unittest.mock import patch
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.http import QueryDict
from urllib.parse import urlsplit
from base.testing import authorize_test_case
from .models import Chanel, Subdivision
from .report_navigation_views import legacy_report


class ReportCatalogTests(TestCase):
    def setUp(self):
        self.user = authorize_test_case(self)

    def test_catalog_has_current_reports_without_version_switch(self):
        response = self.client.get(reverse('motivation_reports'))
        self.assertContains(response, 'Отчёты мотивации')
        self.assertContains(response, 'Премии команды')
        self.assertContains(response, 'Моя премия')
        self.assertNotContains(response, 'Текущие отчёты')
        self.assertNotContains(response, 'Новые отчёты')
        self.assertNotContains(response, 'Пилотный просмотр')
        self.assertNotContains(response, 'Версия отчётов')
        # Shared filter styles remain after removing the version tabs.
        self.assertContains(response, '.next-filter')

    def test_previous_catalog_url_redirects_to_single_catalog(self):
        self.assertRedirects(self.client.get(reverse('motivation_reports_next')),
                             reverse('motivation_reports'))

    def test_old_report_links_keep_filters_and_go_to_matching_current_report(self):
        for old, current in [('manager','mine'),('manager-detail','detail'),
                             ('subdivisions','team'),('deals','deals')]:
            with self.subTest(old=old):
                response = self.client.get(reverse('motivation_report',args=[old]),
                    {'brand':['A','B'],'planning':'1. RAC','quarter':'4 Квартал(1-4 кв)','run':'1'})
                self.assertEqual(response.status_code,302)
                target = urlsplit(response.url)
                self.assertEqual(target.path,reverse('motivation_report_next',args=[current]))
                query = QueryDict(target.query)
                self.assertEqual(query.getlist('brand'),['A','B'])
                self.assertEqual(query['planning'],'1. RAC')
                self.assertEqual(query['period'],'4')
                self.assertNotIn('quarter',query)

    def test_old_plan_summary_maps_to_current_comparison(self):
        response = self.client.get(reverse('motivation_plan_summary'),{'scenario':'12'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(urlsplit(response.url).path,reverse('motivation_report_next',args=['executive']))
        self.assertEqual(QueryDict(urlsplit(response.url).query)['plan'],'12')

    @patch('admin_motivation.report_navigation_views.available',return_value={'mine':('Моя премия','')})
    def test_unavailable_target_falls_back_to_authorized_catalog(self, available):
        request = RequestFactory().get('/reports/subdivisions/')
        request.user = self.user
        self.assertEqual(legacy_report(request,'subdivisions').url,reverse('motivation_reports'))

    @patch('admin_motivation.services.execution_tracking.observe_latest',return_value=None)
    def test_separate_filter_includes_merged_thermotrade_with_original_case(self, observe):
        channel = Chanel.objects.create(chanel_name='Catalog test')
        moscow = Subdivision.all_objects.create(subdivision_key='Д-Москва',subdivision_name='Москва',
            subdivision_global='Москва',chanel=channel)
        tt = Subdivision.all_objects.create(subdivision_key='Термотрейд',subdivision_name='ТТ',
            subdivision_global='ТТ',chanel=channel,merged_into=moscow)
        self.assertFalse(Subdivision.objects.filter(pk=tt.pk).exists())
        response = self.client.get(reverse('motivation_report_all_2026'),{'scenario':'separate'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['branches'],['Д-Москва','Термотрейд'])
        response = self.client.get(reverse('motivation_report_all_2026'),{'scenario':'combined'})
        self.assertEqual(response.context['branches'],['Д-Москва'])
