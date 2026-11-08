from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import SalesPlanningGoods, SalesPlanningGroup, SalesPlanningDirectoryLoad
from .sales_planning_group_service import block_kind, group_key, rebuild_groups, power_rows, save_pair, save_unique_pairs


class BlockTypeTests(SimpleTestCase):
    def test_source_labels_have_one_shared_classification(self):
        for label in ('Внутренний', 'внутренний блок', ' Блок внутренний ', 'БлокВнутренний', 'INDOOR'):
            self.assertEqual(block_kind(label), 'indoor')
        for label in ('Наружный', 'наружный блок', 'Блок наружный', 'БлокНаружный', 'OUTDOOR'):
            self.assertEqual(block_kind(label), 'outdoor')
        for label in ('', None, 'INV', 'On/Off', 'Внутренний/наружный', 'Неизвестно', '0', '1'):
            self.assertIsNone(block_kind(label))


class AutomaticPairTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('auto-pairs-admin'))
        SalesPlanningDirectoryLoad.objects.create(directory='goods', loaded_at=timezone.now(), row_count=1)
        self.indoor = self.good('in', 'БлокВнутренний')
        self.outdoor = self.good('out', 'БлокНаружный')
        rebuild_groups()
        self.group = SalesPlanningGroup.objects.get(source_key=group_key('G'))
        self.url = reverse('sales_planning_group_detail', args=[self.group.pk])

    def good(self, key, block, power='07/20'):
        return SalesPlanningGoods.objects.create(source_key=key, name=key, planning_group='G',
            power_class=power, block_type=block, outdoor_block_type='INV')

    def test_unique_candidates_are_preselected_without_get_writes_then_saved_together(self):
        response = self.client.get(self.url)
        row = response.context['powers'][0]
        self.assertEqual(row['selected_indoor'], self.indoor.pk)
        self.assertEqual(row['selected_outdoor'], self.outdoor.pk)
        self.assertTrue(row['automatic'])
        self.assertEqual(response.context['unclassified'], 0)
        self.assertFalse(self.group.block_pairs.exists())
        response = self.client.post(self.url, {'action': 'auto_pairs'}, follow=True)
        self.assertContains(response, 'Сохранено однозначных пар: 1')
        pair = self.group.block_pairs.get()
        self.assertEqual((pair.indoor_id, pair.outdoor_id), (self.indoor.pk, self.outdoor.pk))
        self.assertEqual(save_unique_pairs(self.group), 0)
        filtered = self.client.get(reverse('sales_planning_groups'), {'pair_status': 'ready'})
        self.assertEqual(filtered.context['page'].paginator.count, 1)

    def test_multiple_missing_or_unknown_candidates_are_not_guessed(self):
        extra = self.good('extra', 'Внутренний')
        self.good('unknown', 'Неизвестно', '09')
        self.good('missing', 'Наружный', '')
        rows = power_rows(self.group, list(SalesPlanningGoods.objects.all()))
        power = next(row for row in rows if row['power'] == '07/20')
        self.assertIsNone(power['selected_indoor'])
        self.assertEqual(power['selected_outdoor'], self.outdoor.pk)
        self.assertFalse(power['automatic'])
        self.assertEqual(save_unique_pairs(self.group), 0)
        self.assertFalse(self.group.block_pairs.exists())

    def test_existing_manual_and_obsolete_choices_are_not_replaced(self):
        pair = save_pair(self.group, '07/20', self.indoor.pk, self.outdoor.pk)
        self.outdoor.is_active = False
        self.outdoor.save()
        self.good('new-out', 'Наружный')
        self.assertEqual(save_unique_pairs(self.group), 0)
        pair.refresh_from_db()
        self.assertEqual(pair.outdoor_id, self.outdoor.pk)
        response = self.client.get(self.url)
        self.assertTrue(response.context['powers'][0]['error'])
        self.assertEqual(response.context['powers'][0]['selected_outdoor'], self.outdoor.pk)
