from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
import importlib.util
import os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from .models import SalesPlanningChannel, SalesPlanningGoods


class SeparateDirectoryTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('directory-admin')
        self.client.force_login(self.admin)
        self.goods = reverse('sales_planning_goods')
        self.channels = reverse('sales_planning_channels')
        SalesPlanningChannel.objects.create(name='Dealer')
        SalesPlanningChannel.objects.create(name='Old retail', is_active=False)
        self.model = SalesPlanningGoods.objects.create(source_key='model-a', name='AKAI Inverter',
            product_group='Бытовые кондиционеры', planning_group='AKAI', sales_plan_group='RAC',
            brand='AKAI', power_class='07', block_type='Наружный', outdoor_block_type='INV', manufacturer='Factory A')
        SalesPlanningGoods.objects.create(source_key='model-b', name='Kent', product_group='Бытовые кондиционеры',
            planning_group='Kent', brand='Kent', manufacturer='Factory B', is_active=False)
        SalesPlanningGoods.objects.create(source_key='foreign', name='Not household', product_group='Вентиляция')

    def test_separate_pages_never_load_dwh_on_get_and_have_only_own_update_button(self):
        with patch('base.sales_planning.load_goods') as goods, patch('base.sales_planning.load_channels') as channels:
            hub = self.client.get(reverse('sales_planning_directories'))
            self.assertNotContains(hub, '<table')
            self.assertContains(hub, self.channels)
            self.assertContains(hub, self.goods)
            page = self.client.get(self.channels)
            self.assertContains(page, 'value="load_channels"')
            self.assertNotContains(page, 'value="load_goods"')
            page = self.client.get(self.goods)
            self.assertContains(page, 'value="load_goods"')
            self.assertNotContains(page, 'value="load_channels"')
            self.assertIn('no-store', page['Cache-Control'])
            goods.assert_not_called()
            channels.assert_not_called()

    def test_goods_filters_are_combined_and_preserve_manufacturer_scope(self):
        query = {'q': 'Inverter', 'planning_group': 'AKAI', 'sales_plan_group': 'RAC', 'brand': 'AKAI',
                 'power_class': '07', 'block_type': 'Наружный', 'outdoor_block_type': 'INV', 'manufacturer': 'Factory A'}
        page = self.client.get(self.goods, query)
        self.assertEqual([row.pk for row in page.context['page']], [self.model.pk])
        self.assertContains(page, 'Factory A')
        query['manufacturer'] = 'Factory B'
        self.assertEqual(self.client.get(self.goods, query).context['page'].paginator.count, 0)
        self.assertEqual(self.client.get(self.goods, {'status': 'all'}).context['page'].paginator.count, 2)
        self.assertEqual(self.client.get(self.goods, {'status': 'inactive'}).context['page'][0].name, 'Kent')

    def test_channel_search_status_and_pagination_keep_query(self):
        page = self.client.get(self.channels, {'status': 'inactive', 'q': 'retail'})
        self.assertEqual([row.name for row in page.context['page']], ['Old retail'])
        SalesPlanningChannel.objects.bulk_create([SalesPlanningChannel(name=f'Dealer {i:02}') for i in range(55)])
        page = self.client.get(self.channels, {'status': 'active', 'q': 'Dealer'})
        self.assertEqual(len(page.context['page']), 50)
        query = parse_qs(urlsplit(page.context['next_url']).query)
        self.assertEqual(query, {'status': ['active'], 'q': ['Dealer'], 'page': ['2']})
        self.assertEqual(len(self.client.get(self.channels + page.context['next_url']).context['page']), 6)

    def test_refresh_isolated_and_preserves_filters(self):
        with patch('base.sales_planning.load_goods', return_value=1) as goods, patch('base.sales_planning.load_channels') as channels:
            response = self.client.post(self.goods + '?brand=AKAI&page=3', {'action': 'load_goods'})
            self.assertEqual(response['Location'], self.goods + '?brand=AKAI')
            self.assertEqual(self.client.post(self.channels, {'action': 'load_goods'}).status_code, 400)
            goods.assert_called_once()
            channels.assert_not_called()

    def test_csrf_and_non_admin_cannot_refresh_either_directory(self):
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin)
        for url, action in [(self.goods, 'load_goods'), (self.channels, 'load_channels')]:
            self.assertEqual(csrf.post(url, {'action': action}).status_code, 403)
        self.client.force_login(User.objects.create_user('directory-reader'))
        for url in (self.goods, self.channels):
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, {'action': 'load_goods'}).status_code, 403)

    def test_invalid_filters_and_hostile_text_are_safe(self):
        self.assertEqual(self.client.get(self.goods, {'status': 'unknown'}).status_code, 400)
        self.assertEqual(self.client.get(self.channels, {'q': 'a' * 201}).status_code, 400)
        page = self.client.get(self.goods, {'q': '<script>alert(1)</script>'})
        self.assertContains(page, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(page, '<script>alert(1)</script>')


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Requires Chromium')
class SeparateDirectoryBrowserTests(StaticLiveServerTestCase):
    setUp = SeparateDirectoryTests.setUp

    def test_open_directories_search_and_switch_activity(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1550, 'height': 1100})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('sales_planning_directories'))
                page.get_by_role('link', name='Открыть номенклатуру →', exact=True).click()
                expect(page.get_by_role('heading', name='Номенклатура', exact=True)).to_be_visible()
                expect(page.get_by_role('button', name='Обновить номенклатуру из DataWH', exact=True)).to_be_visible()
                expect(page.get_by_role('button', name='Обновить каналы продаж из DataWH', exact=True)).to_have_count(0)
                page.get_by_label('Поиск', exact=True).fill('Factory A')
                page.get_by_role('button', name='Применить', exact=True).click()
                expect(page.locator('tbody tr')).to_have_count(1)
                expect(page.locator('tbody tr')).to_contain_text('AKAI Inverter')
                page.get_by_label('Поиск', exact=True).fill('Kent')
                page.get_by_label('Активность', exact=True).select_option('inactive')
                page.get_by_role('button', name='Применить', exact=True).click()
                expect(page.locator('tbody tr')).to_contain_text('Kent')
                page.get_by_role('link', name='Каналы продаж', exact=True).click()
                expect(page.get_by_role('heading', name='Каналы продаж', exact=True)).to_be_visible()
                expect(page.locator('.split-channel-item h2')).to_have_text('Dealer')
                page.get_by_label('Активность', exact=True).select_option('inactive')
                page.get_by_role('button', name='Применить', exact=True).click()
                expect(page.locator('.split-channel-item h2')).to_have_text('Old retail')
            finally:
                context.close()
                browser.close()
