from unittest.mock import Mock, patch
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import SalesPlanningGoods, SalesPlanningGroup, SalesPlanningDirectoryLoad
from .sales_planning_source import load_goods, GOODS_SQL
from .sales_planning_group_service import rebuild_groups, group_key, group_goods, save_pair, pair_error
from .sales_planning_scope import PRODUCT_GROUP


class SplitScopeTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('scope-admin'))
        SalesPlanningDirectoryLoad.objects.create(directory='goods', row_count=999, loaded_at=timezone.now())
        self.indoor = self.good('Domestic in', 'Mixed', PRODUCT_GROUP, 'Внутренний')
        self.outdoor = self.good('Domestic out', 'Mixed', PRODUCT_GROUP, 'Наружный')
        self.foreign = self.good('Commercial only', 'Mixed', 'Полупромышленные', 'Наружный', '99')
        self.other = self.good('Ventilation only', 'Foreign', 'Вентиляция', 'Наружный')
        self.old = SalesPlanningGroup.objects.create(source_key=group_key('Foreign'), name='Foreign', source_names=['Foreign'], notes='Keep')
        rebuild_groups()

    def good(self, name, group, product, block, power='07'):
        return SalesPlanningGoods.objects.create(source_key=name, name=name, planning_group=group,
            product_group=product, block_type=block, power_class=power, brand=product)

    def test_previously_imported_foreign_data_hidden_in_every_directory_without_deletion(self):
        directories = self.client.get(reverse('sales_planning_directories'))
        self.assertEqual(directories.context['goods_count'], 2)
        self.assertNotContains(directories, 'Commercial only')
        self.assertNotContains(directories, 'Ventilation only')
        groups = self.client.get(reverse('sales_planning_groups'), {'inactive': '1'})
        self.assertEqual([row['group'].name for row in groups.context['rows']], ['Mixed'])
        self.assertEqual(groups.context['rows'][0]['count'], 2)
        self.assertEqual(groups.context['rows'][0]['power_class'], ['07'])
        filters = {field['name']: field for field in groups.context['filters']}
        self.assertEqual([option['value'] for option in filters['product_group']['options']], [PRODUCT_GROUP])
        self.assertEqual(self.client.get(reverse('sales_planning_group_detail', args=[self.old.pk])).status_code, 404)
        self.old.refresh_from_db()
        self.assertEqual(self.old.notes, 'Keep')
        self.assertTrue(SalesPlanningGoods.objects.filter(pk=self.other.pk).exists())

    def test_foreign_blocks_cannot_be_selected_and_hidden_links_are_preserved(self):
        group = SalesPlanningGroup.objects.get(source_key=group_key('Mixed'))
        self.assertEqual(group_goods(group).count(), 2)
        with self.assertRaises(ValidationError):
            save_pair(group, '07', self.indoor.pk, self.foreign.pk)
        pair = save_pair(group, '07', self.indoor.pk, self.outdoor.pk)
        self.outdoor.product_group = 'Вентиляция'
        self.outdoor.save()
        pair.refresh_from_db()
        self.assertTrue(pair_error(pair))
        group.related_groups.add(self.old)
        url = reverse('sales_planning_group_detail', args=[group.pk])
        self.client.post(url, {'action': 'settings', 'notes': 'New note'})
        self.assertTrue(group.related_groups.filter(pk=self.old.pk).exists())
        self.assertFalse(self.client.get(url).context['form'].fields['related_groups'].queryset.filter(pk=self.old.pk).exists())

    def test_source_query_and_import_enforce_scope_but_empty_response_keeps_snapshot(self):
        self.assertIn("WHERE LTRIM(RTRIM(Родитель)) = N'Бытовые кондиционеры'", GOODS_SQL)
        connection, cursor = Mock(), Mock()
        domestic = (b'd', 'Imported', PRODUCT_GROUP, 'New', 'Brand', 'Sales', '07', 'Внутренний', 'INV')
        foreign = (None, '', 'Вентиляция', '', '', '', '', '', '')
        cursor.fetchall.return_value = [domestic, foreign]
        with patch('base.sales_planning_source.connect_database', return_value=(connection, cursor)):
            self.assertEqual(load_goods(), 1)
        self.assertFalse(SalesPlanningGoods.objects.get(pk=self.other.pk).is_active)
        self.assertEqual(SalesPlanningDirectoryLoad.objects.get(directory='goods').row_count, 1)
        cursor.fetchall.return_value = [foreign]
        with patch('base.sales_planning_source.connect_database', return_value=(connection, cursor)):
            with self.assertRaises(ValidationError):
                load_goods()
        self.assertTrue(SalesPlanningGoods.objects.get(name='Imported').is_active)
