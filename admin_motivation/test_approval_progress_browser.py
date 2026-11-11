"""Exercise both approval HTTP phases without inventing server percentages."""
import importlib.util
import json
import os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_versions_browser as fixtures


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Optional Chromium check')
class ApprovalProgressBrowserTests(StaticLiveServerTestCase):
    setUp=fixtures.GlobalVersionBrowserTests.setUp
    db=fixtures.GlobalVersionBrowserTests.db

    def test_pending_review_and_commit_show_progress_and_elapsed_time(self):
        from playwright.sync_api import expect
        self.page.goto(self.live_server_url+reverse('global_coeff_admin_motivation'))
        pending=[]
        self.page.route('**'+reverse('motivation_coefficient_comparison'),lambda route:pending.append(route))
        self.page.locator('#approval-review').click()
        expect(self.page.locator('#approval-progress')).to_be_visible()
        expect(self.page.locator('#approval-progress-time')).to_contain_text('Подготовка подтверждения')
        self.page.wait_for_function("document.getElementById('approval-progress-time').textContent.includes('0 мин 1 сек')")
        self.assertNotIn('include_comparison',pending[0].request.post_data_json)
        pending.pop(0).fulfill(status=200,content_type='application/json',body=json.dumps({
            'token':'test','date':'2026-01-01','unsaved':False,'replaced':[],
            'baseline_replaced':False,'summary':{}}))
        expect(self.page.locator('#approval-progress')).not_to_be_visible()
        expect(self.page.locator('#approval-commit')).to_be_enabled()
        self.page.locator('#approval-reason').fill('Изменение сегментов скидки')
        self.page.locator('#approval-commit').click()
        expect(self.page.locator('#approval-progress')).to_be_visible()
        expect(self.page.locator('#approval-progress-time')).to_contain_text('Сохранение утверждения')
        expect(self.page.locator('#approval-commit')).to_be_disabled()
        expect(self.page.locator('#approval-cancel')).to_be_disabled()
        pending.pop(0).fulfill(status=400,content_type='application/json',body='{"error":"Повторите подтверждение"}')
        expect(self.page.locator('#approval-progress')).not_to_be_visible()
        expect(self.page.locator('#approval-error')).to_have_text('Повторите подтверждение')
        expect(self.page.locator('#approval-commit')).to_be_enabled()
        self.assertEqual(self.errors,[])
