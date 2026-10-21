import importlib.util
import os
from unittest import skipUnless
from django.contrib.auth.models import User, Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .access import ADMIN, DIRECTOR, LOGISTICIAN, ensure_roles


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Optional Chromium check')
class PortalLoginBrowserTests(StaticLiveServerTestCase):
    def test_login_navigation_denied_project_and_logout_for_each_role(self):
        ensure_roles()
        for index, role in enumerate((ADMIN, DIRECTOR, LOGISTICIAN)):
            user = User.objects.create_user(f'browser-role-{index}', password='browser-test-password', is_staff=role == ADMIN)
            user.groups.add(Group.objects.get(name=role))
        from playwright.sync_api import sync_playwright
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                for index, role in enumerate((ADMIN, DIRECTOR, LOGISTICIAN)):
                    with self.subTest(role=role):
                        page = browser.new_page(viewport={'width': 390, 'height': 800})
                        page.goto(self.live_server_url + '/accounts/login/')
                        page.fill('#id_username', f'browser-role-{index}')
                        page.fill('#id_password', 'browser-test-password')
                        page.get_by_role('button', name='Войти', exact=True).click()
                        page.wait_for_url(self.live_server_url + '/')
                        self.assertEqual(page.locator('.portal-projects a').count(), 2 if role == ADMIN else 1)
                        self.assertEqual(page.locator('.portal-project-card').count(), 2 if role == ADMIN else 1)
                        if role != ADMIN:
                            denied = '/model_purch/scenarios/' if role == DIRECTOR else '/admin_motivationglobal_coeff'
                            self.assertEqual(page.goto(self.live_server_url + denied).status, 403)
                            self.assertTrue(page.get_by_role('heading', name='Нет доступа к разделу').is_visible())
                        page.get_by_role('button', name='Выйти', exact=True).click()
                        page.wait_for_url('**/accounts/login/')
                        page.goto(self.live_server_url + '/model_purch/scenarios/')
                        self.assertIn('/accounts/login/', page.url)
                        page.close()
            finally:
                browser.close()

    def test_admin_can_preview_user_and_return_without_changing_login(self):
        ensure_roles()
        admin = User.objects.create_user('browser-preview-admin', password='browser-test-password', is_staff=True)
        admin.groups.add(Group.objects.get(name=ADMIN))
        target = User.objects.create_user('browser-preview-director')
        target.groups.add(Group.objects.get(name=DIRECTOR))
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                page = browser.new_page()
                page.goto(self.live_server_url + '/accounts/login/')
                page.fill('#id_username', admin.username)
                page.fill('#id_password', 'browser-test-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                page.get_by_role('link', name='Просмотреть как пользователь', exact=True).click()
                page.locator('#id_user').select_option(str(target.pk))
                page.get_by_role('button', name='Открыть просмотр', exact=True).click()
                expect(page.get_by_role('status')).to_contain_text(target.username)
                expect(page.locator('.portal-project-card')).to_have_count(1)
                expect(page.locator('.portal-project-card')).to_contain_text('Мотивация')
                page.get_by_role('button', name='Вернуться к администратору', exact=True).click()
                expect(page.locator('.portal-project-card')).to_have_count(2)
                expect(page.locator('.portal-user')).to_contain_text(admin.username)
            finally:
                browser.close()

    def test_admin_manually_adds_and_edits_domain_user_without_password(self):
        from django.urls import reverse
        ensure_roles()
        actor = User.objects.create_user('browser-user-manager', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                page = browser.new_page()
                page.goto(self.live_server_url + reverse('portal_login'))
                page.fill('#id_username', actor.username)
                page.fill('#id_password', 'browser-test-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                page.get_by_role('link', name='Пользователи и права', exact=True).click()
                page.goto(self.live_server_url + reverse('admin:auth_user_add'))
                page.fill('#id_username', 'DAICHI\\BrowserEmployee')
                page.select_option('#id_portal_role', DIRECTOR)
                page.locator('input[name=_save]').click()
                expect(page.locator('#id_username')).to_have_value('daichi\\browseremployee')
                expect(page.locator('#id_portal_role')).to_have_value(DIRECTOR)
                page.select_option('#id_portal_role', LOGISTICIAN)
                page.locator('input[name=_save]').click()
                expect(page.locator('#result_list')).to_contain_text(LOGISTICIAN)
            finally:
                browser.close()
        user = User.objects.get(username='daichi\\browseremployee')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(list(user.groups.values_list('name', flat=True)), [LOGISTICIAN])

    def test_politician_sees_three_reports_and_locked_identity_filters(self):
        from django.urls import reverse
        from unittest.mock import patch
        from .access import POLITICIAN
        from .models import MotivationReportAccess
        from admin_motivation.models import Chanel, Subdivision
        ensure_roles()
        user = User.objects.create_user('browser-politician', password='browser-test-password')
        user.groups.add(Group.objects.get(name=POLITICIAN))
        channel = Chanel.objects.create(chanel_name='Personal test')
        branch = Subdivision.objects.create(subdivision_key='Personal', subdivision_name='Personal', subdivision_global='Personal', chanel=channel)
        MotivationReportAccess.objects.create(user=user, subdivision=branch, deal_author='Личный Автор Сделки')
        from playwright.sync_api import sync_playwright, expect
        with patch('admin_motivation.report_views.choices', return_value=[]), sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                page = browser.new_page()
                page.goto(self.live_server_url + reverse('portal_login'))
                page.fill('#id_username', user.username)
                page.fill('#id_password', 'browser-test-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                page.locator('.portal-project-card').click()
                expect(page.locator('.report-catalog-card')).to_have_count(3)
                expect(page.get_by_text('Глобальные коэффициенты', exact=True)).to_have_count(0)
                url = reverse('motivation_report', args=['manager-detail'])
                page.goto(self.live_server_url + url + '?author=Other&type=Директора&subdivision=Other')
                fields = page.locator('#native-report-filters input[readonly]')
                expect(fields).to_have_count(3)
                expect(fields.nth(0)).to_have_value('Политики')
                expect(fields.nth(1)).to_have_value('Personal')
                expect(fields.nth(2)).to_have_value('Личный Автор Сделки')
                self.assertEqual(page.goto(self.live_server_url + reverse('global_coeff_admin_motivation')).status, 403)
            finally:
                browser.close()

    def test_group_editor_search_checkboxes_and_members(self):
        from django.urls import reverse
        from admin_motivation.models import Chanel, Subdivision, TypeCoeff
        ensure_roles()
        actor = User.objects.create_user('browser-groups-admin', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        member = User.objects.create_user('member-test', first_name='Test', last_name='Employee')
        channel = Chanel.objects.create(chanel_name='Groups browser')
        branches = [Subdivision.objects.create(subdivision_key=name, subdivision_name=name, subdivision_global=name, chanel=channel) for name in ('Moscow', 'Volga')]
        worker = TypeCoeff.objects.create(type_coeff_name='Политики')
        from playwright.sync_api import sync_playwright, expect
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                page = browser.new_page(viewport={'width':1440, 'height':1100})
                page.goto(self.live_server_url + reverse('portal_login'))
                page.fill('#id_username', actor.username)
                page.fill('#id_password', 'browser-test-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                page.get_by_role('link', name='Группы пользователей', exact=True).click()
                page.fill('#id_name', 'Политики · Москва и регионы')
                page.select_option('#id_employee_type', str(worker.pk))
                branches_field = page.locator('[data-access-multi]').filter(has=page.locator('input[name=subdivisions]'))
                branches_field.locator('summary').click()
                branches_field.locator('input[type=search]').fill('Moscow')
                expect(branches_field.locator('[data-choice-label]:visible')).to_have_count(1)
                branches_field.locator('[data-choice-all]').click()
                expect(branches_field.locator('input:checked')).to_have_count(2)
                branches_field.locator('[data-choice-close]').click()
                members = page.locator('[data-access-multi]').filter(has=page.locator('input[name=members]'))
                members.locator('summary').click()
                members.locator('input[value="' + str(member.pk) + '"]').check()
                members.locator('[data-choice-close]').click()
                page.get_by_role('button', name='Создать группу', exact=True).click()
                expect(page.locator('[data-group-card]')).to_have_count(1)
                expect(page.locator('[data-group-card]')).to_contain_text('Политики · Москва и регионы')
                expect(page.locator('[data-group-card]')).to_contain_text('Moscow')
                expect(page.locator('[data-group-card]')).to_contain_text('Volga')
                page.screenshot(path='/tmp/portal-user-groups.png', full_page=True)
                page.get_by_role('link', name='Настроить →', exact=True).click()
                expect(page.locator('input[name=subdivisions]:checked')).to_have_count(2)
                expect(page.locator('input[name=members]:checked')).to_have_count(1)
                page.set_viewport_size({'width':390,'height':844})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 391)
            finally:
                browser.close()
        self.assertEqual(Group.objects.get(name='Политики · Москва и регионы').user_set.count(), 1)

    def test_group_report_filters_switch_departments_and_worker_without_losing_author(self):
        from django.urls import reverse
        from unittest.mock import patch
        from .access import POLITICIAN
        from .models import PortalReportGroup, MotivationReportAccess
        from admin_motivation.models import Chanel, Subdivision, TypeCoeff
        ensure_roles()
        user = User.objects.create_user('browser-group-scope', password='browser-test-password')
        user.groups.add(Group.objects.get(name=POLITICIAN))
        channel = Chanel.objects.create(chanel_name='Scope groups')
        branches = [Subdivision.objects.create(subdivision_key=name, subdivision_name=name, subdivision_global=name, chanel=channel) for name in ('A', 'B', 'C')]
        MotivationReportAccess.objects.create(user=user, subdivision=branches[0], deal_author='Собственный Автор Сделки')
        for name, subset in [('Политики', branches[:2]), ('Директора', branches[2:])]:
            worker = TypeCoeff.objects.create(type_coeff_name=name)
            group = Group.objects.create(name='Scope ' + name)
            access = PortalReportGroup.objects.create(group=group, employee_type=worker)
            access.subdivisions.set(subset)
            group.user_set.add(user)
        from playwright.sync_api import sync_playwright, expect
        with patch('admin_motivation.report_views.choices', return_value=[]), sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(self.live_server_url + reverse('portal_login'))
                page.fill('#id_username', user.username)
                page.fill('#id_password', 'browser-test-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                page.goto(self.live_server_url + reverse('motivation_report', args=['manager-detail']))
                divisions = page.locator('.manager-multi-filter[data-field=subdivision]')
                divisions.locator('summary').click()
                divisions.locator('input[value=B]').uncheck()
                with page.expect_navigation():
                    divisions.locator('[data-multi-apply]').click()
                expect(page.locator('input[name=author]')).to_have_value('Собственный Автор Сделки')
                self.assertEqual(page.locator('select[name=subdivision]').evaluate('(el) => [...el.selectedOptions].map(item=>item.value)'), ['A'])
                with page.expect_navigation():
                    page.locator('select[name=type]').select_option('Директора', force=True)
                expect(page.locator('input[name=subdivision]')).to_have_value('C')
                expect(page.locator('input[name=author]')).to_have_value('Собственный Автор Сделки')
                self.assertEqual(errors, [])
            finally:
                browser.close()

    def test_portal_users_local_creation_ad_selection_and_preview(self):
        from unittest.mock import patch
        from playwright.sync_api import sync_playwright, expect
        ensure_roles()
        actor = User.objects.create_user('user-ui-admin', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        with patch('base.user_views.search_accounts', return_value=[{'username': 'daichi\\employee', 'full_name': 'Иванов Иван Иванович'}]):
            with sync_playwright() as driver:
                browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                    page.goto(self.live_server_url + '/accounts/login/')
                    page.fill('#id_username', actor.username)
                    page.fill('#id_password', 'browser-test-password')
                    page.get_by_role('button', name='Войти', exact=True).click()
                    page.get_by_role('link', name='Пользователи и права', exact=True).click()
                    page.fill('#id_username', 'local-browser')
                    page.fill('#id_first_name', 'Иван')
                    page.fill('#id_last_name', 'Иванов')
                    page.fill('#id_password1', 'Browser-local-2479!')
                    page.fill('#id_password2', 'Browser-local-2479!')
                    page.select_option('#id_portal_role', DIRECTOR)
                    page.get_by_role('button', name='Добавить пользователя', exact=True).click()
                    expect(page.locator('.access-notice')).to_contain_text('сохранён')
                    page.screenshot(path='/tmp/portal-users.png', full_page=True)
                    card = page.locator('.access-group-card').filter(has_text='local-browser')
                    card.get_by_role('button', name='Посмотреть как пользователь').click()
                    expect(page.locator('[role=status]')).to_contain_text('Иванов')
                    page.get_by_role('button', name='Вернуться к администратору').click()
                    page.goto(self.live_server_url + '/accounts/users/')
                    page.get_by_text('Найти в Active Directory', exact=True).click()
                    page.fill('#ad-query', 'Иванов')
                    page.locator('#ad-find').click()
                    page.locator('.user-ad-result').click()
                    expect(page.locator('#id_username')).to_have_value('daichi\\employee')
                    expect(page.locator('#id_last_name')).to_have_value('Иванов')
                    expect(page.locator('#id_first_name')).to_have_value('Иван Иванович')
                    page.set_viewport_size({'width': 390, 'height': 844})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                finally:
                    browser.close()
        self.assertTrue(User.objects.get(username='local-browser').check_password('Browser-local-2479!'))
