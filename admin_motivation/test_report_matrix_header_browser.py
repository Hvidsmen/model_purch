import importlib.util
import os
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.template.loader import render_to_string
from django.test import SimpleTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Browser checks are opt-in')
class MatrixHeaderBrowserTests(SimpleTestCase):
    def test_page_scroll_fixed_header_modes_and_horizontal_alignment(self):
        from playwright.sync_api import sync_playwright,expect
        metrics={'metrics':[{'possible':10,'remaining':100,'ratio':.5,'calculated':0}]*2,
                 'total_metrics':{'possible':20,'remaining':100,'ratio':.5,'calculated':0},'bonus':0,'total':0}
        group={'branch':'A','author':'Employee',**metrics,'children':[
            {'label':'Product '+str(i),**metrics,'detail_url':'#','deal_links':[]} for i in range(30)]}
        html=render_to_string('admin_motivation/reports_next/_my_products.html',{
            'presentation':{'product_sections':[{'kind':'Политики','periods':['4 квартал','Год'],'metric_columns':14,'groups':[group]}]},'ledger':{'currency':'USD'}})
        static=Path(settings.BASE_DIR)/'admin_motivation/static/admin_motivation'
        css='\n'.join((static/name).read_text() for name in ('reports_next.css','motivation_ux.css'))
        js='\n'.join((static/name).read_text() for name in ('reports_next.js','report_matrix_header.js'))
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,headless=True)
            try:
                page=browser.new_page(viewport={'width':1200,'height':800})
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.set_content('<style>'+css+'</style><div class="content-wrapper"><section class="nr-page" style="padding-top:250px">'+html+'</section></div><script>'+js+'</script>')
                viewport=page.locator('.nr-product-scroll')
                self.assertTrue(viewport.evaluate('el=>el.scrollHeight<=el.clientHeight+1'))
                page.evaluate("window.scrollTo(0,document.querySelector('.nr-matrix-head').getBoundingClientRect().top+window.scrollY+160)")
                floating=page.locator('.nr-matrix-floating');expect(floating).to_be_visible()
                self.assertLess(abs(floating.bounding_box()['y']),1)
                viewport.evaluate('el=>el.scrollLeft=350')
                page.wait_for_timeout(100)
                original=page.locator('.nr-product-scroll .nr-matrix-header>span').nth(3).bounding_box()
                copied=floating.locator('.nr-matrix-header>span').nth(3).bounding_box()
                self.assertLess(abs(original['x']-copied['x']),2)
                self.assertLess(abs(original['width']-copied['width']),2)
                page.evaluate("document.querySelector('[data-matrix-details]').checked=false;document.querySelector('[data-matrix-details]').dispatchEvent(new Event('change'))")
                expect(floating).to_have_attribute('data-mode','basic')
                self.assertEqual(floating.locator('.nr-matrix-execution:visible').count(),0)
                page.set_viewport_size({'width':900,'height':800});page.wait_for_timeout(100)
                self.assertTrue(viewport.evaluate('el=>el.scrollHeight<=el.clientHeight+1'))
                page.screenshot(path='/tmp/motivation-matrix-page-scroll.png')
                page.evaluate('window.scrollTo(0,0)');expect(floating).to_be_hidden()
                self.assertEqual(errors,[])
            finally:browser.close()
