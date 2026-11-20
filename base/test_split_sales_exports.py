from copy import deepcopy
from decimal import Decimal
from io import BytesIO
import importlib.util
import os
from unittest import skipUnless

from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from openpyxl import load_workbook

from . import test_split_power_grid as fixtures
from .split_workflow import approve
from .split_engine import evaluate


def setup(test):
    fixtures.PowerGridTests.setUp(test)
    group=test.scenario.state['groups'][test.gid]
    group['ref']['details']={'sales_plan_group':'RAC','brand':'Saved brand','outdoor_type':'Inverter','factory':'Saved factory'}
    for line in group['channels'].values():
        # Keep zero months explicit and nonuniform distributions for reconciliation.
        line['months']={month:('100' if month.endswith('-02') else '0') for month in line['months']}
    test.scenario.save(update_fields=['state'])


class SalesExportTests(TestCase):
    setUp=setup

    def approve(self):
        approve(self.scenario,self.actor,self.scenario.revision,False)
        self.scenario.refresh_from_db()

    def export(self,variant,**query):
        response=self.client.get(self.url,{'export':'plan_'+variant,**query})
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response['Content-Disposition'])
        book=load_workbook(BytesIO(b''.join(response.streaming_content)))
        response.close()
        self.addCleanup(book.close)
        return book

    def records(self,book,title):
        values=list(book[title].values);headers=values[0]
        return headers,[dict(zip(headers,row)) for row in values[1:] if row[0] not in ('Итого · компл.','Итого · шт. блоков','Итого · USD','Внутренние · шт.','Наружные · шт.')]

    def test_three_variants_conserve_monthly_quantity_cost_and_revenue(self):
        self.approve();expected=evaluate(self.scenario)
        books={variant:self.export(variant) for variant in ('groups','powers','models')}
        for variant,book in books.items():
            self.assertEqual(book.sheetnames,['Сценарий','Количество','Выручка USD','DDP USD'])
            headers,records=self.records(book,'Количество')
            self.assertEqual(len(records),2 if variant=='groups' else 4 if variant=='powers' else 8)
            self.assertEqual('Мощность' in headers,variant!='groups')
            self.assertEqual('Модель' in headers,variant=='models')
            self.assertTrue(all(row['2027-01']==0 and row['2027-03']==0 for row in records))
            self.assertTrue(all(row['2027-02']==row['Итого за период'] for row in records))
            self.assertEqual(sum(row['Итого за период'] for row in records),60 if variant=='models' else 30)
            for title,key in [('Выручка USD','revenue'),('DDP USD','cost')]:
                _,money=self.records(book,title)
                self.assertAlmostEqual(sum(Decimal(str(row['Итого за период'])) for row in money),expected['totals'][key],places=6)
            self.assertIsNotNone(book['Количество'].freeze_panes)
            self.assertIsNotNone(book['Количество'].auto_filter.ref)
        _,models=self.records(books['models'],'Количество')
        for kind in ('Внутренний','Наружный'):
            self.assertEqual(sum(row['Итого за период'] for row in models if row['Тип блока']==kind),30)

    def test_approved_snapshot_reference_fields_and_formula_text_are_preserved(self):
        group=self.scenario.state['groups'][self.gid]
        group['ref']['name']='=BAD()'
        group['ref']['details']['factory']='@Factory'
        first=next(iter(self.scenario.state['channels']))
        self.scenario.state['channels'][first]['name']='+Channel'
        self.scenario.save(update_fields=['state']);self.approve()
        from .models import SalesPlanningGoods
        SalesPlanningGoods.objects.all().update(brand='Changed after approval',manufacturer='Changed factory')
        book=self.export('models')
        _,records=self.records(book,'Количество')
        self.assertTrue(all(row['Группа планирования']=="'=BAD()" for row in records))
        self.assertTrue(all(row['Бренд / марка']=='Saved brand' for row in records))
        self.assertTrue(all(row['Производитель / завод']=="'@Factory" for row in records))
        self.assertIn("'+Channel",{row['Канал'] for row in records})
        self.assertFalse(any(cell.data_type=='f' for sheet in book for row in sheet for cell in row))

    def test_zero_lines_are_present_and_filters_do_not_limit_approved_export(self):
        group=self.scenario.state['groups'][self.gid]
        for line in group['channels'].values():line['input']='0'
        other=deepcopy(group);other['ref'].update(name='Other group',source_key='Other group')
        other['ref']['details']['brand']='Other brand'
        for line in other['channels'].values():line['input']='10'
        self.scenario.state['groups']['other']=other
        self.scenario.state['archived_groups']={'archived':deepcopy(group)}
        self.scenario.save(update_fields=['state']);self.approve()
        for variant in ('groups','powers','models'):
            book=self.export(variant,brand_page='missing',filter_q='nothing',filter_channel='none')
            _,records=self.records(book,'Количество')
            self.assertEqual({row['Группа планирования'] for row in records},{'G','Other group'})
            self.assertTrue(all(row['Итого за период']==0 for row in records if row['Группа планирования']=='G'))
            self.assertGreater(sum(row['Итого за период'] for row in records),0)
            self.assertTrue(all(row['Канал'] in {channel['name'] for channel in self.scenario.state['channels'].values()} for row in records))

    def test_draft_and_unknown_variant_do_not_download_and_approved_menu_lists_three_choices(self):
        for variant in ('groups','powers','models'):
            response=self.client.get(self.url,{'export':'plan_'+variant})
            self.assertContains(response,'после утверждения')
            self.assertNotIn('Content-Disposition',response)
        self.approve()
        response=self.client.get(self.url,{'tab':'plan'})
        for variant in ('groups','powers','models'):self.assertContains(response,'?export=plan_'+variant)
        response=self.client.get(self.url,{'export':'plan_unknown'})
        self.assertContains(response,'Выберите вариант выгрузки')


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class SalesExportBrowserTests(StaticLiveServerTestCase):
    setUp=setup

    def test_approved_menu_downloads_each_variant(self):
        from playwright.sync_api import sync_playwright,expect
        approve(self.scenario,self.actor,self.scenario.revision,False)
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1000},accept_downloads=True)
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=plan')
                menu=page.locator('.split-heading .split-sales-export')
                menu.locator('summary').click()
                expect(menu.get_by_role('navigation')).to_be_visible()
                for variant in ('groups','powers','models'):
                    with page.expect_download() as event:menu.locator('[href="?export=plan_'+variant+'"]').click()
                    self.assertTrue(event.value.suggested_filename.endswith('-'+variant+'.xlsx'))
                    self.assertIsNone(event.value.failure())
                page.screenshot(path='/tmp/approved-sales-export-menu.png',full_page=False)
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
