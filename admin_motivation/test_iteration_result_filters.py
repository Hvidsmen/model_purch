import importlib.util,os
from decimal import Decimal
from unittest import skipUnless
from unittest.mock import Mock,patch
from urllib.parse import urlparse,parse_qs
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_coefficient_iteration_calculation as fixtures
from .services.coefficient_iteration_runner import start,execute
from .services.coefficient_iteration_calculation import MERGED


def setup(test):
    fixtures.CalculationTests.setUp(test)
    test.run=execute(start(test.iteration.pk,test.user,test.config,test.iteration.revision),Mock())


class ResultFilterTests(TestCase):
    def setUp(self):setup(self)

    def test_filters_rebuild_totals_from_recipients_and_preserve_frozen_run(self):
        original=self.run.results
        with patch('admin_motivation.conns.connect_database') as connection:
            response=self.client.get(self.url,{'run':self.run.pk,'result_year':['2026'],'result_branch':[MERGED],'result_kind':['Политики']})
        connection.assert_not_called();self.assertEqual(response.status_code,200)
        results=response.context['presentation'];self.assertEqual(len(results),1)
        self.assertEqual(len(results[0]['recipients']),1)
        total=next(row for row in results[0]['summary'] if row['branch']=='ИТОГО')
        self.assertEqual(Decimal(str(total['total'])),Decimal(20))
        self.run.refresh_from_db();self.assertEqual(self.run.results,original)

    def test_multiple_types_years_and_currency_links_keep_all_selections(self):
        query={'run':self.run.pk,'result_year':['2026','2027'],'result_kind':['Политики','Продажи']}
        response=self.client.get(self.url,query)
        self.assertEqual(len(response.context['presentation']),2)
        self.assertTrue(all(len(result['recipients'])==2 for result in response.context['presentation']))
        params=parse_qs(urlparse(response.context['currency_urls']['RUB']).query)
        self.assertEqual(params['result_year'],query['result_year']);self.assertEqual(params['result_kind'],query['result_kind'])
        self.assertEqual(params['currency'],['RUB'])
        query['currency']='RUB';rub=self.client.get(self.url,query)
        self.assertEqual(rub.context['currency'],'RUB')

    def test_choices_use_all_run_rows_and_empty_result_is_explicit(self):
        response=self.client.get(self.url,{'run':self.run.pk,'result_branch':'missing'})
        self.assertContains(response,'По выбранным условиям нет строк результатов.')
        self.assertEqual(response.context['presentation'],[])
        kinds=next(field for field in response.context['result_filters'].fields if field['key']=='kind')
        self.assertEqual({option['value'] for option in kinds['options']},{'Политики','Продажи','Директора'})
        reset=self.client.get(self.url,{'run':self.run.pk})
        self.assertEqual(len(reset.context['presentation']),2)


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class ResultFilterBrowserTests(StaticLiveServerTestCase):
    def setUp(self):setup(self)

    def test_search_multiselect_filter_and_currency_switch(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1000})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();page.goto(self.live_server_url+self.url+'?run='+str(self.run.pk))
                form=page.get_by_role('form',name='Фильтры результатов')
                year=form.locator('[data-result-filter]').filter(has_text='Год расчёта')
                year.locator('summary').click();year.locator('input[value="2026"]').check()
                kinds=form.locator('[data-result-filter]').filter(has_text='Тип коэффициента')
                kinds.locator('summary').click();kinds.get_by_role('searchbox').fill('полит')
                kinds.get_by_role('button',name='Выбрать найденные').click()
                with page.expect_navigation():form.get_by_role('button',name='Показать',exact=True).click()
                expect(page.get_by_role('heading',name='Москва + ТТ · IV квартал 2026 · 100%')).to_be_visible()
                expect(page.get_by_role('heading',name='Все подразделения · 2027 · 100%')).to_have_count(0)
                expect(page.locator('.iteration-table').first).to_contain_text('Политики')
                expect(page.locator('.iteration-table').first).not_to_contain_text('Продажи')
                page.get_by_role('link',name='RUB',exact=True).click()
                expect(page.locator('.iteration-table').first).to_contain_text('Общая выплата, RUB')
                self.assertIn('result_kind=',page.url)
                page.get_by_role('link',name='Сбросить фильтры',exact=True).click()
                expect(page.get_by_role('heading',name='Все подразделения · 2027 · 100%')).to_be_visible()
            finally:context.close();browser.close()
