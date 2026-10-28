from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from base.models import UserProjectAccess
from base.testing import authorize_test_case
from .models import ScenarioModel
from .services.scenario_setup import KEY


class ScenarioSetupTests(TestCase):
    def setUp(self):
        self.actor = authorize_test_case(self)
        self.payload = {'name':'New scenario','date_start_plan':'2027-01-01','date_end_plan':'2027-11-30',
                        'plans-TOTAL_FORMS':'1','plans-INITIAL_FORMS':'0',
                        'plans-MIN_NUM_FORMS':'0','plans-MAX_NUM_FORMS':'1000',
                        'plans-0-name':'Plan 2027','plans-0-flag_order_in_purch':'on'}

    def create(self):
        with patch('model_purch.views.sync_purch_data'), patch('model_purch.views.sync_pggoods_data_for_scenario'):
            return self.client.post(reverse('scenario_create'),self.payload)

    def test_creation_opens_goods_with_recommendations_and_remembers_selected_scenario(self):
        response = self.create()
        scenario = ScenarioModel.objects.get(name='New scenario')
        self.assertRedirects(response,reverse('pggoods_list')+f'?scenario={scenario.pk}&setup=1#scenario-setup',fetch_redirect_response=False)
        self.assertEqual(self.client.session['model_purch_scenario_id'],scenario.pk)
        self.assertIn(scenario.pk,self.client.session[KEY])
        page = self.client.get(response.url)
        for label in ('Общие параметры закупок','Параметры товаров','Плановые и фактические показатели','Обновить план, факт и сток'):
            self.assertContains(page,label)
        self.assertNotContains(page,'data-bs-target="#copyPggoodsModal">Скопировать товары')
        self.assertContains(self.client.get(reverse('pggoods_list'),{'scenario':scenario.pk}),'id="scenario-setup"')

    def test_sync_failure_still_opens_goods_and_does_not_expose_connection_details(self):
        with patch('model_purch.views.sync_purch_data',side_effect=RuntimeError('SECRET')):
            with self.assertLogs('model_purch.views',level='ERROR'):
                response = self.client.post(reverse('scenario_create'),self.payload)
        scenario = ScenarioModel.objects.get(name='New scenario')
        self.assertIn(f'scenario={scenario.pk}',response.url)
        page = self.client.get(response.url)
        self.assertContains(page,'загрузка данных из MS SQL не завершилась')
        self.assertNotContains(page,'SECRET')
        self.assertContains(page,'id="scenario-setup"')

    def test_dismiss_is_post_only_scoped_and_recommendations_can_be_reopened(self):
        response = self.create()
        scenario = ScenarioModel.objects.get(name='New scenario')
        other = ScenarioModel.objects.create(name='Other',date_start_plan='2028-01-01',date_end_plan='2028-12-31')
        self.assertNotContains(self.client.get(reverse('pggoods_list'),{'scenario':other.pk}),'id="scenario-setup"')
        url = reverse('dismiss_scenario_setup',args=[scenario.pk])
        self.assertEqual(self.client.get(url).status_code,405)
        csrf = Client(enforce_csrf_checks=True);csrf.force_login(self.actor)
        self.assertEqual(csrf.post(url).status_code,403)
        self.client.post(url)
        self.assertNotIn(scenario.pk,self.client.session[KEY])
        self.assertNotContains(self.client.get(reverse('pggoods_list'),{'scenario':scenario.pk}),'id="scenario-setup"')
        page = self.client.get(reverse('pggoods_list'),{'scenario':scenario.pk,'setup':'1'})
        self.assertContains(page,'id="scenario-setup"')
        self.assertContains(page,'data-bs-target="#copyPggoodsModal">Скопировать товары')
        self.assertIn(scenario.pk,self.client.session[KEY])
        analyst = User.objects.create_user('setup-analyst')
        UserProjectAccess.objects.create(user=analyst,project='purchases',role='Аналитик')
        self.client.force_login(analyst)
        self.assertEqual(self.client.post(url).status_code,403)
        self.assertNotContains(self.client.get(reverse('pggoods_list'),{'scenario':scenario.pk,'setup':'1'}),'id="scenario-setup"')
