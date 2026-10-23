from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from . import test_shared_versions
from .models import DirectorEmployee, SubdivisionDirectorCoeff, GlobalCoeffVersion
from .services.save_verification import SaveVerification


class VerifiedSaveTests(TestCase):
    setUp = test_shared_versions.SharedMotivationVersionTests.setUp

    def payload(self, **changes):
        self.first.refresh_from_db()
        return {'version': self.first.pk, 'revision': self.first.revision, 'action_button': 'save_coeff',
                f'select_var_calc={self.good.pk}': self.variation.pk,
                f'original_var_calc={self.good.pk}': self.variation.pk,
                f'global_coeff={self.global_coeff.pk}': '20%', **changes}

    def test_success_is_read_back_and_noop_is_distinguished(self):
        url = reverse('gb_act')
        response = self.client.post(url, self.payload(), follow=True)
        self.assertContains(response, 'Сохранение проверено. Изменено полей: 1.')
        self.global_coeff.refresh_from_db()
        self.assertEqual(self.global_coeff.motivation_coeff, .2)
        response = self.client.post(url, self.payload(), follow=True)
        self.assertContains(response, 'новых изменений нет')

    def test_stale_tab_cannot_overwrite_newer_save(self):
        data = self.payload()
        self.global_coeff.motivation_coeff = .3
        self.global_coeff.save()
        response = self.client.post(reverse('gb_act'), data, follow=True)
        self.assertContains(response, 'Версия изменилась')
        self.global_coeff.refresh_from_db()
        self.assertEqual(self.global_coeff.motivation_coeff, .3)

    def test_readback_failure_rolls_back_all_cells_and_revision(self):
        data = self.payload()
        revision = self.first.revision
        with patch.object(SaveVerification, 'equal', side_effect=[False, False]):
            response = self.client.post(reverse('gb_act'), data, follow=True)
        self.assertContains(response, 'Все изменения этой операции отменены')
        self.global_coeff.refresh_from_db()
        self.first.refresh_from_db()
        self.assertEqual(self.global_coeff.motivation_coeff, .1)
        self.assertEqual(self.first.revision, revision)

    def test_unknown_id_and_empty_post_cannot_report_success(self):
        response = self.client.post(reverse('gb_act'), self.payload(**{'global_coeff=999999': '40%'}), follow=True)
        self.assertContains(response, 'Коэффициент не найден')
        self.global_coeff.refresh_from_db()
        self.assertEqual(self.global_coeff.motivation_coeff, .1)
        response = self.client.post(reverse('gb_act'), {'version':self.first.pk,'action_button':'save_coeff'}, follow=True)
        self.assertContains(response, 'Нет коэффициентов для сохранения')

    def test_archived_director_is_not_selectable_and_stale_post_rejected(self):
        archived = DirectorEmployee.objects.create(full_name='Archived director')
        archived.subdivisions.add(self.sub)
        row = SubdivisionDirectorCoeff.objects.create(version=self.first, subdivision=self.sub,employee=archived,goods=self.good,k0='.12')
        self.client.post(reverse('motivation_director_directory'), {'action':'delete','employee':archived.pk,'confirm':'yes'})
        url = reverse('coeff_subdivisions_admin_motivation', args=[self.sub.pk])
        page = self.client.get(url)
        self.assertNotIn(archived, list(page.context['director_employees']))
        self.assertIsNone(page.context['employee'])
        page = self.client.get(url, {'employee':archived.pk})
        self.assertContains(page, 'Директор удалён или недоступен')
        response = self.client.post(reverse('sub_act',args=[self.sub.pk]), {
            'version':self.first.pk,'action_button':'save_coeff','employee':archived.pk,
            f'director_coeff={self.good.pk}=0':'50%',f'sub_coeff={self.local_coeff.pk}':'50%'},follow=True)
        self.assertContains(response,'Директор удалён. Изменения не сохранены')
        row.refresh_from_db();self.local_coeff.refresh_from_db()
        self.assertEqual(str(row.k0),'0.120000')
        self.assertEqual(self.local_coeff.motivation_coeff,.15)

    def test_manager_coefficients_are_verified_and_scope_validated(self):
        url = reverse('sub_act',args=[self.sub.pk])
        self.first.refresh_from_db()
        response=self.client.post(url,{'version':self.first.pk,'revision':self.first.revision,'action_button':'update_motive_coeff',f'kind_coeff={self.manager_kind.pk}':'90%'},follow=True)
        self.assertContains(response,'Сохранение проверено. Изменено полей: 1.')
        self.manager.refresh_from_db();self.assertEqual(self.manager.coeff,.9)
        response=self.client.post(url,{'version':self.first.pk,'action_button':'update_motive_coeff','kind_coeff=99999':'80%'},follow=True)
        self.assertContains(response,'Период поправочного коэффициента не найден')
