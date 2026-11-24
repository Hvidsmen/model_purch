import importlib.util
import os
import re
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from .services.manager_detail import manager_detail_report
from .test_manager_detail import detail_rows


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Browser checks are opt-in')
class ManagerReportHeadersBrowserTests(SimpleTestCase):
    def test_both_tables_follow_page_with_headers_and_dynamic_columns(self):
        from playwright.sync_api import sync_playwright,expect
        rows=[dict(row,АвторСделки='Employee '+str(i)) for i in range(20) for row in detail_rows()]
        data=manager_detail_report(rows)
        html=render_to_string('admin_motivation/reports/manager_detail.html',{'data':data,'new_report_terms':True})
        html=re.sub(r'<(?:link|script)\b[^>]*(?:href|src)=[^>]*>(?:</script>)?','',html)
        static=Path(settings.BASE_DIR)/'admin_motivation/static/admin_motivation'
        css='\n'.join((static/name).read_text() for name in ('reports.css','motivation_ux.css','manager_report_headers.css'))
        js='\n'.join((static/name).read_text() for name in ('manager_detail.js','manager_report_headers.js'))
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,headless=True)
            try:
                page=browser.new_page(viewport={'width':1100,'height':850})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.set_content('<style>'+css+'</style><div class="content-wrapper"><div style="height:200px"></div>'+html+'</div><script>'+js+'</script>')
                summary=page.locator('.manager-period-summary>.report-scroll')
                detail=page.locator('#manager-detail-scroll')
                for frame in (summary,detail):self.assertTrue(frame.evaluate('el=>el.scrollHeight<=el.clientHeight+1'))
                page.evaluate("window.scrollTo(0,document.querySelector('.manager-period-summary thead').getBoundingClientRect().top+window.scrollY+100)")
                native=page.locator('.manager-period-summary>.manager-report-floating');expect(native).to_be_visible()
                self.assertLess(abs(native.bounding_box()['y']),1)
                summary.evaluate('el=>el.scrollLeft=150');page.wait_for_timeout(100)
                originals=page.locator('.manager-period-summary>.report-scroll thead th')
                copies=native.locator('thead th')
                for index in range(originals.count()):
                    original=originals.nth(index).bounding_box();copied=copies.nth(index).bounding_box()
                    self.assertLess(abs(original['x']-copied['x']),2)
                    self.assertLess(abs(original['width']-copied['width']),2)
                page.get_by_role('button',name='Развернуть всё',exact=True).click()
                page.evaluate("window.scrollTo(0,document.querySelector('.manager-sticky-header').getBoundingClientRect().top+window.scrollY+150)")
                floating=page.locator('#manager-detail-panel>.manager-report-floating');expect(floating).to_be_visible()
                self.assertLess(abs(floating.bounding_box()['y']),1)
                for action in ('extra','period'):
                    if action=='extra':page.locator('#manager-detail-extra').evaluate("el=>{el.checked=true;el.dispatchEvent(new Event('change'))}")
                    else:page.locator('[data-manager-period-toggle="0"]').evaluate("el=>{el.checked=false;el.dispatchEvent(new Event('change'))}")
                    detail.evaluate('el=>el.scrollLeft=180');page.wait_for_timeout(100)
                    originals=page.locator('#manager-detail-report .report-column-header>span:visible')
                    copies=floating.locator('.report-column-header>span:visible')
                    self.assertEqual(originals.count(),copies.count())
                    for index in range(originals.count()):
                        original=originals.nth(index).bounding_box();copied=copies.nth(index).bounding_box()
                        self.assertLess(abs(original['x']-copied['x']),2)
                        self.assertLess(abs(original['width']-copied['width']),2)
                    self.assertTrue(detail.evaluate('el=>el.scrollHeight<=el.clientHeight+1'))
                page.screenshot(path='/tmp/manager-report-single-scroll.png')
                page.evaluate('window.scrollTo(0,0)');expect(floating).to_be_hidden();expect(native).to_be_hidden()
                self.assertEqual(errors,[])
            finally:browser.close()
