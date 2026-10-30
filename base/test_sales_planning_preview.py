from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class SalesPlanningPreviewTests(TestCase):
    def test_preview_is_admin_only_get_without_source_calls(self):
        url = reverse('sales_planning_preview')
        self.assertEqual(self.client.get(url).status_code, 302)
        user = User.objects.create_user('split-reader')
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 403)
        admin = User.objects.create_superuser('split-admin')
        self.client.force_login(admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        html = b''.join(response.streaming_content).decode()
        self.assertIn('План продаж Split', html)
        self.assertIn('демоданные', html)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.post(url).status_code, 405)
