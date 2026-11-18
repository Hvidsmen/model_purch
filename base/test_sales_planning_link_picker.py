import importlib.util
import os
from unittest import skipUnless
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import SalesPlanningGoods, SalesPlanningGroup, SalesPlanningDirectoryLoad
from .sales_planning_group_service import group_key, rebuild_groups


def setup_picker(test):
    test.actor = User.objects.create_superuser('group-picker-admin')
    test.client.force_login(test.actor)
    SalesPlanningDirectoryLoad.objects.create(directory='goods', loaded_at=timezone.now(), row_count=5)
    for index, (name, brand) in enumerate([('Current', 'Brand A'), ('A related <test>', 'Brand A'),
                                           ('B related', 'Brand A'), ('C other', 'Brand B')]):
        SalesPlanningGoods.objects.create(source_key=str(index), name='Model ' + str(index), planning_group=name,
            product_group='Бытовые кондиционеры', brand=brand, sales_plan_group='RAC', outdoor_block_type='INV',
            manufacturer='Factory ' + brand[-1], power_class='07', block_type='Наружный')
    rebuild_groups()
    test.group = SalesPlanningGroup.objects.get(source_key=group_key('Current'))
    test.a = SalesPlanningGroup.objects.get(source_key=group_key('A related <test>'))
    test.b = SalesPlanningGroup.objects.get(source_key=group_key('B related'))
    test.c = SalesPlanningGroup.objects.get(source_key=group_key('C other'))
    SalesPlanningGoods.objects.create(source_key='old', name='Old', planning_group='Old saved',
                                     product_group='Бытовые кондиционеры', is_active=False)
    test.old = SalesPlanningGroup.objects.create(source_key=group_key('Old saved'), name='Old saved', source_names=['Old saved'], is_active=False)
    test.group.related_groups.add(test.old)
    test.url = reverse('sales_planning_group_detail', args=[test.group.pk])


class GroupLinkPickerTests(TestCase):
    def setUp(self):
        setup_picker(self)

    def test_picker_attributes_respect_allowed_groups_and_preserve_inactive_selection(self):
        response = self.client.get(self.url)
        metadata = response.context['link_metadata']
        self.assertNotIn(str(self.group.pk), metadata)
        self.assertEqual(metadata[str(self.a.pk)]['brand'], ['Brand A'])
        self.assertEqual(metadata[str(self.a.pk)]['manufacturer'], ['Factory A'])
        self.assertFalse(metadata[str(self.old.pk)]['active'])
        self.assertContains(response, 'A related &lt;test&gt;')
        self.assertContains(response, 'data-link-add="related_groups"')
        self.assertEqual(response.context['link_sections'][0]['count'], 1)

    def test_multi_group_settings_save_notes_and_both_link_types_without_losing_old_links(self):
        response = self.client.post(self.url, {'action': 'settings', 'notes': 'Compact note',
            'related_groups': [self.a.pk, self.b.pk, self.old.pk], 'replacement_groups': [self.c.pk]})
        self.assertEqual(response.status_code, 302)
        self.group.refresh_from_db()
        self.assertEqual(self.group.notes, 'Compact note')
        self.assertEqual(set(self.group.related_groups.values_list('pk', flat=True)), {self.a.pk, self.b.pk, self.old.pk})
        self.assertEqual(set(self.group.replacement_groups.values_list('pk', flat=True)), {self.c.pk})
        self.client.post(self.url, {'action': 'settings', 'notes': 'Invalid', 'related_groups': [self.group.pk]})
        self.group.refresh_from_db()
        self.assertEqual(self.group.notes, 'Compact note')
        self.assertTrue(self.group.related_groups.filter(pk=self.old.pk).exists())


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Requires Chromium')
class GroupLinkPickerBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        setup_picker(self)

    def test_filter_multiselect_cancel_remove_and_save(self):
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1450, 'height': 1000})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + self.url)
                related = page.locator('[data-link-section="related_groups"]')
                replacement = page.locator('[data-link-section="replacement_groups"]')
                expect(related.locator('[data-link-enhanced]')).not_to_be_visible()
                related.locator('summary').click()
                related.get_by_role('button', name='Добавить', exact=True).click()
                dialog = page.get_by_role('dialog')
                dialog.get_by_label('Марка', exact=True).select_option('Brand A')
                expect(dialog.get_by_role('checkbox')).to_have_count(2)
                dialog.get_by_role('button', name='Выбрать найденные', exact=True).click()
                expect(dialog.locator('[data-link-picker-count]')).to_contain_text('Выбрано: 3')
                dialog.get_by_role('button', name='Добавить выбранные', exact=True).click()
                expect(related.locator('[data-link-selected] li')).to_have_count(3)
                replacement.locator('summary').click()
                replacement.get_by_role('button', name='Добавить', exact=True).click()
                dialog.get_by_label('Поиск групп', exact=True).fill('C other')
                dialog.get_by_role('checkbox').check()
                dialog.get_by_role('button', name='Отмена', exact=True).click()
                expect(replacement.locator('[data-link-selected] li')).to_have_count(0)
                replacement.get_by_role('button', name='Добавить', exact=True).click()
                dialog.get_by_label('Производитель', exact=True).select_option('Factory B')
                dialog.get_by_role('checkbox').check()
                dialog.get_by_role('button', name='Добавить выбранные', exact=True).click()
                related.get_by_role('button', name='Убрать B related', exact=True).click()
                expect(related.locator('[data-link-selected] li')).to_have_count(2)
                page.locator('details').filter(has=page.locator('textarea[name="notes"]')).locator('summary').click()
                page.get_by_label('Примечание Split', exact=True).fill('Compact note')
                page.get_by_role('button', name='Сохранить настройки', exact=True).click()
                expect(page.get_by_text('Настройки Split сохранены.', exact=True)).to_be_visible()
                related.locator('summary').click()
                expect(related.locator('[data-link-selected] li')).to_have_count(2)
                expect(related.locator('[data-link-selected]')).to_contain_text('Old saved')
                expect(page.locator('textarea[name="notes"]')).to_have_value('Compact note')
            finally:
                context.close()
                browser.close()
        self.assertEqual(set(self.group.related_groups.values_list('pk', flat=True)), {self.a.pk, self.old.pk})
        self.assertEqual(set(self.group.replacement_groups.values_list('pk', flat=True)), {self.c.pk})
