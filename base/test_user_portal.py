from unittest.mock import patch
from django.contrib.auth.models import User, Group
from django.test import TestCase, override_settings
from django.urls import reverse
from .access import ADMIN, POLITICIAN
from .user_views import UserForm


class UserPortalTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('portal-admin', password='Admin-test-9853!')
        self.client.force_login(self.admin)

    def local_data(self, **overrides):
        data = {'username': 'local-test', 'first_name': 'Иван', 'last_name': 'Иванов', 'is_active': 'on',
                'portal_role': '', 'password1': 'Local-test-9274!', 'password2': 'Local-test-9274!'}
        data.update(overrides)
        return data

    def test_create_local_and_edit_without_resetting_password(self):
        response = self.client.post(reverse('portal_users'), self.local_data())
        self.assertRedirects(response, reverse('portal_users'))
        user = User.objects.get(username='local-test')
        self.assertTrue(user.check_password('Local-test-9274!'))
        old = user.password
        response = self.client.post(reverse('portal_users'), self.local_data(user=str(user.pk), password1='', password2='', username='tampered', first_name='Пётр'))
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertEqual(user.username, 'local-test')
        self.assertEqual(user.password, old)
        self.assertEqual(user.first_name, 'Пётр')

    def test_domain_user_has_unusable_password_and_access_group(self):
        group = Group.objects.create(name='Тестовая группа')
        data = self.local_data(username='DAICHI\\tester', password1='', password2='', groups=[str(group.pk)])
        self.assertEqual(self.client.post(reverse('portal_users'), data).status_code, 302)
        user = User.objects.get(username='daichi\\tester')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(list(user.groups.all()), [group])
        self.assertFalse(user.is_staff)

    def test_domain_password_and_duplicate_case_rejected(self):
        self.assertEqual(self.client.post(reverse('portal_users'), self.local_data(username='DAICHI\\tester')).status_code, 200)
        self.assertFalse(User.objects.filter(username__icontains='tester').exists())
        User.objects.create(username='Existing')
        form = UserForm(self.local_data(username='existing'), actor=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)

    def test_personal_profile_required_and_self_lockout_denied(self):
        form = UserForm(self.local_data(portal_role=POLITICIAN), actor=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('report_author', form.errors)
        form = UserForm(self.local_data(username=self.admin.username, is_active='', portal_role=''), actor=self.admin, instance=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)

    def test_search_and_directory_access(self):
        User.objects.create(username='needle-user', first_name='Нужный')
        response = self.client.get(reverse('portal_users'), {'q': 'Нужный'})
        self.assertContains(response, 'needle-user')
        self.assertNotContains(response, 'value="portal-admin"')
        with patch('base.user_views.search_accounts', return_value=[{'username': 'daichi\\test', 'full_name': 'Тестовый Сотрудник'}]) as lookup:
            response = self.client.get(reverse('portal_directory_search'), {'q': 'Тест'})
            self.assertEqual(response.json()['users'][0]['username'], 'daichi\\test')
            lookup.assert_called_once_with('Тест')
        with patch('base.user_views.search_accounts') as lookup:
            self.assertEqual(self.client.get(reverse('portal_directory_search'), {'q': 'ab'}).status_code, 400)
            lookup.assert_not_called()
        with patch('base.user_views.search_accounts', side_effect=RuntimeError('Служба недоступна')):
            self.assertEqual(self.client.get(reverse('portal_directory_search'), {'q': 'test'}).status_code, 503)
        self.client.logout()
        for name in ('portal_users', 'portal_directory_search'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_non_admin_denied_and_preview_button_posts_existing_endpoint(self):
        user = User.objects.create(username='regular', is_active=True)
        self.assertContains(self.client.get(reverse('portal_users')), 'Посмотреть как пользователь')
        response = self.client.post(reverse('portal_user_preview'), {'user': user.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(reverse('portal_users')).status_code, 403)
        self.client.post(reverse('portal_stop_preview'))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('portal_users')).status_code, 403)
        self.assertEqual(self.client.get(reverse('portal_directory_search'), {'q': 'test'}).status_code, 403)

    def test_role_and_group_filters_intersect_with_search_and_match_effective_role(self):
        from .access import DIRECTOR, LOGISTICIAN
        first, second = Group.objects.create(name='Регионы'), Group.objects.create(name='Центр')
        director = User.objects.create(username='filter-director', first_name='Поиск')
        director.groups.add(Group.objects.get(name=DIRECTOR), first, second)
        logistician = User.objects.create(username='filter-logistician', first_name='Поиск')
        logistician.groups.add(Group.objects.get(name=LOGISTICIAN), first)
        unassigned = User.objects.create(username='filter-none')
        conflicted = User.objects.create(username='filter-conflict')
        conflicted.groups.add(Group.objects.get(name=DIRECTOR), Group.objects.get(name=LOGISTICIAN))
        inactive = User.objects.create(username='filter-inactive', is_active=False)
        inactive.groups.add(Group.objects.get(name=DIRECTOR))
        def ids(**params):
            response = self.client.get(reverse('portal_users'), params)
            self.assertEqual(response.status_code, 200)
            return [row.pk for row in response.context['catalog']]
        self.assertEqual(ids(role=[DIRECTOR], group=[str(first.pk)], q='Поиск'), [director.pk])
        self.assertCountEqual(ids(role=[DIRECTOR, LOGISTICIAN], group=[str(first.pk), str(second.pk)]), [director.pk, logistician.pk])
        self.assertCountEqual(ids(role=['none']), [unassigned.pk, conflicted.pk, inactive.pk])
        self.assertIn(self.admin.pk, ids(role=[ADMIN]))
        self.assertNotIn(director.pk, ids(group=['none']))
        self.assertIn(unassigned.pk, ids(group=['none']))
        self.assertEqual(ids(group=['not-an-id']), [])
