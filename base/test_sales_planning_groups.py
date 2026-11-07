from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import SalesPlanningGoods, SalesPlanningGroup, SalesPlanningDirectoryLoad
from .sales_planning_group_service import rebuild_groups, group_goods, group_key, group_summaries, save_pair, pair_error, ungrouped_goods
from .sales_planning_source import load_goods


class GroupDirectoryTests(TestCase):
    def setUp(self):
        SalesPlanningDirectoryLoad.objects.create(directory='goods', loaded_at=timezone.now(), row_count=4)
        self.indoor = self.good('in', ' KUMO ', 'Внутренний', '07')
        self.outdoor = self.good('out', 'kumo', 'Наружный', '07')
        self.other = self.good('other', 'Другая', 'Наружный', '09')
        self.good('missing', '  ', '', '')

    def good(self, key, group, block, power):
        return SalesPlanningGoods.objects.create(source_key=key, name=key, planning_group=group,
            block_type=block, power_class=power, brand='Daichi', product_group='Split', outdoor_block_type='INV')

    def group(self):
        return SalesPlanningGroup.objects.get(source_key=group_key('KUMO'))

    def test_normalized_groups_keep_all_models_and_empty_names_are_issues(self):
        self.assertEqual(rebuild_groups(), 2)
        group = self.group()
        self.assertEqual(set(group_goods(group).values_list('pk', flat=True)), {self.indoor.pk, self.outdoor.pk})
        stat = group_summaries([group])[0]
        self.assertEqual(stat['count'], 2)
        self.assertEqual(stat['power_class'], ['07'])
        self.assertEqual(stat['block_type'], ['Внутренний', 'Наружный'])
        self.assertEqual(ungrouped_goods().count(), 1)
        self.assertEqual(rebuild_groups(), 2)
        self.assertEqual(self.group().pk, group.pk)

    def test_refresh_keeps_manual_settings_and_flags_obsolete_pairs(self):
        rebuild_groups()
        group = self.group()
        group.notes = 'Ручная настройка'
        group.save(update_fields=['notes'])
        other = SalesPlanningGroup.objects.get(source_key=group_key('Другая'))
        group.related_groups.add(other)
        group.replacement_groups.add(other)
        pair = save_pair(group, '07', self.indoor.pk, self.outdoor.pk)
        self.outdoor.is_active = False
        self.outdoor.save()
        rebuild_groups()
        group.refresh_from_db()
        pair.refresh_from_db()
        self.assertEqual(group.notes, 'Ручная настройка')
        self.assertTrue(group.related_groups.filter(pk=other.pk).exists())
        self.assertTrue(group.replacement_groups.filter(pk=other.pk).exists())
        self.assertEqual(pair.outdoor_id, self.outdoor.pk)
        self.assertTrue(pair_error(pair))
        self.indoor.is_active = False
        self.indoor.save()
        rebuild_groups()
        group.refresh_from_db()
        self.assertFalse(group.is_active)
        self.assertTrue(group.block_pairs.exists())
        self.indoor.is_active = True
        self.indoor.save()
        rebuild_groups()
        self.assertEqual(self.group().pk, group.pk)

    def test_pairs_reject_foreign_group_wrong_power_wrong_block_and_inactive_models(self):
        rebuild_groups()
        group = self.group()
        for indoor, outdoor, power in [(self.indoor, self.other, '07'), (self.outdoor, self.indoor, '07'),
                                       (self.indoor, self.outdoor, '09')]:
            with self.assertRaises(ValidationError):
                save_pair(group, power, indoor.pk, outdoor.pk)
        self.outdoor.is_active = False
        self.outdoor.save()
        with self.assertRaises(ValidationError):
            save_pair(group, '07', self.indoor.pk, self.outdoor.pk)
        self.assertFalse(group.block_pairs.exists())

    def test_goods_import_rebuilds_groups_atomically(self):
        connection, cursor = Mock(), Mock()
        cursor.fetchall.return_value = [(b'in', 'Модель', 'Split', 'Новая', 'Brand', 'Sales', '12', 'Внутренний', 'INV')]
        with patch('base.sales_planning_source.connect_database', return_value=(connection, cursor)):
            load_goods()
        group = SalesPlanningGroup.objects.get(source_key=group_key('Новая'))
        self.assertEqual(group_goods(group).count(), 1)
        self.assertEqual(SalesPlanningDirectoryLoad.objects.get(directory='groups').row_count, 1)
        with patch('base.sales_planning_source.connect_database', return_value=(connection, cursor)), \
             patch('base.sales_planning_group_service.rebuild_groups', side_effect=RuntimeError('fail')):
            cursor.fetchall.return_value = [(b'bad', 'Bad', '', 'Bad', '', '', '', '', '')]
            with self.assertRaises(RuntimeError):
                load_goods()
        self.assertEqual(group_goods(group).count(), 1)
        self.assertFalse(SalesPlanningGoods.objects.filter(name='Bad').exists())

    def test_admin_pages_and_pair_settings_round_trip_without_datawh_calls(self):
        rebuild_groups()
        self.client.force_login(User.objects.create_superuser('group-admin'))
        url = reverse('sales_planning_groups')
        with patch('base.sales_planning_source.connect_database', side_effect=AssertionError('No DWH')):
            response = self.client.get(url)
            self.assertContains(response, 'Не указана группа планирования: 1')
            self.assertContains(response, 'KUMO')
            self.assertRedirects(self.client.post(url, {'action': 'rebuild'}), url)
        detail = reverse('sales_planning_group_detail', args=[self.group().pk])
        response = self.client.get(detail)
        self.assertContains(response, 'Мощность 07')
        self.assertContains(response, 'value="' + str(self.indoor.pk) + '"')
        response = self.client.post(detail, {'action': 'pair', 'power': '07', 'indoor': self.indoor.pk, 'outdoor': self.outdoor.pk}, follow=True)
        self.assertContains(response, 'Пара блоков сохранена')
        pair = self.group().block_pairs.get()
        other = SalesPlanningGroup.objects.get(source_key=group_key('Другая'))
        self.client.post(detail, {'action': 'settings', 'notes': '<b>Keep</b>', 'replacement_groups': [other.pk]})
        self.assertEqual(self.group().notes, '<b>Keep</b>')
        self.assertContains(self.client.get(detail), '&lt;b&gt;Keep&lt;/b&gt;')
        self.client.post(detail, {'action': 'remove_pair', 'pair': pair.pk})
        self.assertFalse(self.group().block_pairs.exists())

    def test_admin_access_csrf_and_settings_validation(self):
        rebuild_groups()
        url = reverse('sales_planning_groups')
        detail = reverse('sales_planning_group_detail', args=[self.group().pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        reader = User.objects.create_user('group-reader')
        self.client.force_login(reader)
        for route in (url, detail):
            self.assertEqual(self.client.get(route).status_code, 403)
            self.assertEqual(self.client.post(route, {'action': 'rebuild'}).status_code, 403)
        admin = User.objects.create_superuser('secure-group-admin')
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(admin)
        self.assertEqual(secure.post(url, {'action': 'rebuild'}).status_code, 403)
        self.client.force_login(admin)
        response = self.client.post(detail, {'action': 'settings', 'related_groups': [self.group().pk]})
        self.assertTrue(response.context['form'].errors)
        self.assertFalse(self.group().related_groups.exists())
