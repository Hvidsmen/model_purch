from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User, Group
from base.testing import authorize_test_case
from base.access import MOTIVATION_VIEWER
from .models import MotivationSubdivisionExclusion
from .services.subdivision_scope import exclusion_scope, included


class ExclusionDirectoryPageTests(TestCase):
    def setUp(self):
        self.user=authorize_test_case(self)
        self.url=reverse('motivation_exclusion_directory')

    def data(self):
        rows=list(MotivationSubdivisionExclusion.objects.all())
        data={'exclusions-TOTAL_FORMS':str(len(rows)+1),'exclusions-INITIAL_FORMS':str(len(rows)),
              'exclusions-MIN_NUM_FORMS':'0','exclusions-MAX_NUM_FORMS':'500'}
        for i,row in enumerate(rows):
            data.update({f'exclusions-{i}-id':str(row.pk),f'exclusions-{i}-subdivision_name':row.subdivision_name,
                         f'exclusions-{i}-comment':row.comment})
            if row.enabled:data[f'exclusions-{i}-enabled']='on'
        data[f'exclusions-{len(rows)}-enabled']='on'
        return rows,data

    def test_menu_and_page_show_the_shared_reference(self):
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Справочник исключений')
        self.assertContains(response,'Справочник директоров')
        self.assertContains(response,'E-Com')
        self.assertContains(response,'Сохранить исключения')
        self.assertContains(response,'канал ДПП')
        self.assertContains(response,'HEAT')
        self.assertContains(response,'href="'+self.url+'"')
        self.assertEqual(len(response.context['formset'].initial_forms),9)

    def test_editor_adds_a_name_and_can_disable_existing_exclusion(self):
        rows,data=self.data()
        data.pop('exclusions-0-enabled',None)
        data[f'exclusions-{len(rows)}-subdivision_name']=' Новый филиал '
        data[f'exclusions-{len(rows)}-comment']='Общее исключение'
        self.assertRedirects(self.client.post(self.url,data),self.url)
        self.assertTrue(MotivationSubdivisionExclusion.objects.get(subdivision_name='Новый филиал').enabled)
        rows[0].refresh_from_db();self.assertFalse(rows[0].enabled)
        with exclusion_scope():
            self.assertFalse(included('Новый филиал'))
            self.assertTrue(included(rows[0].subdivision_name))

    def test_duplicate_name_is_rejected_without_partial_save(self):
        rows,data=self.data()
        data[f'exclusions-{len(rows)}-subdivision_name']=' retail '
        data['exclusions-0-comment']='Must not be saved'
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Такое имя уже есть')
        rows[0].refresh_from_db();self.assertNotEqual(rows[0].comment,'Must not be saved')
        self.assertEqual(MotivationSubdivisionExclusion.objects.count(),9)

    def test_report_only_user_cannot_change_the_dictionary(self):
        user=User.objects.create_user('exclusion-viewer')
        user.groups.add(Group.objects.get(name=MOTIVATION_VIEWER));self.client.force_login(user)
        _,data=self.data()
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,403)
        self.assertEqual(MotivationSubdivisionExclusion.objects.filter(enabled=True).count(),9)

    def test_anonymous_user_is_redirected_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)
