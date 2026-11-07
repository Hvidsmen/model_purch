from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from .models import SalesPlanningGoods, SalesPlanningDirectoryLoad
from .sales_planning_source import load_goods, GOODS_SQL


def product(key=b'product', name='Split', parent='Товарная группа', planning='Планирование', brand='Daichi', sales='Планы продаж'):
    return (key, name, parent, planning, brand, sales, '2.5', 'Внутренний', 'INV')


class GoodsImportTests(TestCase):
    def import_rows(self, rows):
        connection, cursor = Mock(), Mock()
        cursor.fetchall.return_value = rows
        with patch('base.sales_planning_source.connect_database', return_value=(connection, cursor)) as connect:
            result = load_goods()
        connect.assert_called_once_with('vm-dwh', 'DataWH')
        cursor.execute.assert_called_once_with(GOODS_SQL)
        cursor.close.assert_called_once()
        connection.close.assert_called_once()
        return result

    def test_all_fields_and_binary_hierarchy_keys_survive_repeat_import(self):
        self.assertEqual(self.import_rows([product(), product(b'parent', 'Группа', None)]), 2)
        good = SalesPlanningGoods.objects.get(source_key='0x' + b'product'.hex())
        pk = good.pk
        self.assertEqual(good.product_group, 'Товарная группа')
        self.assertEqual((good.planning_group, good.sales_plan_group, good.brand, good.power_class, good.block_type, good.outdoor_block_type),
                         ('Планирование', 'Планы продаж', 'Daichi', '2.5', 'Внутренний', 'INV'))
        self.import_rows([product(memoryview(b'product'), 'Новое название')])
        good.refresh_from_db()
        self.assertEqual(good.pk, pk)
        self.assertEqual(good.name, 'Новое название')
        self.assertFalse(SalesPlanningGoods.objects.get(source_key='0x' + b'parent'.hex()).is_active)
        self.assertEqual(SalesPlanningDirectoryLoad.objects.get(directory='goods').row_count, 1)

    def test_empty_bad_and_ambiguous_sources_keep_successful_snapshot(self):
        self.import_rows([product()])
        timestamp = SalesPlanningDirectoryLoad.objects.get(directory='goods').loaded_at
        for rows in ([], [product(key=None)], [product(name=' ')], [product(), product(name='Conflict')]):
            with self.assertRaises(ValidationError):
                self.import_rows(rows)
        self.assertEqual(SalesPlanningDirectoryLoad.objects.get(directory='goods').loaded_at, timestamp)
        self.assertTrue(SalesPlanningGoods.objects.get().is_active)
        self.assertEqual(self.import_rows([product(), product()]), 1)

    def test_atomic_refresh_rolls_back_if_metadata_write_fails(self):
        self.import_rows([product()])
        with patch('base.sales_planning_source.SalesPlanningDirectoryLoad.objects.update_or_create', side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):
                self.import_rows([product(b'new')])
        self.assertEqual(SalesPlanningGoods.objects.count(), 1)
        self.assertTrue(SalesPlanningGoods.objects.get().is_active)

    def test_page_loads_only_on_admin_post_and_renders_separate_groups_and_parent(self):
        url = reverse('sales_planning_directories')
        admin = User.objects.create_superuser('goods-admin')
        self.client.force_login(admin)
        with patch('base.sales_planning.load_goods', side_effect=lambda: self.import_rows([product(name='<b>Split</b>'), product(b'parent', 'Родительская группа', None)])) as load:
            response = self.client.post(url, {'action': 'load_goods'}, follow=True)
        load.assert_called_once()
        for text in ('Номенклатура загружена: 2', 'Планирование', 'Планы продаж', 'Товарная группа', '&lt;b&gt;Split&lt;/b&gt;', 'INV', 'Бренд', 'Мощность', 'INV / On-Off'):
            self.assertContains(response, text)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(admin)
        self.assertEqual(secure.post(url, {'action': 'load_goods'}).status_code, 403)
        reader = User.objects.create_user('goods-reader')
        self.client.force_login(reader)
        with patch('base.sales_planning.load_goods') as load:
            self.assertEqual(self.client.post(url, {'action': 'load_goods'}).status_code, 403)
            load.assert_not_called()

