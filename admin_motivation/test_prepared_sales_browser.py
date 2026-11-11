import importlib.util
import json
import os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_versions_browser as fixtures


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Optional Chromium check')
class PreparedSalesButtonBrowserTests(StaticLiveServerTestCase):
    setUp=fixtures.GlobalVersionBrowserTests.setUp
    db=fixtures.GlobalVersionBrowserTests.db

    def test_busy_audit_blocks_start_and_submit_stays_disabled_until_navigation(self):
        from playwright.sync_api import expect
        state={'status':'Подготовка документов','agent_running':False,'history':[
            {'run_id':'new-run','started_at':'2026-11-11T12:49:46Z','completed_at':None,'state':'running','stage':'Подготовка документов'}]}
        failure=[False];requests=[]
        def respond(route):
            requests.append(route.request.method)
            route.fulfill(status=503 if failure[0] else 200,content_type='application/json',body=json.dumps(
                {'error':'Статус временно недоступен'} if failure[0] else state))
        self.page.route('**'+reverse('motivation_prepared_sales_monitor')+'*',respond)
        self.page.goto(self.live_server_url+reverse('motivation_calculations'))
        button=self.page.locator('[data-prepared-start]')
        expect(button).to_have_text('Подготовка выполняется…');expect(button).to_be_disabled()
        self.assertEqual(button.evaluate('(button)=>getComputedStyle(button).opacity'),'0.55')
        failure[0]=True
        self.page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(self.page.locator('[data-prepared-status]')).to_have_text('Статус временно недоступен')
        expect(button).to_be_disabled()
        failure[0]=False
        state['history'][0].update(state='completed',completed_at='2026-11-11T13:00:00Z')
        state['status']='Готово'
        self.page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(button).to_have_text('Подготовить сейчас');expect(button).to_be_enabled()
        # Observe the real form event, then hold navigation so a second submit
        # and a stale poll can be exercised while the POST would be pending.
        self.page.evaluate("""window.submits=[];document.querySelector('[data-prepared-start]').form.addEventListener('submit',event=>{
            window.submits.push(event.defaultPrevented);event.preventDefault();});""")
        button.click()
        expect(button).to_have_text('Запускаем подготовку…');expect(button).to_be_disabled()
        self.page.evaluate("document.querySelector('[data-prepared-start]').form.requestSubmit()")
        self.assertEqual(self.page.evaluate('window.submits'),[False,True])
        with self.page.expect_response(lambda response:reverse('motivation_prepared_sales_monitor') in response.url):
            self.page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        expect(button).to_be_disabled()
        self.assertTrue(all(method=='GET' for method in requests))
        self.assertEqual(self.errors,[])
