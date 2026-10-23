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

    def test_switching_projects_preserves_shell_geometry(self):
        from playwright.sync_api import sync_playwright
        ensure_roles()
        actor = User.objects.create_user('shell-admin', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                page = browser.new_page()
                page.goto(self.live_server_url + '/accounts/login/')
                page.fill('#id_username', actor.username)
                page.fill('#id_password', 'browser-test-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                for width in (1280, 2560):
                    page.set_viewport_size({'width': width, 'height': 900})
                    measurements = []
                    for path in ('/model_purch/scenarios/', '/admin_motivationglobal_coeff'):
                        response = page.goto(self.live_server_url + path)
                        self.assertEqual(response.status, 200)
                        page.evaluate('window.scrollTo(0,0)')
                        measurements.append(page.evaluate("""() => Object.fromEntries(
                            ['.portal-topbar','.portal-brand','.portal-projects','.portal-sidebar','.portal-main'].map(selector => {
                                const r=document.querySelector(selector).getBoundingClientRect();
                                return [selector, {x:r.x,y:r.y,width:r.width,height:r.height}];
                            }))"""))
                    for selector in measurements[0]:
                        dimensions = ('x', 'y', 'width', 'height') if selector != '.portal-main' and selector != '.portal-sidebar' else ('x', 'y', 'width')
                        for dimension in dimensions:
                            self.assertAlmostEqual(measurements[0][selector][dimension], measurements[1][selector][dimension], delta=1, msg=f'{width}: {selector} {dimension}')
                    self.assertLess(measurements[1]['.portal-sidebar']['x'], 20)
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
                page.goto(self.live_server_url + '/accounts/preview/')
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

    def test_user_catalog_role_group_filters_work_together(self):
        from playwright.sync_api import sync_playwright, expect
        ensure_roles()
        actor = User.objects.create_user('filter-admin', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        region = Group.objects.create(name='Региональная команда')
        target = User.objects.create_user('filter-director')
        target.groups.add(Group.objects.get(name=DIRECTOR), region)
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                page = browser.new_page(viewport={'width': 1200, 'height': 950})
                page.goto(self.live_server_url + '/accounts/login/')
                page.fill('#id_username', actor.username)
                page.fill('#id_password', 'browser-test-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                page.goto(self.live_server_url + '/accounts/users/')
                page.locator('.user-filter-form [data-access-multi]').nth(0).locator('summary').click()
                page.locator('.user-filter-form').get_by_label(DIRECTOR, exact=True).check()
                page.locator('.user-filter-form [data-access-multi]').nth(0).locator('[data-choice-close]').click()
                page.locator('.user-filter-form [data-access-multi]').nth(1).locator('summary').click()
                page.locator('.user-filter-form').get_by_label(region.name, exact=True).check()
                page.locator('.user-filter-form [data-access-multi]').nth(1).locator('[data-choice-close]').click()
                page.locator('.user-filter-form').get_by_role('button', name='Найти', exact=True).click()
                expect(page.locator('.access-group-card')).to_have_count(1)
                expect(page.locator('.access-group-card')).to_contain_text(target.username)
                page.screenshot(path='/tmp/portal-user-filters.png', full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
                page.get_by_role('link', name='Сбросить фильтры', exact=True).click()
                expect(page.locator('.access-group-card')).to_have_count(2)
            finally:
                browser.close()

    def test_ad_search_waits_for_background_result_and_fills_selected_user(self):
        from playwright.sync_api import sync_playwright, expect
        ensure_roles()
        actor = User.objects.create_user('ad-poll-admin', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        from .directory_search import DirectoryPending
        from unittest.mock import patch
        sequence = [DirectoryPending('Загружаем каталог AD…'), DirectoryPending('Загружаем каталог AD…'), [{'username': 'daichi\\burukin', 'full_name': 'Бурукин Иван'}]]
        with patch('base.user_views.search_accounts', side_effect=sequence) as search:
            with sync_playwright() as driver:
                browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
                try:
                    page = browser.new_page()
                    page.goto(self.live_server_url + '/accounts/login/')
                    page.fill('#id_username', actor.username)
                    page.fill('#id_password', 'browser-test-password')
                    page.get_by_role('button', name='Войти', exact=True).click()
                    page.goto(self.live_server_url + '/accounts/users/')
                    page.get_by_text('Найти в Active Directory', exact=True).click()
                    page.fill('#ad-query', 'Бурукин')
                    page.locator('#ad-find').click()
                    expect(page.locator('#ad-status')).to_contain_text('Загружаем каталог')
                    expect(page.locator('#ad-find')).to_be_disabled()
                    expect(page.locator('.user-ad-result')).to_have_count(1, timeout=10000)
                    page.locator('.user-ad-result').click()
                    expect(page.locator('#id_username')).to_have_value('daichi\\burukin')
                    expect(page.locator('#ad-find')).to_be_enabled()
                finally:
                    browser.close()
        self.assertEqual(search.call_count, 3)

    def test_ad_forest_domain_selector_sends_selected_domain_and_keeps_identity(self):
        from playwright.sync_api import sync_playwright, expect
        from unittest.mock import patch
        ensure_roles()
        actor = User.objects.create_user('forest-admin', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        with patch('base.windows_domains.domain_map', return_value={'daichi': 'daichi.local', 'tt': 'tt.daichi.local'}), patch('base.user_views.search_accounts', return_value=[{'username': 'tt\\krasnoperov', 'full_name': 'Красноперов Иван'}]) as search:
            with sync_playwright() as driver:
                browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
                try:
                    page = browser.new_page()
                    page.goto(self.live_server_url + '/accounts/login/')
                    page.fill('#id_username', actor.username)
                    page.fill('#id_password', 'browser-test-password')
                    page.get_by_role('button', name='Войти', exact=True).click()
                    page.goto(self.live_server_url + '/accounts/users/')
                    page.get_by_text('Найти в Active Directory', exact=True).click()
                    expect(page.locator('#ad-domain option')).to_have_count(2)
                    page.select_option('#ad-domain', 'TT')
                    page.fill('#ad-query', 'Красноперов')
                    page.locator('#ad-find').click()
                    page.locator('.user-ad-result').click()
                    expect(page.locator('#id_username')).to_have_value('tt\\krasnoperov')
                    expect(page.locator('#id_last_name')).to_have_value('Красноперов')
                finally:
                    browser.close()
        search.assert_called_once_with('Красноперов', background=True, domain='TT')

    def test_politician_author_autofill_and_group_only_access_without_duplicate_fields(self):
        from admin_motivation.models import Chanel, Subdivision, TypeCoeff
        from .models import PortalReportGroup, MotivationReportAccess
        from .access import POLITICIAN, role_for
        from unittest.mock import patch
        from playwright.sync_api import sync_playwright, expect
        ensure_roles()
        actor = User.objects.create_user('auto-profile-admin', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        channel = Chanel.objects.create(chanel_name='Auto browser')
        branch = Subdivision.objects.create(subdivision_key='Термотрейд', subdivision_name='Термотрейд', subdivision_global='Термотрейд', chanel=channel)
        worker = TypeCoeff.objects.create(type_coeff_name='Политики')
        group = Group.objects.create(name='Политики Москва и Термотрейд')
        scope = PortalReportGroup.objects.create(group=group, employee_type=worker)
        scope.subdivisions.add(branch)
        with patch('base.user_views.search_accounts', return_value=[{'username': 'tt\\dgafurov', 'full_name': 'Гафуров Дамир Наилович'}]):
            with sync_playwright() as driver:
                browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
                try:
                    page = browser.new_page()
                    page.goto(self.live_server_url + '/accounts/login/')
                    page.fill('#id_username', actor.username)
                    page.fill('#id_password', 'browser-test-password')
                    page.get_by_role('button', name='Войти', exact=True).click()
                    page.goto(self.live_server_url + '/accounts/users/')
                    page.fill('#id_first_name', 'Дамир Наилович')
                    page.fill('#id_last_name', 'Гафуров')
                    page.select_option('#id_portal_role', POLITICIAN)
                    expect(page.locator('#id_report_author')).to_have_value('Гафуров Дамир Наилович')
                    page.fill('#id_report_author', 'Ручное точное ФИО')
                    page.fill('#id_first_name', 'Дамир')
                    expect(page.locator('#id_report_author')).to_have_value('Ручное точное ФИО')
                    page.fill('#id_report_author', '')
                    page.select_option('#id_portal_role', '')
                    field = page.locator('#portal-user-form [data-access-multi]')
                    field.locator('summary').click()
                    field.get_by_label(group.name, exact=True).check()
                    field.locator('[data-choice-close]').click()
                    expect(page.locator('#id_portal_role')).not_to_be_visible()
                    expect(page.locator('#id_report_subdivision')).not_to_be_visible()
                    page.get_by_text('Найти в Active Directory', exact=True).click()
                    page.fill('#ad-query', 'Гафуров')
                    page.locator('#ad-find').click()
                    page.locator('.user-ad-result').click()
                    expect(page.locator('#id_report_author')).to_have_value('Гафуров Дамир Наилович')
                    page.screenshot(path='/tmp/portal-user-auto-group.png', full_page=True)
                    page.get_by_role('button', name='Добавить пользователя', exact=True).click()
                    expect(page.locator('.access-notice')).to_contain_text('сохранён')
                finally:
                    browser.close()
        user = User.objects.get(username='tt\\dgafurov')
        self.assertEqual(role_for(user), POLITICIAN)
        self.assertFalse(user.groups.filter(name=POLITICIAN).exists())
        self.assertEqual(MotivationReportAccess.objects.get(user=user).deal_author, 'Гафуров Дамир Наилович')

    def test_compact_user_cards_group_collapse_and_expand(self):
        from playwright.sync_api import sync_playwright, expect
        ensure_roles()
        actor = User.objects.create_user('compact-groups-admin', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        team = Group.objects.create(name='Команда продаж')
        target = User.objects.create_user('compact-employee')
        target.groups.add(Group.objects.get(name=DIRECTOR), team)
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 1000})
                page.goto(self.live_server_url + '/accounts/login/')
                page.fill('#id_username', actor.username)
                page.fill('#id_password', 'browser-test-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                page.goto(self.live_server_url + '/accounts/users/')
                section = page.locator('.user-catalog-group').filter(has=page.locator('summary').filter(has_text=team.name))
                card = section.locator('.user-card')
                expect(card).to_be_visible()
                self.assertLess(card.bounding_box()['height'], 110)
                section.locator('summary').click()
                expect(card).not_to_be_visible()
                page.locator('[data-user-groups-expand]').click()
                expect(card).to_be_visible()
                page.locator('[data-user-groups-collapse]').click()
                expect(page.locator('.user-catalog-group[open]')).to_have_count(0)
                page.locator('[data-user-groups-expand]').click()
                expect(page.locator('.user-catalog-group[open]')).to_have_count(2)
                page.screenshot(path='/tmp/portal-user-grouped-compact.png', full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
            finally:
                browser.close()

    def test_admin_assigns_director_roles_and_previews_branch_reports(self):
        from admin_motivation.models import Chanel, Subdivision, TypeCoeff
        from .models import PortalReportGroup
        from .access import BRANCH_DIRECTOR, DEPUTY_DIRECTOR, role_for
        from playwright.sync_api import sync_playwright, expect
        ensure_roles()
        actor = User.objects.create_user('branch-ui-admin', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        channel = Chanel.objects.create(chanel_name='Branch UI')
        branch = Subdivision.objects.create(subdivision_key='Allowed', subdivision_name='Allowed', subdivision_global='Allowed', chanel=channel)
        worker = TypeCoeff.objects.create(type_coeff_name='Политики')
        TypeCoeff.objects.create(type_coeff_name='Директора')
        team = Group.objects.create(name='Выбранные филиалы')
        scope = PortalReportGroup.objects.create(group=team, employee_type=worker)
        scope.subdivisions.add(branch)
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                page = browser.new_page()
                page.goto(self.live_server_url + '/accounts/login/')
                page.fill('#id_username', actor.username)
                page.fill('#id_password', 'browser-test-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                for index, role in enumerate((DEPUTY_DIRECTOR, BRANCH_DIRECTOR)):
                    username = f'TT\\branch-ui-{index}'
                    page.goto(self.live_server_url + '/accounts/users/')
                    page.fill('#id_username', username)
                    field = page.locator('#portal-user-form [data-access-multi]')
                    field.locator('summary').click()
                    field.get_by_label(team.name, exact=True).check()
                    field.locator('[data-choice-close]').click()
                    page.get_by_role('button', name='Изменить роль', exact=True).click()
                    page.select_option('#id_portal_role', role)
                    expect(page.locator('#id_report_author')).not_to_be_visible()
                    expect(page.locator('#id_report_subdivision')).not_to_be_visible()
                    page.get_by_role('button', name='Добавить пользователя', exact=True).click()
                    expect(page.locator('.access-notice')).to_contain_text('сохранён')
                    card = page.locator('.user-card').filter(has_text=username.lower())
                    card.get_by_role('button', name='Посмотреть как пользователь', exact=True).click()
                    page.locator('.portal-project-card').filter(has_text='Мотивация').click()
                    expect(page.locator('.report-catalog-card')).to_have_count(5 if role == BRANCH_DIRECTOR else 3)
                    page.get_by_role('button', name='Вернуться к администратору', exact=True).click()
            finally:
                browser.close()
        for index, role in enumerate((DEPUTY_DIRECTOR, BRANCH_DIRECTOR)):
            self.assertEqual(role_for(User.objects.get(username=f'tt\\branch-ui-{index}')), role)

    def test_subdivision_merge_preview_keeps_access_and_requires_review_again_after_edit(self):
        from datetime import date
        from django.urls import reverse
        from admin_motivation.models import Chanel, Subdivision, GlobalCoeffVersion
        from playwright.sync_api import sync_playwright, expect
        ensure_roles()
        actor = User.objects.create_user('merge-ui-admin', password='browser-test-password', is_staff=True)
        actor.groups.add(Group.objects.get(name=ADMIN))
        channel = Chanel.objects.create(chanel_name='Merge browser')
        source, target = [Subdivision.objects.create(subdivision_key=name, subdivision_name=name, subdivision_global=name, chanel=channel) for name in ('ТЕРМОТРЕЙД', 'Д-Москва')]
        extra = Subdivision.objects.create(subdivision_key='Д-Волга', subdivision_name='Д-Волга', subdivision_global='Д-Волга', chanel=channel)
        version = GlobalCoeffVersion.objects.create(title='Merge test', effective_from=date(2026, 1, 1))
        merge_url = reverse('motivation_subdivision_merge')
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            try:
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                page.goto(self.live_server_url + '/accounts/login/')
                page.fill('#id_username', actor.username)
                page.fill('#id_password', 'browser-test-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                page.get_by_role('link', name='Слияние подразделений', exact=True).click()
                page.get_by_role('link', name='Термотрейд → Д-Москва', exact=True).click()
                expect(page.locator(f'input[name=source][value="{source.pk}"]')).to_be_checked()
                page.locator('#merge-source-search').fill('Волга')
                page.locator(f'input[name=source][value="{extra.pk}"]').check()
                page.locator('#merge-source-search').fill('')
                expect(page.locator('#merge-source-count')).to_have_text('2')
                expect(page.locator(f'input[name=source][value="{target.pk}"]')).to_be_disabled()
                expect(page.locator('select[name=target]')).to_have_value(str(target.pk))
                page.select_option('select[name=version]', str(version.pk))
                page.select_option('select[name=policy]', 'target')
                page.get_by_role('button', name='Проверить объединение', exact=True).click()
                expect(page.locator('#merge-review')).to_be_visible()
                page.screenshot(path='/tmp/portal-subdivision-merge.png', full_page=True)
                page.select_option('select[name=policy]', 'source')
                page.select_option('select[name=priority_source]', str(extra.pk))
                expect(page.locator('#merge-review')).not_to_be_visible()
                expect(page.locator('#merge-review-status')).to_contain_text('Параметры изменились')
                page.get_by_role('button', name='Проверить объединение', exact=True).click()
                page.get_by_label('Подтверждаю слияние по показанным параметрам').check()
                page.get_by_role('button', name='Объединить подразделения', exact=True).click()
                expect(page.locator('.merge-notice')).to_contain_text('Подразделения объединены')
                page.set_viewport_size({'width': 390, 'height': 844})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
            finally:
                browser.close()
        source.refresh_from_db()
        self.assertEqual(source.merged_into_id, target.pk)
        extra.refresh_from_db()
        self.assertEqual(extra.merged_into_id, target.pk)
