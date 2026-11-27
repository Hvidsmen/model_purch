from django.test import TestCase,override_settings
from django.contrib.auth.models import User,Group
from django.urls import reverse
from base.access import FINANCIER,DIRECTOR,POLITICIAN,SALES,BRANCH_DIRECTOR,MODELER
from base.testing import authorize_admin


class CalculationGuideTests(TestCase):
    def setUp(self):
        authorize_admin(self.client)
        self.url=reverse('motivation_calculation_guide')

    def test_information_page_is_available_for_motivation_roles_without_personal_data(self):
        for index,role in enumerate((FINANCIER,DIRECTOR,POLITICIAN,SALES,BRANCH_DIRECTOR,MODELER)):
            user=User.objects.create_user('guide-'+str(index));user.groups.add(Group.objects.get(name=role))
            self.client.force_login(user)
            response=self.client.get(self.url)
            self.assertContains(response,'Как считается премия')
            self.assertContains(response,'они не перемножаются')
            self.assertContains(response,'Проверка ОЗС ФАО')
            self.assertEqual(self.client.post(self.url,{}).status_code in (403,405),True)

    def test_mode_description_matches_switch_and_anonymous_cannot_read(self):
        with override_settings(MOTIVATION_MAIN_ALL_QUARTERS=False):
            self.assertContains(self.client.get(self.url),'IV квартал и год')
        with override_settings(MOTIVATION_MAIN_ALL_QUARTERS=True):
            self.assertContains(self.client.get(self.url),'I–IV кварталы и год')
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code,302)
