from io import BytesIO
from pathlib import Path
from decimal import Decimal
from unittest.mock import MagicMock, patch
import json
import re
import sqlite3

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from base.testing import authorize_test_case
from .models import MotivationCalculationSchedule, Motivation2025Input, SalesPlanScenario
from .services.recalculation_2025 import read_coefficients, summary, workbook
from .services.recalculation_2025_sql import patch_source, ALLOCATION


def file_bytes(bad=None):
    b = Workbook(); s = b.active
    s.append(['№','Группа планов продаж','Группа ERP','Марка / бренд',None,'Политики',None,None,None,None,'Продажи'])
    s.append([None]*5 + [f'K{i}' for i in range(5)]*2)
    s.append([1,'1. RAC','Бытовые кондиционеры','BRAND',None]+[.0016]*10)
    if bad is not None:
        s.cell(3,6,bad)
    f = BytesIO(); b.save(f); f.seek(0); return f


def source():
    s = (Path(__file__).parent/'sql/review/sp_CalcBySub.refactored.sql').read_text()
    s = s[s.index('CREATE OR ALTER PROCEDURE'):].replace('@Year','@FactYear')
    s = s.replace('@PlanVersion nvarchar(255)', '@QuarterShare decimal(9,8)=.8, @YearShare decimal(9,8)=.2, @PlanVersion nvarchar(255)')
    return s.replace('*0.8', '*@QuarterShare').replace('*0.2', '*@YearShare')


