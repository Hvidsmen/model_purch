from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from datetime import date
from base.testing import authorize_admin
from .models import Chanel,Subdivision,CuratorEmployee,GlobalCoeffVersion


class CuratorWorkspaceTests(TestCase):
    def setUp(self):
        self.admin=authorize_admin(self.client)
        ch=Chanel.objects.create(chanel_name='Workspace')
        self.a=Subdivision.objects.create(subdivision_key='Москва',subdivision_name='Москва',subdivision_global='Москва',chanel=ch)
        self.b=Subdivision.all_objects.create(subdivision_key='ТТ',subdivision_name='ТТ',subdivision_global='ТТ',chanel=ch,merged_into=self.a)
        self.employee=CuratorEmployee.objects.create(full_name='Куратор Тестовый')
        self.employee.assigned_subdivisions().add(self.a,self.b)
        self.version=GlobalCoeffVersion.objects.create(effective_from=date(2026,1,1),title='Рабочая версия')

    def test_workspace_keeps_physical_branches_and_separate_directory(self):
        response=self.client.get(reverse('motivation_curator_coefficients'),{'version':self.version.pk})
        self.assertContains(response,'Коэффициенты кураторов')
        self.assertContains(response,'Куратор Тестовый')
        for branch in (self.a,self.b):self.assertContains(response,f'subdivision={branch.pk}')
        self.assertContains(response,reverse('motivation_curators'))
        self.assertEqual(self.client.get(reverse('motivation_curators')).status_code,200)

    def test_personal_user_cannot_open_workspace(self):
        user=User.objects.create_user('sales-workspace')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('motivation_curator_coefficients')).status_code,403)

    def test_workspace_is_get_only(self):
        self.assertEqual(self.client.post(reverse('motivation_curator_coefficients'),{}).status_code,405)
