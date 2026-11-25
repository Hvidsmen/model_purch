from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from . import test_distribution_stages as fixtures
from .models import Subdivision,PeriodMotivationCoefficient
from .services.closing_coefficient import confirm
from .services.closing_bulk import workspace
from django.test import RequestFactory

class ClosingBulkTests(TestCase):
    setUp=fixtures.ClosingWorkflowTests.setUp
    create=fixtures.ClosingWorkflowTests.create

    def setUp(self):
        fixtures.ClosingWorkflowTests.setUp(self)
        self.other=Subdivision.all_objects.create(subdivision_key='Closing Other',subdivision_name='Other',subdivision_global='Other',chanel=self.branch.chanel)
        self.merged=Subdivision.all_objects.create(subdivision_key='Thermotrade',subdivision_name='Термотрейд',subdivision_global='TT',chanel=self.branch.chanel,merged_into=self.branch)
        self.client.force_login(self.ceo)
        self.url=reverse('motivation_distributions')

    def grid(self):
        request=RequestFactory().get(self.url,{'year':2025,'period':4});request.user=self.ceo
        return workspace(request,True)['closing_grid']

    def post_data(self):
        grid=self.grid()
        data={'action':'confirm_coefficients','year':2025,'period':4,'token':grid.initial['token']}
        data.update({row['field'].name:'1.25' for row in grid.rows if not row['locked']})
        return data

    def test_period_shows_navigation_branches_and_confirms_together(self):
        with patch('admin_motivation.premium_distribution_views.latest_calculation',return_value=None):
            response=self.client.get(self.url,{'year':2025,'period':4})
        self.assertContains(response,'Other')
        self.assertContains(response,'Зафиксировать множители всех подразделений')
        self.assertContains(response,'value="1"')
        self.assertEqual(self.client.post(self.url,self.post_data()).status_code,302)
        for branch in (self.branch,self.other):
            row=PeriodMotivationCoefficient.objects.get(subdivision=branch,year=2025,period=4)
            self.assertEqual(str(row.coefficient),'1.2500');self.assertEqual(row.confirmed_by,self.ceo)

    def test_bad_value_rolls_back_whole_table_and_preserves_input(self):
        data=self.post_data();data[f'coefficient_{self.other.pk}']='-1'
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,200)
        self.assertFalse(PeriodMotivationCoefficient.objects.exists())
        self.assertContains(response,'value="1.25"')

    def test_locked_branch_is_skipped_and_its_coefficient_cannot_change(self):
        batch=self.create();data=self.post_data();data[f'coefficient_{self.branch.pk}']='99'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertEqual(PeriodMotivationCoefficient.objects.get(subdivision=self.branch).coefficient,1)
        self.assertEqual(PeriodMotivationCoefficient.objects.get(subdivision=self.other).coefficient,1.25)
        batch.refresh_from_db();self.assertEqual(batch.source['workflow']['coefficient'],'1.0000')

    def test_stale_coefficient_or_new_fund_prevents_partial_confirmation(self):
        data=self.post_data();confirm(self.ceo,self.other,2025,4,'1.5')
        response=self.client.post(self.url,data)
        self.assertContains(response,'Загрузите таблицу заново')
        self.assertFalse(PeriodMotivationCoefficient.objects.filter(subdivision=self.branch).exists())
        data=self.post_data();self.create()
        response=self.client.post(self.url,data)
        self.assertContains(response,'Загрузите таблицу заново')
        self.assertEqual(PeriodMotivationCoefficient.objects.get(subdivision=self.other).coefficient,1.5)

    def test_signature_actor_period_and_missing_branch_are_checked(self):
        data=self.post_data();data['token']+='broken'
        self.assertContains(self.client.post(self.url,data),'Таблица устарела')
        data=self.post_data();data['period']=3
        self.assertContains(self.client.post(self.url,data),'Период изменился')
        data=self.post_data();del data[f'coefficient_{self.other.pk}']
        self.assertEqual(self.client.post(self.url,data).status_code,200)
        self.assertFalse(PeriodMotivationCoefficient.objects.exists())
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.url,self.post_data()).status_code,403)

    def test_financier_cannot_confirm(self):
        data=self.post_data();self.client.force_login(self.finance)
        self.assertEqual(self.client.post(self.url,data).status_code,403)

    def test_list_matches_sidebar_source_and_excludes_merged_branches(self):
        from .templatetags.motivation_navigation import motivation_subdivisions
        grid=self.grid()
        self.assertEqual([r['branch'].pk for r in grid.rows],list(motivation_subdivisions().values_list('pk',flat=True)))
        self.assertNotIn(self.merged.pk,[r['branch'].pk for r in grid.rows])
        with patch('admin_motivation.premium_distribution_views.latest_calculation',return_value=None):
            response=self.client.get(self.url,{'year':2025,'period':4})
        self.assertNotContains(response,'Термотрейд')
        self.assertNotContains(response,'pd-physical-branches')
        data=self.post_data();data[f'coefficient_{self.merged.pk}']='9'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertFalse(PeriodMotivationCoefficient.objects.filter(subdivision=self.merged).exists())