class Recalculation2025UnitTests(SimpleTestCase):
    def test_uploaded_layout_and_percent_fraction(self):
        rows, digest = read_coefficients(file_bytes())
        self.assertEqual(len(digest),64)
        self.assertEqual(rows[0]['Политики'], ['0.001600']*5)
        self.assertEqual(rows[0]['Продажи'], ['0.001600']*5)

    def test_formula_and_nonfinite_are_rejected(self):
        for value in ('=1/100','NaN','Infinity',1.1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                read_coefficients(file_bytes(value))

    def test_private_source_has_all_periods_and_keeps_bonus_base(self):
        s = patch_source(source())
        self.assertIn('CREATE PROCEDURE motivation.PortalCalcTest2025',s)
        self.assertIn('Price - AmountUSDIt_ITW + BallsOuterUSD',s)
        self.assertNotIn('WHERE 1=0 AND',s)
        self.assertNotIn('PortalSubdivisionMerge',s)
        self.assertNotIn('PortalCalculationSubdivisions',s)
        self.assertIn('s.Даичи_АвторСделкиНаименование',s)
        self.assertNotRegex(s,r"'Политики'\s+АвторСделки")
        self.assertIn('WHERE [Год]<>2025',s)
        self.assertEqual(s.count('SELECT TOP(1) c.PGSales'),4)
        self.assertIn('sc.Group_ [ГруппаТоваров]',s)
        self.assertIn('sc.Group_=pm.ГруппаТоваров',s)
        self.assertIn('sc.Brand=pm.Марка',s)
        self.assertIn('АвторСделки,ГруппаПлановПродаж',s)
        self.assertNotRegex(s,r"\b(?:INTO|FROM|DROP TABLE)\s+DataWH\.motivation\.(?:Fact|PlanFact|ReportMotivationByDealAuthor)\b")

    def test_unsupported_source_does_not_guess(self):
        with self.assertRaises(ValidationError):
            patch_source('CREATE PROC motivation.sp_CalcBySub AS SELECT 1')
        with self.assertRaises(ValidationError):
            patch_source(source().replace('INTO #percent_plan_menger','INTO #unknown'))

    def test_distribution_uses_annual_fact_per_branch_goods_and_author(self):
        sql = ALLOCATION[ALLOCATION.index('SELECT t.*'):ALLOCATION.index('IF EXISTS(SELECT 1 FROM #percent_plan_menger')]
        sql = sql.replace('INTO #percent_plan_menger','').replace('#fact','facts').replace("N'","'")
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE facts (ПодразделениеДокумент,АвторСделки,ГруппаПлановПродаж,ГруппаТоваров,Марка,СуммаОтгрузокUSD REAL,ТипКоэф,Год)')
            data = [('M','A','RAC','AC','X',30,'Политики',2025),('M','B','RAC','AC','X',70,'Политики',2025),
                    ('TT','A','RAC','AC','X',100,'Политики',2025),('M','A','RAC','AC','Y',100,'Политики',2025),
                    ('M','A','RAC','AC','X',9000,'Политики',2024),('M','Продажи','RAC','AC','X',8000,'Продажи',2025)]
            db.executemany('INSERT INTO facts VALUES (?,?,?,?,?,?,?,?)',data)
            result = db.execute(sql).fetchall()
        fractions = {(r[0],r[1],r[4]): r[-1] for r in result}
        self.assertAlmostEqual(fractions['M','A','X'],.3)
        self.assertAlmostEqual(fractions['M','B','X'],.7)
        self.assertEqual(fractions['TT','A','X'],1)
        self.assertEqual(fractions['M','A','Y'],1)
        self.assertAlmostEqual(1000 * fractions['M','A','X'] + 1000 * fractions['M','B','X'],1000)

    def test_summary_and_excel_do_not_double_count_branch_subtotals(self):
        rows = [('Политики','Д-Москва','A','1 Квартал',10),('Политики','Д-Москва','B','Год',20),
                ('Политики','ТЕРМОТРЕЙД','A','1 Квартал',5),('Продажи','Д-Москва','Продажи','Год',7)]
        rows.append(('Политики','Д-Москва','A','Год',0,Decimal('3.50')))
        sections = summary(rows)
        self.assertEqual(sections[0]['total'],Decimal('38.50'))
        self.assertEqual(sections[0]['bonus'],Decimal('3.50'))
        self.assertEqual(sections[0]['values'][4],20)
        b = load_workbook(workbook(sections),data_only=False)
        self.assertEqual(b.sheetnames,['1. Политики','2. Продажи','3. Выполнение политик','4. Выполнение продаж'])
        self.assertEqual(b.worksheets[0].cell(2,2).value,'Сотрудник')
        self.assertEqual(b.worksheets[0].cell(b.worksheets[0].max_row,9).value,38.5)
        self.assertEqual(b.worksheets[0].cell(b.worksheets[0].max_row,8).value,3.5)
        self.assertEqual(b.worksheets[1].cell(b.worksheets[1].max_row,8).value,7)
        self.assertIn('USD', b.worksheets[0].cell(3,3).number_format)

    def test_excel_external_names_are_literal(self):
        b = load_workbook(workbook(summary([('Политики','=HYPERLINK("evil")','=1+1','Год',1)])))
        cells = [c for row in b.worksheets[0] for c in row if isinstance(c.value,str) and c.value.startswith('=')]
        self.assertTrue(cells)
        self.assertTrue(all(c.data_type=='s' for c in cells))


class Recalculation2025WebTests(TestCase):
    def setUp(self):
        authorize_test_case(self)
        self.plan=SalesPlanScenario.objects.create(title='План 2025',source_version='Plan 25',loaded_at=timezone.now())
        self.url=reverse('motivation_report_2025')

    def make_job(self):
        job=MotivationCalculationSchedule.objects.create(name='Пересчёт 2025',variant='test_2025',fact_year=2025,plan_version='Plan 25')
        rows,sha=read_coefficients(file_bytes())
        Motivation2025Input.objects.create(job=job,coefficients=rows,source_name='coeffs.xlsx',source_sha256=sha)
        return job

    def test_catalog_and_empty_page(self):
        self.assertContains(self.client.get(reverse('motivation_reports')),'Пересчёт 2025')
        self.assertContains(self.client.get(self.url),'План 2025')

    def test_upload_is_private_and_does_not_start_calculation(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload=SimpleUploadedFile('coeffs.xlsx',file_bytes().read())
        with patch('admin_motivation.services.calculation_jobs.start_job') as start:
            r=self.client.post(self.url,dict(action='upload',plan_version='Plan 25',quarter_share=80,year_share=20,payout_threshold='.8',coefficients=upload))
        self.assertEqual(r.status_code,302)
        start.assert_not_called()
        saved=Motivation2025Input.objects.get()
        self.assertEqual(len(saved.coefficients),1)
        self.assertEqual(saved.job.fact_year,2025)
        self.assertEqual(saved.job.variant,'test_2025')

    def test_run_uses_only_selected_test_job(self):
        job=self.make_job()
        with patch('admin_motivation.services.calculation_jobs.monitor',return_value={}), patch('admin_motivation.services.calculation_jobs.start_job') as start:
            r=self.client.post(self.url,dict(action='run',job=job.pk))
        self.assertEqual(r.status_code,302)
        self.assertEqual(start.call_args.args[0].variant,'test_2025')
        job.refresh_from_db();self.assertTrue(job.synced)
        main=MotivationCalculationSchedule.objects.create(name='Main',fact_year=2026)
        with patch('admin_motivation.services.calculation_jobs.start_job') as start:
            self.assertEqual(self.client.post(self.url,dict(action='run',job=main.pk)).status_code,404)
        start.assert_not_called()

    def test_anonymous_cannot_read_or_start(self):
        self.client.logout()
        self.assertIn(self.client.get(self.url).status_code,(302,403))
        self.assertIn(self.client.post(self.url,{'action':'run'}).status_code,(302,403))

    def test_job_command_and_audit_snapshot(self):
        from .services.calculation_jobs import calculation_command
        from .services.execution_tracking import parameters
        job=self.make_job();job.plan_version="Plan O'Brien"
        command=calculation_command(job)
        self.assertIn('EXEC motivation.PortalCalcTest2025 @FactYear=2025',command)
        self.assertIn('@Test2025Coefficients=',command)
        self.assertIn("Plan O''Brien",command)
        self.assertNotIn('EXEC motivation.PortalCalcBySub',command)
        self.assertEqual(parameters(job)['premium_periods'],list('12345'))
        self.assertEqual(parameters(job)['coefficient_sha256'],job.recalculation_2025.source_sha256)
        job.fact_year=2026
        with self.assertRaises(ValidationError):calculation_command(job)

    def test_read_blocks_old_or_failed_results_under_lock(self):
        from .reports_2025 import read_results
        job=self.make_job();connection,cursor=MagicMock(),MagicMock()
        cursor.fetchone.side_effect=[(0,),('another-job','completed','fact25-plan25-v1')]
        with self.assertRaises(ValidationError):read_results(job,lambda *a:(connection,cursor))
        self.assertFalse(any('ReportMotivationByDealAuthorTest2025' in c.args[0] for c in cursor.execute.call_args_list))
        connection.close.assert_called_once()


class SalesParticipationTests(SimpleTestCase):
    def test_dpp_channel_survives_for_sales_and_directors_only(self):
        from .services.sales_participation import patch as participation
        from .services.director_participation import patch as directors
        from .test_director_participation import SOURCE
        sql=participation(directors(SOURCE)).replace('#pf_base','base').replace("N'", "'")
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE base (Канал,ТипКоэф,Подразделение)')
            db.executemany('INSERT INTO base VALUES (?,?,?)',[
                ('ДПП',kind,'Д-Москва') for kind in ('Политики','Продажи','Директора')])
            db.executescript(sql)
            self.assertCountEqual(db.execute('SELECT ТипКоэф FROM base').fetchall(), [('Продажи',),('Директора',)])

    def test_private_prepare_uses_full_fact_without_live_writes(self):
        from .services.recalculation_2025_sql import prepare
        cursor=MagicMock();cursor.fetchone.side_effect=[(None,),(0,)]
        prepare(cursor)
        sql=cursor.execute.call_args.args[0]
        self.assertRegex(sql,r'FROM\s+#portal_motivation_sales s')
        self.assertNotIn('FROM DataWH.dbo.Sales s',sql)
        self.assertNotIn('PortalCalculationSubdivisions',sql)
        self.assertIn('SELECT SubdivisionKey FROM DataWH.dbo.Subdivisions',sql)
        self.assertIn("NULLIF(ds.Даичи_АвторСделкиНаименование,N''),s.Даичи_АвторСделкиНаименование",sql)
        self.assertIn('THROW 50025',sql)

    def test_plan_year_is_checked_before_an_agent_job_is_installed(self):
        from .services.calculation_jobs import validate_sql_parameters
        from types import SimpleNamespace
        cursor=MagicMock()
        cursor.fetchall.return_value=[(name,) for name in ('@FactYear','@PlanVersion','@QuarterShare','@YearShare','@PayoutThreshold')]
        cursor.fetchone.side_effect=[(1,),None]
        with self.assertRaisesMessage(ValidationError,'нет плана за 2025'):
            validate_sql_parameters(cursor,SimpleNamespace(variant='test_2025',fact_year=2025,plan_version='Plan 26'))

    def test_shared_reader_accepts_only_matching_input_snapshot(self):
        from .reports_2025 import read_results
        from .services.execution_tracking import parameters
        from types import SimpleNamespace
        job=SimpleNamespace(key='1234',variant='test_2025',fact_year=2025,plan_version='P25',
            quarter_share=80,year_share=20,payout_threshold='.8',recalculation_2025=SimpleNamespace(source_sha256='abc'))
        connection,cursor=MagicMock(),MagicMock()
        data=parameters(job)
        cursor.fetchone.side_effect=[(0,),('1234','completed','fact25-plan25-v1',json.dumps(data))]
        cursor.fetchall.return_value=[('Политики','Д-Москва','A','Год',10)]
        sections=read_results(job,lambda *a:(connection,cursor))
        self.assertEqual(sections[0]['total'],10)
        data['plan_version']='Other'
        cursor.fetchone.side_effect=[(0,),('1234','completed','fact25-plan25-v1',json.dumps(data))]
        with self.assertRaisesMessage(ValidationError,'изменились'):
            read_results(job,lambda *a:(connection,cursor))


import os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1','Optional Chromium check')
class Recalculation2025BrowserTests(StaticLiveServerTestCase):
    def test_tabs_totals_and_four_sheet_download(self):
        from playwright.sync_api import sync_playwright, expect
        from django.conf import settings
        from types import SimpleNamespace
        authorize_test_case(self)
        job=MotivationCalculationSchedule.objects.create(name='Пересчёт 2025',variant='test_2025',fact_year=2025,
            plan_version='План 2025',synced=True)
        rates,sha=read_coefficients(file_bytes())
        Motivation2025Input.objects.create(job=job,coefficients=rates,source_name='coeffs.xlsx',source_sha256=sha)
        sections=summary([('Политики','Д-Москва','Иванов','1 Квартал',100),
            ('Политики','ТЕРМОТРЕЙД','Петров','Год',50),('Продажи','Д-Москва','Продажи','Год',70)])
        from .services.recalculation_2025_performance import explain
        rows = explain([
            ('Политики','Д-Москва','Иванов','PG','G','B','Подразделение(Глобально)','1 Квартал',100,100,100,100,0),
            ('Продажи','Д-Москва','Продажи','PG','G','B','Подразделение','Год',100,90,70,70,0)], [], '.8')
        for section in sections:
            section['performance'] = [row for row in rows if row['kind'] == section['kind']]
        with patch('admin_motivation.reports_2025.read_results',return_value=sections), patch(
                'admin_motivation.services.execution_tracking.observe',return_value=SimpleNamespace(state='completed')):
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                page.goto(self.live_server_url+reverse('motivation_report_2025')+'?run=1')
                expect(page.get_by_role('cell',name='Иванов',exact=True)).to_be_visible()
                expect(page.get_by_role('cell',name='Петров',exact=True)).to_be_visible()
                expect(page.get_by_role('columnheader',name='Сотрудник',exact=True)).to_be_visible()
                expect(page.get_by_role('columnheader',name='Доплаты за кварталы',exact=True)).to_be_visible()
                expect(page.get_by_role('columnheader',name='Подразделение менеджера',exact=True)).to_have_count(0)
                page.screenshot(path='/tmp/recalculation-2025-policies.png',full_page=True)
                page.get_by_role('link',name='Продажи',exact=True).click()
                expect(page.get_by_role('heading',name='Продажи · USD')).to_be_visible()
                expect(page.get_by_role('columnheader',name='Сотрудник',exact=True)).to_have_count(0)
                with page.expect_download() as pending:
                    page.get_by_role('link',name='Скачать Excel · 4 листа').click()
                download=pending.value
                self.assertEqual(download.suggested_filename,'motivation-2025-usd.xlsx')
                book=load_workbook(BytesIO(Path(download.path()).read_bytes()))
                self.assertEqual(book.sheetnames,['1. Политики','2. Продажи','3. Выполнение политик','4. Выполнение продаж'])
                self.assertEqual(book.worksheets[0].cell(book.worksheets[0].max_row,9).value,150)
                self.assertEqual(book.worksheets[1].cell(book.worksheets[1].max_row,8).value,70)
                self.assertEqual(book.worksheets[2].cell(4,10).value,1)
                self.assertEqual(book.worksheets[3].cell(4,9).value,.9)
                browser.close()

    def test_failed_launch_preserves_saved_fields_across_browser_refresh(self):
        from playwright.sync_api import sync_playwright, expect
        from django.conf import settings
        authorize_test_case(self)
        SalesPlanScenario.objects.create(title='План 2025',source_version='Plan 25',loaded_at=timezone.now())
        job=MotivationCalculationSchedule.objects.create(name='Пересчёт 2025',variant='test_2025',fact_year=2025,
            plan_version='Plan 25',quarter_share=80,year_share=20,payout_threshold='.8')
        rates,sha=read_coefficients(file_bytes())
        Motivation2025Input.objects.create(job=job,coefficients=rates,source_name='coeffs.xlsx',source_sha256=sha)
        with patch('admin_motivation.services.calculation_jobs.monitor',return_value={}), patch(
                'admin_motivation.services.calculation_jobs.start_job',side_effect=ValidationError('SQL Server временно недоступен')) as start:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.cookies[settings.SESSION_COOKIE_NAME].value,'url':self.live_server_url}])
                page.goto(self.live_server_url+reverse('motivation_report_2025')+f'?job={job.pk}')
                page.get_by_role('button',name='Запустить пересчёт 2025',exact=True).click()
                expect(page.get_by_role('alert')).to_contain_text('SQL Server временно недоступен')
                expect(page.locator('.errorlist')).to_have_count(0)
                page.reload()
                expect(page.get_by_role('alert')).to_contain_text('SQL Server временно недоступен')
                expect(page.get_by_text('Входные данные сохранены. Запуск не выполнен',exact=False)).to_be_visible()
                page.get_by_text('Изменить сохранённые входные данные',exact=True).click()
                expect(page.locator('select[name=plan_version]')).to_have_value('Plan 25')
                self.assertEqual(float(page.locator('input[name=quarter_share]').input_value()),80)
                self.assertEqual(float(page.locator('input[name=year_share]').input_value()),20)
                self.assertEqual(float(page.locator('input[name=payout_threshold]').input_value()),.8)
                expect(page.locator('.errorlist')).to_have_count(0)
                self.assertIsNone(page.locator('input[name=coefficients]').get_attribute('required'))
                page.screenshot(path='/tmp/recalculation-2025-saved-after-failure.png',full_page=True)
                self.assertEqual(start.call_count,1)
                browser.close()
