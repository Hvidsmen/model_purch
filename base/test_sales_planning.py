from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class SalesPlanningTests(TestCase):
    def test_access_requires_active_administrator(self):
        for name in ('sales_planning_index', 'sales_planning_split'):
            url = reverse(name)
            self.assertEqual(self.client.get(url).status_code, 302)
            user, _ = User.objects.get_or_create(username='sales-reader-' + name)
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 403)
            user.is_active = False
            user.is_superuser = True
            user.save()
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 302)
            self.client.logout()

    def test_navigation_and_unconfigured_directories_without_dwh_calls(self):
        user = User.objects.create_superuser('sales-admin')
        self.client.force_login(user)
        response = self.client.get(reverse('index_base'))
        self.assertContains(response, 'Модель планирования продаж', count=3)
        self.assertRedirects(self.client.get(reverse('sales_planning_index')), reverse('sales_planning_split'))
        with patch('model_purch.conns.connect_database', side_effect=AssertionError('Unexpected DWH connection')):
            response = self.client.get(reverse('sales_planning_split'))
        self.assertNotContains(response, 'Источник не настроен')
        self.assertContains(response, 'Каналы продаж')
        self.assertContains(response, 'Номенклатура')
        self.assertContains(response, 'name="action" value="load_channels"')
        self.assertNotContains(response, 'localStorage')
        self.assertNotContains(response, 'демоданные')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.post(reverse('sales_planning_split')).status_code, 400)
        self.assertEqual(self.client.post(reverse('sales_planning_index')).status_code, 405)


