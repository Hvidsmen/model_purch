from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from .models import UserProjectAccess

class PlannerGuideTests(TestCase):
    def test_guide_requires_planning_access_and_contains_images_and_pdf(self):
        url=reverse('split_planner_guide')
        self.assertEqual(self.client.get(url).status_code,302)
        user=User.objects.create_user('guide-reader');self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code,403)
        UserProjectAccess.objects.create(user=user,project='sales_planning',role='Планировщик',sales_plan_groups=['RAC'])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Инструкция планировщика')
        self.assertContains(response,'Сохранить в PDF / печать')
        self.assertEqual(response.content.decode().count('<figure>'),13)
        self.assertContains(response,'Дозаписать · только нули и пустые')
        self.assertContains(response,'Утверждает план администратор планирования')
        self.assertEqual(self.client.post(url).status_code,405)
