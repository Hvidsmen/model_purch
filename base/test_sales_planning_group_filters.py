from urllib.parse import urlsplit

from django.contrib.auth.models import User
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import SalesPlanningGoods, SalesPlanningGroup, SalesPlanningDirectoryLoad
from .sales_planning_group_service import rebuild_groups, group_key, save_pair


class GroupFilterTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('group-filters-admin'))
        self.url = reverse('sales_planning_groups')
        SalesPlanningDirectoryLoad.objects.create(directory='goods', loaded_at=timezone.now(), row_count=1)
        self.next_key = 0

    def good(self, group, brand='Daichi', power='07', block='Внутренний', sales='Sales', technology='INV', product='Бытовые кондиционеры'):
        self.next_key += 1
        return SalesPlanningGoods.objects.create(source_key=str(self.next_key), name=f'Model {self.next_key}',
            planning_group=group, brand=brand, power_class=power, block_type=block,
            sales_plan_group=sales, outdoor_block_type=technology, product_group=product)

    def names(self, query):
        response = self.client.get(self.url, query)
        self.assertEqual(response.status_code, 200)
        return {row['group'].name for row in response.context['rows']}

    def group(self, name):
        return SalesPlanningGroup.objects.get(source_key=group_key(name))

    def test_multiselect_or_within_filter_and_and_across_same_model(self):
        self.good('A')
        self.good('B', brand='Kentatsu', power='09', technology='On/Off', sales='Other')
        self.good('Mixed', brand='Kentatsu', power='09')
        self.good('Mixed', brand='Daichi', power='07')
        self.good('Inactive', brand='Kentatsu').delete()
        rebuild_groups()
        self.assertEqual(self.names({'brand': ['Daichi', 'Kentatsu']}), {'A', 'B', 'Mixed'})
        self.assertEqual(self.names({'brand': ['Kentatsu'], 'power_class': ['07']}), set())
        self.assertEqual(self.names({'brand': ['Kentatsu'], 'power_class': ['09'],
            'outdoor_block_type': ['On/Off'], 'sales_plan_group': ['Other'],
            'product_group': ['Бытовые кондиционеры']}), {'B'})
        self.assertEqual(self.names({'brand': 'Missing'}), set())
        self.assertEqual(self.names({}), {'A', 'B', 'Mixed'})
        response = self.client.get(self.url, {'brand': ['Daichi', 'Kentatsu']})
        self.assertContains(response, 'name="brand" value="Daichi" checked')
        self.assertContains(response, 'name="brand" value="Kentatsu" checked')
        self.assertContains(response, 'Сбросить фильтры')

    def test_status_checks_every_power_and_invalid_pairs_after_source_changes(self):
        ready_in = self.good('Ready')
        ready_out = self.good('Ready', block='Наружный')
        partial_in = self.good('Partial')
        partial_out = self.good('Partial', block='Наружный')
        self.good('Partial', power='09')
        review_in = self.good('Review')
        review_out = self.good('Review', block='Наружный')
        self.good('Bad data', power='', block='')
        rebuild_groups()
        for name, indoor, outdoor in [('Ready', ready_in, ready_out), ('Partial', partial_in, partial_out), ('Review', review_in, review_out)]:
            save_pair(self.group(name), '07', indoor.pk, outdoor.pk)
        review_out.is_active = False
        review_out.save()
        self.assertEqual(self.names({'pair_status': ['ready']}), {'Ready'})
        self.assertEqual(self.names({'pair_status': ['pending'], 'power_class': ['07']}), {'Partial'})
        self.assertEqual(self.names({'pair_status': ['review']}), {'Review', 'Bad data'})
        self.assertEqual(self.names({'pair_status': ['ready', 'pending']}), {'Ready', 'Partial'})
        response = self.client.get(self.url, {'pair_status': 'invented'})
        self.assertEqual(response.status_code, 400)

    def test_pagination_keeps_repeated_values_search_and_inactive_flag(self):
        for number in range(27):
            self.good(f'Group {number:02}', brand='A' if number % 2 else 'B')
        rebuild_groups()
        response = self.client.get(self.url, {'brand': ['A', 'B'], 'q': 'Group', 'inactive': '1', 'pair_status': ['pending']})
        self.assertEqual(response.context['page'].paginator.count, 27)
        query = QueryDict(urlsplit(response.context['next_url']).query)
        self.assertEqual(query.getlist('brand'), ['A', 'B'])
        self.assertEqual(query.getlist('pair_status'), ['pending'])
        self.assertEqual(query['q'], 'Group')
        self.assertEqual(query['inactive'], '1')
        self.assertEqual(query['page'], '2')
        second = self.client.get(self.url + response.context['next_url'])
        self.assertEqual(len(second.context['rows']), 2)

    def test_inactive_groups_and_missing_group_issues_follow_filters(self):
        live = self.good('Live', brand='A')
        old = self.good('Old', brand='B')
        self.good('', brand='A')
        self.good('', brand='B')
        rebuild_groups()
        old.is_active = False
        old.save()
        rebuild_groups()
        self.assertEqual(self.names({}), {'Live'})
        self.assertEqual(self.names({'inactive': '1'}), {'Live', 'Old'})
        self.assertEqual(self.names({'inactive': '1', 'brand': ['B']}), set())
        response = self.client.get(self.url, {'brand': ['A']})
        self.assertEqual(response.context['ungrouped_count'], 1)
        self.assertEqual(response.context['rows'][0]['pair_status'], 'Не настроены')
