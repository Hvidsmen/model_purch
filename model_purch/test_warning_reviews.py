from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_test_case
from .models import PGGoods, ScenarioModel, KindPurch, GoodsWarningReview
from .services.warning_reviews import warning_details


class WarningReviewTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.scenario = ScenarioModel.objects.create(name='Plan', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.good = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='Group', kind_purch=KindPurch.objects.create(name='Buy'),
            volume=1, exw_usd=10, ddp_usd=10, plan_ddp_usd=100, kddp=1, stock_cnt_day=0, percent_stock_end=0)
        self.url = reverse('review_goods_warning', args=[self.good.pk])
        self.token = next(item['token'] for item in warning_details(self.good, 10, {}) if item['kind'] == 'ddp')
        self.data = {'scenario': self.scenario.pk, 'kind': 'ddp', 'token': self.token, 'action': 'review'}

    def test_review_filter_and_reset(self):
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        review = GoodsWarningReview.objects.get()
        self.assertIsNotNone(review.reviewed_by)
        page = self.client.get(reverse('pggoods_list'), {'scenario': self.scenario.pk, 'review_status': 'reviewed'})
        self.assertEqual(page.context['page_obj'].paginator.count, 1)
        self.assertContains(page, '✓ Проверено')
        self.assertTrue(next(item for item in page.context['page_obj'][0].warning_details if item['kind'] == 'ddp')['reviewed'])
        self.client.post(self.url, dict(self.data, action='reset'))
        self.assertFalse(GoodsWarningReview.objects.exists())
        page = self.client.get(reverse('pggoods_list'), {'scenario': self.scenario.pk, 'review_status': 'reviewed'})
        self.assertEqual(page.context['page_obj'].paginator.count, 0)

    def test_changed_values_invalidate_review_and_stale_requests(self):
        self.client.post(self.url, self.data)
        self.good.plan_ddp_usd = 101
        self.good.save()
        self.client.post(self.url, self.data)
        page = self.client.get(reverse('pggoods_list'), {'scenario': self.scenario.pk, 'review_status': 'pending', 'plan_warning': 'ddp'})
        self.assertEqual(page.context['page_obj'].paginator.count, 1)
        self.assertFalse(next(item for item in page.context['page_obj'][0].warning_details if item['kind'] == 'ddp')['reviewed'])
        self.assertEqual(GoodsWarningReview.objects.get().fingerprint, self.token)

    def test_scope_and_post_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url, dict(self.data, scenario=99999)).status_code, 404)
        self.assertFalse(GoodsWarningReview.objects.exists())

    def test_analyst_cannot_mark_warning_reviewed(self):
        from django.contrib.auth.models import User
        from base.access import ANALYST, ensure_roles
        from base.models import UserProjectAccess
        ensure_roles()
        user = User.objects.create_user('review-reader')
        UserProjectAccess.objects.create(user=user, project='purchases', role=ANALYST)
        self.client.force_login(user)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        self.assertFalse(GoodsWarningReview.objects.exists())

    def test_threshold_change_requires_new_review(self):
        self.client.post(self.url, self.data)
        page = self.client.get(reverse('pggoods_list'), {'scenario': self.scenario.pk, 'ddp_threshold': '20', 'review_status': 'reviewed'})
        self.assertEqual(page.context['page_obj'].paginator.count, 0)
