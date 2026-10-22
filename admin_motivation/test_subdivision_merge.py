from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from base.testing import authorize_test_case
from .models import Chanel, Subdivision, DirectorEmployee, Goods, SubdivisionDirectorCoeff, GlobalCoeffVersion
from .services.subdivision_merge import apply, preview
from .services.subdivision_aliases import expand, coefficient_rows


class SubdivisionMergeTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.version = GlobalCoeffVersion.objects.get(effective_from=date(2001, 1, 1))
        channel = Chanel.objects.create(chanel_name='Test')
        self.a, self.b = [Subdivision.objects.create(subdivision_key=name, subdivision_name=name, subdivision_global=name, chanel=channel) for name in ('Old', 'New')]
        self.employee = DirectorEmployee.objects.create(full_name='Test Employee')
        self.employee.subdivisions.add(self.a)
        self.good = Goods.objects.create(goods_key='Test', planning_group_sales='Test', group='Test', brand='Test')
        for branch, value in ((self.a, '.1'), (self.b, '.2')):
            SubdivisionDirectorCoeff.objects.create(version=self.version, subdivision=branch, employee=self.employee, goods=self.good, k0=Decimal(value))

    def test_merge_source_priority_moves_coefficients_and_director(self):
        self.assertEqual(preview(self.a.pk, self.b.pk, self.version.pk)['conflicts'], 1)
        log = apply(self.a.pk, self.b.pk, self.version.pk, 'source')
        self.assertFalse(log.details['sql_synced'])
        self.assertFalse(Subdivision.objects.filter(pk=self.a.pk).exists())
        self.assertFalse(SubdivisionDirectorCoeff.objects.filter(subdivision=self.a, version=self.version).exists())
        row = SubdivisionDirectorCoeff.objects.get(subdivision=self.b, version=self.version)
        self.assertEqual(row.k0, Decimal('.1'))
        self.assertEqual(list(self.employee.subdivisions.all()), [self.b])
        # Edited target must remain authoritative after materializing the merge.
        row.k0 = Decimal('.3')
        row.save()
        self.assertEqual(list(coefficient_rows([('New', row.k0)], 0, [0])), [('New', Decimal('.3'))])
        self.assertEqual(expand(['New'], {'Old': 'New'}), ['New', 'Old'])

    def test_target_priority_and_invalid_self_merge(self):
        with self.assertRaises(ValidationError):
            apply(self.a.pk, self.a.pk, self.version.pk, 'target')
        apply(self.a.pk, self.b.pk, self.version.pk, 'target')
        self.assertEqual(SubdivisionDirectorCoeff.objects.get(subdivision=self.b).k0, Decimal('.2'))

    def test_merge_transfers_access_groups_and_personal_bindings_without_changing_author(self):
        from django.contrib.auth.models import User, Group
        from base.models import PortalReportGroup, MotivationReportAccess
        from base.report_access import politician_scope
        from .models import TypeCoeff
        worker, _ = TypeCoeff.objects.get_or_create(type_coeff_name='Политики')
        group = Group.objects.create(name='Source employees')
        scope = PortalReportGroup.objects.create(group=group, employee_type=worker)
        scope.subdivisions.set([self.a, self.b])
        user = User.objects.create_user('source-politician')
        user.groups.add(group)
        profile = MotivationReportAccess.objects.create(user=user, subdivision=self.a, deal_author='Иванов Иван Иванович')
        info = preview(self.a.pk, self.b.pk, self.version.pk)
        self.assertEqual(info['access_groups'], 1)
        self.assertEqual(info['personal_profiles'], 1)
        log = apply(self.a.pk, self.b.pk, self.version.pk, 'target', expected_fingerprint=info['fingerprint'])
        profile.refresh_from_db()
        self.assertEqual(profile.subdivision, self.b)
        self.assertEqual(profile.deal_author, 'Иванов Иван Иванович')
        self.assertEqual(list(scope.subdivisions.all()), [self.b])
        self.assertEqual(politician_scope(User.objects.get(pk=user.pk))['subdivision'], ['New'])
        self.assertEqual(log.details['access_groups_moved'], 1)
        self.assertEqual(log.details['personal_profiles_moved'], 1)

    def test_changed_coefficients_require_new_preview_without_partial_merge(self):
        info = preview(self.a.pk, self.b.pk, self.version.pk)
        SubdivisionDirectorCoeff.objects.filter(subdivision=self.a).update(k0='.9')
        with self.assertRaisesMessage(ValidationError, 'Данные изменились'):
            apply(self.a.pk, self.b.pk, self.version.pk, 'source', expected_fingerprint=info['fingerprint'])
        self.assertTrue(Subdivision.objects.filter(pk=self.a.pk).exists())
        self.assertEqual(SubdivisionDirectorCoeff.objects.get(subdivision=self.b).k0, Decimal('.2'))

    def test_web_preview_and_confirmation_are_bound_to_same_parameters(self):
        from django.urls import reverse
        url = reverse('motivation_subdivision_merge')
        data = {'source': str(self.a.pk), 'target': str(self.b.pk), 'version': str(self.version.pk), 'policy': 'target'}
        response = self.client.post(url, {**data, 'action': 'apply', 'confirm': 'yes'})
        self.assertContains(response, 'Сначала проверьте объединение')
        self.assertTrue(Subdivision.objects.filter(pk=self.a.pk).exists())
        response = self.client.post(url, {**data, 'action': 'preview'})
        self.assertEqual(response.status_code, 200)
        token = response.context['preview_token']
        self.assertTrue(token)
        response = self.client.post(url, {**data, 'action': 'apply', 'confirm': 'yes', 'policy': 'source', 'preview_token': token})
        self.assertContains(response, 'Параметры изменились после проверки')
        self.assertTrue(Subdivision.objects.filter(pk=self.a.pk).exists())
        response = self.client.post(url, {**data, 'action': 'apply', 'confirm': 'yes', 'preview_token': token})
        self.assertRedirects(response, url)
        self.assertFalse(Subdivision.objects.filter(pk=self.a.pk).exists())

    def test_admin_page_exposes_quick_pair_for_real_names_and_restricts_other_roles(self):
        from django.urls import reverse
        from django.contrib.auth.models import User, Group
        from base.access import DEPUTY_DIRECTOR
        self.a.subdivision_key = 'ТЕРМОТРЕЙД'
        self.a.save(update_fields=['subdivision_key'])
        self.b.subdivision_key = 'Д-Москва'
        self.b.save(update_fields=['subdivision_key'])
        response = self.client.get(reverse('motivation_subdivision_merge'))
        self.assertContains(response, 'Термотрейд → Д-Москва')
        self.assertEqual(response.context['pair_source'], self.a)
        self.assertEqual(response.context['pair_target'], self.b)
        user = User.objects.create_user('not-merge-admin')
        user.groups.add(Group.objects.get(name=DEPUTY_DIRECTOR))
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('motivation_subdivision_merge')).status_code, 403)
        self.assertEqual(self.client.post(reverse('motivation_subdivision_merge'), {'action': 'sync_sql'}).status_code, 403)

    def test_existing_merge_is_detected_even_when_source_is_hidden_by_active_manager(self):
        incoming = Subdivision.all_objects.create(subdivision_key='Already merged', subdivision_name='Already merged', subdivision_global='Already merged', chanel=self.a.chanel, merged_into=self.b)
        self.assertFalse(Subdivision.objects.filter(pk=incoming.pk).exists())
        with self.assertRaisesMessage(ValidationError, 'Получатель уже объединён'):
            preview(self.a.pk, self.b.pk, self.version.pk)
        with self.assertRaisesMessage(ValidationError, 'уже принимает другие подразделения'):
            preview(self.b.pk, self.a.pk, self.version.pk)
