import importlib.util,os
from urllib.parse import parse_qs
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from . import test_split_ddp as fixtures

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class CompactPlanSaveBrowserTests(StaticLiveServerTestCase):
    def setUp(self):fixtures.DdpTests.setUp(self)

    def test_html_error_preserves_input_then_large_form_saves_with_ctrl_s(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1000})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(self.live_server_url+self.url+'?tab=plan')
                page.evaluate("""()=>{const f=document.querySelector('#split-plan-inputs');for(let i=0;i<1100;i++){const n=document.createElement('input');n.type='hidden';n.name='extra_'+i;n.value='keep';f.append(n);}}""")
                field=page.get_by_label('DDP G',exact=True);field.fill('145')
                def html_error(route):
                    if route.request.method=='POST':route.fulfill(status=500,content_type='text/html',body='<!DOCTYPE html><h1>Server error</h1>')
                    else:route.continue_()
                pattern='**'+self.url+'*';page.route(pattern,html_error)
                page.keyboard.press('Control+s')
                expect(page.get_by_text('Сервер вернул неожиданный ответ (HTTP 500).',exact=False).first).to_be_visible()
                expect(field).to_have_value('145');expect(page.locator('.split-unsaved-dock')).to_be_visible()
                page.unroute(pattern,html_error)
                with page.expect_request(lambda r:r.method=='POST' and self.url in r.url) as sent:
                    with page.expect_navigation():page.keyboard.press('Control+s')
                payload=parse_qs(sent.value.post_data)
                self.assertLess(len(payload),10);self.assertIn('plan_payload',payload)
                expect(page.get_by_label('DDP G',exact=True)).to_have_value('145')
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups'][self.gid]['ddp'],'145')
