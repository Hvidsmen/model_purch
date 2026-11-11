import importlib.util
import os
from io import BytesIO
from contextlib import nullcontext
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook
from . import test_versions_browser as fixtures
from .models import MotivationExecution
from .services.manager_detail import manager_detail_report
from .test_manager_detail import detail_rows


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Optional Chromium check')
class All2026DetailBrowserTests(StaticLiveServerTestCase):
    setUp=fixtures.GlobalVersionBrowserTests.setUp
    db=fixtures.GlobalVersionBrowserTests.db

    def test_separate_detail_tree_coefficients_and_excel(self):
        from playwright.sync_api import expect
        self.db(lambda:MotivationExecution.objects.create(run_id=uuid4(),job_key=uuid4(),state='completed',
            started_at=timezone.now(),completed_at=timezone.now(),parameters={'variant':'separate_2026','fact_year':2026}))
        data=manager_detail_report([dict(detail_rows()[1],АвторСделки='Employee',Подразделение='Д-Москва',
            КварталМотивации=period) for period in ('1 Квартал','2 Квартал(1-2 кв)')])
        query=QueryDict('scenario=separate&run=1&detail=1&type=Политики&period=1&period=2&author=Employee&subdivision=Д-Москва')
        with patch('admin_motivation.services.execution_tracking.observe_latest',return_value=None), \
             patch('admin_motivation.services.reports_next.current_run',return_value=nullcontext()), \
             patch('admin_motivation.services.reports_next.load_report',return_value=data) as load:
            self.page.goto(self.live_server_url+reverse('motivation_report_all_2026')+'?'+query.urlencode())
            expect(self.page.locator('#manager-detail-report')).to_be_visible()
            self.page.locator('#manager-detail-extra').check()
            self.page.locator('#manager-detail-expand').click()
            expect(self.page.locator('#manager-detail-report')).to_contain_text('Employee')
            with self.page.expect_download() as event:
                self.page.get_by_role('link',name='Скачать расширенную таблицу Excel',exact=True).click()
            with open(event.value.path(),'rb') as file:book=load_workbook(BytesIO(file.read()))
            self.assertIn('раздельно',str(list(book['Параметры отчёта'].values)))
            book.close()
            self.assertTrue(all(call.kwargs['calculation_variant']=='separate_2026' for call in load.call_args_list))
            self.assertEqual(self.errors,[])
