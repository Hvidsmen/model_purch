from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_test_case
from .models import (GlobalCoeffVersion, Goods, Chanel, Subdivision, DirectorEmployee,
                     GlobalDirectorCoeff, SubdivisionDirectorCoeff)
from .services.directors import copy_directors
from .services.versions import create_version, delete_draft
from .services.approval import review_approval, approve_review
from .director_forms import GlobalDirectorAddForm


class DirectorCoefficientTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.version = GlobalCoeffVersion.objects.get(effective_from=date(2001, 1, 1))
        channel = Chanel.objects.create(chanel_name='Channel')
        self.sub = Subdivision.objects.create(subdivision_key='A', subdivision_name='A', subdivision_global='A', chanel=channel)
        self.other_sub = Subdivision.objects.create(subdivision_key='B', subdivision_name='B', subdivision_global='B', chanel=channel)
        self.first = DirectorEmployee.objects.create(full_name='Первый директор', employee_code='D1')
        self.second = DirectorEmployee.objects.create(full_name='Второй директор', employee_code='D2')
        self.first.subdivisions.add(self.sub)
        self.second.subdivisions.add(self.sub)
        self.goods = Goods.objects.create(goods_key='Test', planning_group_sales='1. RAC', group='Group', brand='Brand')
        self.global_row = GlobalDirectorCoeff.objects.create(version=self.version, goods=self.goods,
            k0=Decimal('.01'), k1=Decimal('.02'), k2=Decimal('.03'), k3=Decimal('.04'), k4=Decimal('.05'))
        self.global_url = reverse('motivation_directors_global')
        self.local_url = reverse('motivation_directors_local')

    def local_post(self, action, employee=None, **data):
        return self.client.post(self.local_url, {'version': self.version.pk, 'subdivision': self.sub.pk,
            'employee': (employee or self.first).pk, 'action': action, **data})

    def test_percent_form_and_single_coefficient_set(self):
        form = GlobalDirectorAddForm({'goods': self.goods.pk, **{f'k{i}': '12.3456' for i in range(5)}},
            instance=GlobalDirectorCoeff(version=self.version))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['k0'], Decimal('.123456'))
        self.assertEqual(GlobalDirectorAddForm(instance=self.global_row).initial['k1'], Decimal('2'))
        bad = GlobalDirectorAddForm({'goods': self.goods.pk, **{f'k{i}': '101' for i in range(5)}},
            instance=GlobalDirectorCoeff(version=self.version))
        self.assertFalse(bad.is_valid())

    def test_global_and_local_forms_render(self):
        global_page = self.client.get(self.global_url)
        self.assertContains(global_page, 'Глобальные коэффициенты директоров')
        self.assertContains(global_page, 'coeff-0-k0')
        page = self.client.get(self.local_url, {'subdivision': self.sub.pk})
        self.assertContains(page, 'Первый директор')
        self.assertContains(page, 'Второй директор')
        self.assertNotContains(page, 'name="copy_source"')

    def test_each_employee_has_independent_coefficients(self):
        self.local_post('copy_global', confirm_copy='yes')
        self.local_post('copy_global', self.second, confirm_copy='yes')
        first = SubdivisionDirectorCoeff.objects.get(employee=self.first)
        first.k0 = Decimal('.5')
        first.save()
        self.assertEqual(SubdivisionDirectorCoeff.objects.get(employee=self.second).k0, Decimal('.01'))
        self.assertEqual(GlobalDirectorCoeff.objects.get().k0, Decimal('.01'))
        self.assertEqual(self.first.subdivisions.count(), 1)
        self.assertEqual(self.sub.directors.count(), 2)

    def test_copy_requires_confirmation_and_assigned_employee(self):
        self.local_post('copy_global')
        self.assertFalse(SubdivisionDirectorCoeff.objects.exists())
        with self.assertRaises(ValidationError):
            copy_directors(self.version.pk, subdivision=self.other_sub, employee=self.first, from_global=True)
        self.assertFalse(SubdivisionDirectorCoeff.objects.exists())

    def test_empty_source_does_not_remove_current_rows(self):
        self.local_post('copy_global', confirm_copy='yes')
        self.global_row.delete()
        with self.assertRaises(ValidationError):
            copy_directors(self.version.pk, subdivision=self.sub, employee=self.first, from_global=True)
        self.assertEqual(SubdivisionDirectorCoeff.objects.count(), 1)

    def test_new_version_clones_director_rows_and_old_version_is_frozen(self):
        self.local_post('copy_global', confirm_copy='yes')
        next_version = create_version(date(2026, 1, 1), 'Next', self.version.pk)
        self.assertEqual(GlobalDirectorCoeff.objects.get(version=next_version).k4, Decimal('.05'))
        self.assertEqual(SubdivisionDirectorCoeff.objects.get(version=next_version).employee, self.first)
        with self.assertRaises(ValidationError):
            self.global_row.k0 = Decimal('.9')
            self.global_row.save()
        with self.assertRaises(ValidationError):
            copy_directors(self.version.pk, next_version.pk)
        copied = GlobalDirectorCoeff.objects.get(version=next_version)
        copied.k0 = Decimal('.8')
        copied.save()
        copy_directors(next_version.pk, self.version.pk)
        self.assertEqual(GlobalDirectorCoeff.objects.get(version=next_version).k0, Decimal('.01'))
        copy_directors(next_version.pk, self.version.pk, self.sub, self.first)
        self.assertEqual(SubdivisionDirectorCoeff.objects.get(version=next_version).k4, Decimal('.05'))

    def test_approved_director_version_is_immutable_and_draft_deletion_retains_history(self):
        review = review_approval(self.version)
        approve_review(review['token'])
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'approved')
        with self.assertRaises(ValidationError):
            self.global_row.save()
        draft = create_version(date(2026, 1, 1), 'Next', self.version.pk)
        delete_draft(draft.pk)
        self.assertFalse(GlobalDirectorCoeff.objects.filter(version=draft).exists())
        self.assertEqual(GlobalDirectorCoeff.objects.filter(version=self.version).count(), 1)

    def test_partial_save_cannot_modify_other_employee(self):
        self.local_post('copy_global', confirm_copy='yes')
        self.local_post('copy_global', self.second, confirm_copy='yes')
        other = SubdivisionDirectorCoeff.objects.get(employee=self.second)
        response = self.local_post('save', **{'coeff-TOTAL_FORMS': '1', 'coeff-INITIAL_FORMS': '1',
            'coeff-MIN_NUM_FORMS': '0', 'coeff-MAX_NUM_FORMS': '1000', 'coeff-0-id': other.pk,
            **{f'coeff-0-k{i}': '90' for i in range(5)}})
        self.assertEqual(response.status_code, 302)
        other.refresh_from_db()
        self.assertEqual(other.k0, Decimal('.01'))

    def test_save_employee_and_multiple_branch_assignments(self):
        response = self.client.post(self.local_url, {'version': self.version.pk, 'action': 'employee_save',
            'full_name': 'Третий директор', 'employee_code': 'D3', 'subdivisions': [self.sub.pk, self.other_sub.pk]})
        self.assertEqual(response.status_code, 302)
        third = DirectorEmployee.objects.get(employee_code='D3')
        self.assertEqual(third.subdivisions.count(), 2)

    def test_new_version_returns_to_director_page(self):
        response = self.client.post(reverse('gb_act'), {'action_button': 'create_version',
            'source_version': self.version.pk, 'effective_from': '2026-01-01', 'title': 'Next',
            'return_directors': 'local', 'return_subdivision': self.sub.pk, 'return_employee': self.first.pk})
        latest = GlobalCoeffVersion.objects.latest('pk')
        self.assertRedirects(response, self.local_url + f'?subdivision={self.sub.pk}&employee={self.first.pk}&version={latest.pk}')

    def test_formset_saves_percent_values_and_preserves_other_scopes(self):
        self.local_post('copy_global', confirm_copy='yes')
        row = SubdivisionDirectorCoeff.objects.get(employee=self.first)
        values = {'coeff-TOTAL_FORMS': '1', 'coeff-INITIAL_FORMS': '1', 'coeff-MIN_NUM_FORMS': '0',
                  'coeff-MAX_NUM_FORMS': '1000', 'coeff-0-id': row.pk,
                  **{f'coeff-0-k{i}': '1.0000' for i in range(5)}}
        response = self.local_post('save', **values)
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.k4, Decimal('.01'))
        self.assertEqual(GlobalDirectorCoeff.objects.get().k4, Decimal('.05'))

    def test_logistician_cannot_open_director_forms(self):
        from base.access import LOGISTICIAN
        from django.contrib.auth.models import User, Group
        user = User.objects.create_user('director-test-logistician')
        user.groups.add(Group.objects.get(name=LOGISTICIAN))
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.global_url).status_code, 403)
        self.assertEqual(self.client.get(self.local_url).status_code, 403)

    def test_seed_matrix_adds_missing_goods_without_overwriting_coefficients(self):
        other = Goods.objects.create(goods_key='Other', planning_group_sales='2. VRF', group='Other', brand='Brand')
        for _ in range(2):
            self.client.post(self.global_url, {'version': self.version.pk, 'action': 'seed_matrix'})
        self.assertEqual(GlobalDirectorCoeff.objects.filter(version=self.version).count(), 2)
        self.assertEqual(GlobalDirectorCoeff.objects.get(goods=other).k0, 0)
        self.assertEqual(GlobalDirectorCoeff.objects.get(goods=self.goods).k0, Decimal('.01'))
