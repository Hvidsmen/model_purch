from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class SalesPlanningTests(TestCase):
    def test_access_requires_active_administrator(self):
        for name in ('sales_planning_index', 'sales_planning_split', 'sales_planning_directories'):
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
            response = self.client.get(reverse('sales_planning_directories'))
        self.assertNotContains(response, 'Источник не настроен')
        self.assertContains(response, 'Каналы продаж')
        self.assertContains(response, 'Номенклатура')
        self.assertContains(response, 'name="action" value="load_channels"')
        self.assertNotContains(response, 'localStorage')
        self.assertNotContains(response, 'демоданные')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.post(reverse('sales_planning_directories')).status_code, 400)
        self.assertEqual(self.client.post(reverse('sales_planning_index')).status_code, 405)



    def test_split_is_a_summary_and_directories_have_their_own_active_menu(self):
        self.client.force_login(User.objects.create_superuser('split-menu-admin'))
        with patch('base.sales_planning.load_channels') as channels, patch('base.sales_planning.load_goods') as goods:
            overview = self.client.get(reverse('sales_planning_split'))
            self.assertContains(overview, 'Открыть справочники')
            self.assertNotContains(overview, 'name="action"')
            self.assertContains(overview, 'href="' + reverse('sales_planning_split') + '" aria-current="page"')
            directories = self.client.get(reverse('sales_planning_directories'))
            self.assertContains(directories, 'href="' + reverse('sales_planning_directories') + '" aria-current="page"')
            self.assertContains(directories, 'name="action" value="load_goods"')
            self.assertEqual(self.client.post(reverse('sales_planning_split')).status_code, 405)
            channels.assert_not_called()
            goods.assert_not_called()
