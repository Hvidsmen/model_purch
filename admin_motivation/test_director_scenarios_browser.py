import os
from io import BytesIO
from pathlib import Path
import re
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_director_scenarios_end_to_end import ScenarioFlow,GOLDEN


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1','Optional Chromium end-to-end test')
class DirectorFullBrowserTests(ScenarioFlow,StaticLiveServerTestCase):
    def setUp(self):self.setup_flow()

    def test_form_sql_read_five_cases_reload_and_excel_download(self):
        from playwright.sync_api import sync_playwright,expect
        from django.conf import settings
        from openpyxl import load_workbook
        from .reports_director_scenarios import ComparisonForm
        from threading import Event
        from .services.director_prepared_fact import read_fact
        release=Event();self.addCleanup(release.set)
        def slow_read(*args,**kwargs):
            release.wait(10)
            return read_fact(*args,**kwargs)
        with patch('admin_motivation.services.director_scenario_inputs.connect_database',self.warehouse.connector),patch('admin_motivation.services.director_prepared_fact.read_fact',slow_read):
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True)
                page=browser.new_page(viewport={'width':1600,'height':1100})
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                page.goto(self.live_server_url+self.url)
                expect(page.get_by_role('heading',name='Сравнение директорских сценариев',exact=True)).to_be_visible()
                for year in (2025,2026):page.locator(f'select[name=plan_{year}]').select_option(str(self.plans[year].pk))
                page.locator('select[name=coefficient_version]').select_option(str(self.version.pk))
                for key,person in zip(('source_moscow','source_tt','target_moscow','target_tt'),self.people):
                    label=ComparisonForm.base_fields[key].label
                    page.get_by_role('button',name=label,exact=True).click()
                    search=page.get_by_role('searchbox',name=f'Поиск: {label}',exact=True)
                    search.fill(person.full_name)
                    option=f'{person.full_name} · № {person.pk}'+(' · архив' if person.is_archived else '')
                    page.get_by_role('button',name=option,exact=True).click()
                page.get_by_role('button',name='Рассчитать пять вариантов и сохранить',exact=True).click()
                expect(page.locator('#director-progress')).to_be_visible()
                expect(page.locator('#director-progress-stage')).to_contain_text('Чтение подготовленного факта 2026',timeout=8000)
                expect(page.get_by_role('button',name='Рассчитать пять вариантов и сохранить',exact=True)).to_be_disabled()
                expect(page.locator('#director-progress-time')).to_contain_text('Всего:')
                release.set()
                expect(page).to_have_url(re.compile(r'calculation=\d+'))
                for case in range(1,6):
                    expect(page.locator(f'#scenario-{case}')).to_be_visible()
                    expect(page.locator('table').nth(case-1).locator('tbody tr')).to_have_count(2)
                calls=len(self.warehouse.calls)
                page.reload()
                expect(page.locator('#scenario-5')).to_contain_text('факт = план')
                self.assertEqual(len(self.warehouse.calls),calls)
                with page.expect_download() as pending:
                    page.get_by_role('link',name='Скачать Excel · все пять вариантов',exact=True).click()
                raw=Path(pending.value.path()).read_bytes();book=load_workbook(BytesIO(raw))
                self.assertEqual(len(book.sheetnames),9)
                for case,values in enumerate(GOLDEN,1):
                    self.assertEqual(book[f'{case} Вариант']['O2'].value,values[0])
                    self.assertEqual(book[f'{case} Вариант']['O3'].value,values[1])
                self.assertEqual(len(self.warehouse.calls),calls)
                if output:=os.environ.get('DIRECTOR_VERIFICATION_OUTPUT'):
                    Path(output).write_bytes(raw)
                    page.screenshot(path=str(Path(output).with_suffix('.png')),full_page=True)
                browser.close()
