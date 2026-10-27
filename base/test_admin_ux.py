from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth.models import User, Group
from django.urls import reverse
from .access import ensure_roles, FINANCIER, BRANCH_DIRECTOR
from .models import UserProjectAccess, GroupProjectAccess, PortalReportGroup
from .admin_ux import effective_access


class AdministrationUXTests(TestCase):
    def setUp(self):
        ensure_roles()
        self.admin=User.objects.create_superuser('ux-admin')
        self.user=User.objects.create_user('ux-employee')

    def test_admin_overview_is_guarded_and_reads_no_dwh(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('portal_administration')).status_code,403)
        self.client.force_login(self.admin)
        with patch('admin_motivation.services.calculation_jobs.monitor',side_effect=AssertionError('No live SQL on overview')):
            response=self.client.get(reverse('portal_administration'))
        self.assertContains(response,'Требует внимания')
        self.assertContains(response,'сохранённые сведения')
        self.assertContains(response,'Обслуживание портала и данных')

    def test_personal_deny_and_group_conflict_explained_without_granting_access(self):
        first=Group.objects.create(name='Finance A');second=Group.objects.create(name='Directors B')
        self.user.groups.add(first,second)
        GroupProjectAccess.objects.create(group=first,project='motivation',role=FINANCIER)
        GroupProjectAccess.objects.create(group=second,project='motivation',role=BRANCH_DIRECTOR)
        summary=effective_access(self.user)[0]
        self.assertEqual(summary['role'],'Нет доступа')
        self.assertIn('конфликт',summary['warning'])
        UserProjectAccess.objects.create(user=self.user,project='motivation',role='')
        summary=effective_access(self.user)[0]
        self.assertEqual(summary['role'],'Нет доступа')
        self.assertEqual(summary['source'],'Персональный запрет')
        self.assertFalse(summary['warning'])

    def test_director_summary_uses_real_report_scope(self):
        from admin_motivation.models import Chanel,Subdivision,TypeCoeff
        TypeCoeff.objects.get_or_create(type_coeff_name='Политики')
        channel=Chanel.objects.create(chanel_name='UX')
        branch=Subdivision.objects.create(subdivision_key='UX Moscow',subdivision_name='UX Moscow',subdivision_global='UX Moscow',chanel=channel)
        group=Group.objects.create(name='UX directors');self.user.groups.add(group)
        GroupProjectAccess.objects.create(group=group,project='motivation',role=BRANCH_DIRECTOR)
        scope=PortalReportGroup.objects.create(group=group,portal_role=BRANCH_DIRECTOR);scope.subdivisions.add(branch)
        summary=effective_access(self.user)[0]
        self.assertEqual(summary['branches'],['UX Moscow'])
        self.assertIn('Все сотрудники',summary['description'])
        self.client.force_login(self.admin)
        page=self.client.get(reverse('portal_users'),{'edit':self.user.pk})
        self.assertContains(page,'Итоговый доступ')
        self.assertContains(page,'Группы: UX directors')
        self.assertContains(page,'UX Moscow')
        self.user.is_active=False;self.user.save()
        self.assertEqual(effective_access(self.user)[0]['role'],'Нет доступа')

    def test_update_describes_installed_release_without_claiming_remote_version(self):
        self.client.force_login(self.admin)
        response=self.client.get(reverse('portal_updates'))
        self.assertContains(response,'Что входит в установленную версию')
        self.assertContains(response,'заранее не проверяется')

    def test_daily_schedule_does_not_claim_a_start_before_its_start_date(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from admin_motivation.models import MotivationCalculationSchedule
        job=MotivationCalculationSchedule.objects.create(name='Future daily UX',enabled=True,mode='daily',run_at=datetime(2090,1,15,12,30,tzinfo=ZoneInfo('Europe/Moscow')))
        self.client.force_login(self.admin)
        page=self.client.get(reverse('motivation_calculations'))
        shown=next(row for row in page.context['jobs'] if row.pk==job.pk)
        self.assertTrue(shown.next_run_label.startswith('15.01 12:30'),shown.next_run_label)
