from django.test import TestCase
from django.urls import reverse
from .models import ScenarioModel, KindPurch, PGGoods


class GoodsEditorTests(TestCase):
    def setUp(self):
        self.scenario = ScenarioModel.objects.create(name='Plan', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        kind = KindPurch.objects.create(name='Purchased')
        self.good = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='Group', planning_sales='Sales',
            group_goods='Goods', kind_purch=kind, brand='Brand', purch='Supplier', volume=.01, exw_usd=0,
            ddp_usd=117.326646, kddp=1, stock_cnt_day=1, percent_stock_end=0)
        self.url = reverse('edit_pggoods', args=[self.good.pk]) + f'?scenario={self.scenario.pk}'
        self.data = {'scenario': self.scenario.pk, 'volume': '.01', 'exw_usd': '0', 'ddp_usd': '117.326646',
                     'kddp': '', 'stock_cnt_day': '1', 'percent_stock_end': '0'}

    def test_numeric_edit_accepts_zero_exw_and_preserves_unsubmitted_metadata(self):
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, reverse('pggoods_list') + f'?scenario={self.scenario.pk}')
        self.good.refresh_from_db()
        self.assertEqual((self.good.brand, self.good.purch, self.good.kddp), ('Brand', 'Supplier', 1))
        self.assertAlmostEqual(self.good.ddp_usd, 117.326646)

    def test_brand_and_purchase_are_displayed_and_can_be_changed_or_cleared(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'name="brand"')
        self.assertContains(page, 'name="purch"')
        self.assertContains(page, 'Плановая группа')
        for brand, purchase in [('New brand', 'New supplier'), ('', '')]:
            self.client.post(self.url, dict(self.data, brand=brand, purch=purchase))
            self.good.refresh_from_db()
            self.assertEqual(self.good.brand or '', brand)
            self.assertEqual(self.good.purch or '', purchase)

    def test_coefficient_is_calculated_server_side(self):
        self.client.post(self.url, dict(self.data, exw_usd='10', ddp_usd='15', kddp='999'))
        self.good.refresh_from_db()
        self.assertEqual(self.good.kddp, 1.5)

    def test_invalid_volume_explains_error_and_preserves_database_values(self):
        response = self.client.post(self.url, dict(self.data, volume='0'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Изменения не сохранены')
        self.assertContains(response, 'Объём должен быть больше нуля')
        self.assertContains(response, 'href="#id_volume"')
        self.good.refresh_from_db()
        self.assertEqual(self.good.volume, .01)
