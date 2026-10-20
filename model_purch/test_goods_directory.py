from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from base.testing import authorize_test_case
from .models import Freight, GoodsGroup, KindPurch, PGGoods, Purch, ScenarioModel
from .services.goods_directory import refresh_goods_directory


class GoodsDirectoryTests(TestCase):
    def setUp(self):
        self.user = authorize_test_case(self)
        self.scenario = ScenarioModel.objects.create(name='Plan', date_start_plan='2026-01-01', date_end_plan='2026-12-31')
        self.other = ScenarioModel.objects.create(name='Other', date_start_plan='2027-01-01', date_end_plan='2027-12-31')
        self.good = PGGoods.objects.create(scenario_plan=self.scenario, planning_group='Group', planning_sales='Sales',
            group_goods='Goods', brand='Brand', purch='Supplier', kind_purch=KindPurch.objects.create(name='Purchased'),
            volume=1, container_volume=70, duty_rate=7, exw_usd=10, stock_cnt_day=30, percent_stock_end=20)
        self.connection, self.cursor = Mock(), Mock()
        self.connector = Mock(return_value=(self.connection, self.cursor))
        self.cursor.fetchall.return_value = [(' GROUP ', 'New sales', 'New goods', 'New brand', 'New supplier')]
        self.url = reverse('refresh_goods_directory', args=[self.scenario.pk])

    def refresh(self):
        return refresh_goods_directory(self.scenario, connector=self.connector)

    def test_only_four_fields_update_and_only_in_selected_scenario(self):
        other_good = PGGoods.objects.get(pk=self.good.pk)
        other_good.pk = None
        other_good.scenario_plan = self.other
        other_good.save()
        before = PGGoods.objects.values().get(pk=self.good.pk)
        self.assertEqual(self.refresh(), {'updated': 1, 'unchanged': 0, 'missing': 0})
        after = PGGoods.objects.values().get(pk=self.good.pk)
        self.assertEqual({key for key in before if before[key] != after[key]}, {'planning_sales', 'group_goods', 'brand', 'purch'})
        self.assertEqual((after['planning_sales'], after['group_goods'], after['brand'], after['purch']),
                         ('New sales', 'New goods', 'New brand', 'New supplier'))
        other_good.refresh_from_db()
        self.assertEqual(other_good.brand, 'Brand')
        self.assertTrue(GoodsGroup.objects.filter(name='New goods').exists())
        self.assertEqual(PGGoods.objects.count(), 2)
        self.connection.close.assert_called_once()
        self.connector.assert_called_once_with('vm-dwh', 'ModelPurch')
        self.assertEqual(self.refresh(), {'updated': 0, 'unchanged': 1, 'missing': 0})

    def test_unmatched_goods_preserved_new_source_goods_not_created(self):
        self.cursor.fetchall.return_value = [('Missing', 'Sales', 'Goods', 'Brand', 'Supplier')]
        before = PGGoods.objects.values().get(pk=self.good.pk)
        self.assertEqual(self.refresh(), {'updated': 0, 'unchanged': 0, 'missing': 1})
        self.assertEqual(PGGoods.objects.values().get(pk=self.good.pk), before)
        self.assertEqual(PGGoods.objects.count(), 1)

    def test_duplicate_identical_source_rows_accepted_and_blanks_clear_fields(self):
        self.cursor.fetchall.return_value = [('group', None, None, None, None), ('GROUP', '', '', '', '')]
        self.assertEqual(self.refresh()['updated'], 1)
        self.good.refresh_from_db()
        self.assertEqual((self.good.planning_sales, self.good.group_goods, self.good.brand, self.good.purch), ('', '', None, None))

    def test_conflicts_and_oversized_values_cancel_entire_update(self):
        for bad in [('group', 'Conflicting', 'New goods', 'New brand', 'New supplier'),
                    ('Group', 'x' * 256, '', '', '')]:
            self.cursor.fetchall.return_value = [('Group', 'New sales', 'New goods', 'New brand', 'New supplier'), bad]
            with self.assertRaises(ValidationError):
                self.refresh()
            self.good.refresh_from_db()
            self.assertEqual(self.good.planning_sales, 'Sales')
            self.assertFalse(GoodsGroup.objects.filter(name='New goods').exists())
        self.assertEqual(self.connection.close.call_count, 2)

    def test_connection_failure_closes_connection_and_preserves_goods(self):
        self.cursor.execute.side_effect = RuntimeError('read failed')
        with self.assertRaises(RuntimeError):
            self.refresh()
        self.connection.close.assert_called_once()
        self.good.refresh_from_db()
        self.assertEqual(self.good.brand, 'Brand')

    def test_supplier_change_updates_derived_price_without_overwriting_inputs(self):
        Freight.objects.create(scenario=self.scenario, price_per_container=700)
        self.good.save()
        self.assertGreater(self.good.ddp_usd, self.good.exw_usd)
        Purch.objects.create(name='New supplier', lag_income=30, supply_type='russian')
        self.refresh()
        self.good.refresh_from_db()
        self.assertEqual(self.good.ddp_usd, self.good.exw_usd)
        self.assertEqual((self.good.volume, self.good.container_volume, self.good.duty_rate, self.good.exw_usd), (1, 70, 7, 10))

    def test_price_failure_rolls_back_metadata_and_new_groups(self):
        with patch('model_purch.services.goods_directory.reprice_goods', side_effect=ValidationError('Bad price')):
            with self.assertRaises(ValidationError):
                self.refresh()
        self.good.refresh_from_db()
        self.assertEqual(self.good.purch, 'Supplier')
        self.assertFalse(GoodsGroup.objects.filter(name='New goods').exists())

    def test_empty_scenario_skips_connection(self):
        self.assertEqual(refresh_goods_directory(self.other, connector=self.connector), {'updated': 0, 'unchanged': 0, 'missing': 0})
        self.connector.assert_not_called()

    def test_post_action_shows_counts_and_toolbar_button(self):
        with patch('model_purch.services.goods_directory.connect_database', self.connector):
            response = self.client.post(self.url)
        self.assertRedirects(response, reverse('pggoods_list') + f'?scenario={self.scenario.pk}', fetch_redirect_response=False)
        page = self.client.get(response.url)
        self.assertContains(page, 'Обновить справочник товаров')
        self.assertContains(page, 'Изменено: 1; без изменений: 0; не найдено в источнике: 0.')
        self.assertContains(page, self.url)

    def test_failure_message_and_post_only_archived_and_csrf_guards(self):
        with patch('model_purch.views_goods_directory.refresh_goods_directory') as refresh:
            self.assertEqual(self.client.get(self.url).status_code, 405)
            csrf_client = Client(enforce_csrf_checks=True)
            csrf_client.force_login(self.user)
            self.assertEqual(csrf_client.post(self.url).status_code, 403)
            self.other.archived_at = '2026-10-20T12:00:00Z'
            self.other.save(update_fields=['archived_at'])
            self.assertEqual(self.client.post(reverse('refresh_goods_directory', args=[self.other.pk])).status_code, 404)
            refresh.assert_not_called()
            refresh.side_effect = RuntimeError('SECRET connection details')
            with self.assertLogs('model_purch.views_goods_directory', level='ERROR'):
                response = self.client.post(self.url, follow=True)
            self.assertContains(response, 'Изменения не сохранены')
            self.assertNotContains(response, 'SECRET')
