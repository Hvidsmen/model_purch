import importlib.util
import os
import re
from pathlib import Path
from unittest import skipUnless
from django.conf import settings
from django.test import TestCase
from . import test_shareholders as fixtures


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Browser checks are opt-in')
class ShareholderBrowserTests(TestCase):
    setUp=fixtures.SnapshotTests.setUp
    url=fixtures.SnapshotTests.url

    def test_five_screen_navigation_filters_and_single_table_scroll(self):
        from playwright.sync_api import sync_playwright,expect
        static=Path(settings.BASE_DIR)/'admin_motivation/static/admin_motivation'
        css='\n'.join((static/name).read_text() for name in ('reports.css','motivation_ux.css','reports_next.css','reports_performance.css','shareholders.css','shareholder_cards.css','manager_report_headers.css'))
        js='\n'.join((static/name).read_text() for name in ('reports_next.js','report_team_tabs.js','report_matrix_header.js','manager_detail.js','manager_report_headers.js','product_filters.js'))
        responses=[(kind,self.client.get(self.url(kind),{'type':'Политики','author':'Иванов','period':['1','5']})) for kind in ('team','mine','detail','deals','registry','executive')]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,headless=True)
            try:
                page=browser.new_page(viewport={'width':1440,'height':900});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                for kind,response in responses:
                    self.assertEqual(response.status_code,200)
                    html=response.content.decode();html=re.sub(r'<(?:link|script)\b[^>]*(?:href|src)=[^>]*>(?:</script>)?','',html)
                    page.set_content(html)
                    feature=page.locator('.sh-page').evaluate('el=>el.outerHTML')
                    page.set_content('<style>body{font:14px system-ui,sans-serif;background:#f5f6fa;color:#243650;margin:20px}a{color:#6551ae}.btn{display:inline-block;padding:8px 12px;border:1px solid #6551ae;border-radius:5px}'+css+'</style>'+feature+'<script>'+js+'</script>')
                    expect(page.locator('nav.sh-nav a')).to_have_count(6)
                    expect(page.locator('nav.sh-nav [aria-current=page]')).to_have_text(response.context['title'])
                    expect(page.locator('input[name=period][value="1"]')).to_be_checked()
                    expect(page.locator('input[name=period][value="5"]')).to_be_checked()
                    expect(page.locator('.nr-filter-panel')).to_be_visible()
                    expect(page.locator('select[name=subdivision]')).to_have_count(0)
                    if kind=='team':
                        periods=page.locator('.next-periods')
                        periods.locator('summary').click()
                        periods.locator('[data-period-clear]').click()
                        expect(periods.locator('input:checked')).to_have_count(0)
                        periods.locator('[data-period-all]').click()
                        expect(periods.locator('input:checked')).to_have_count(5)
                        periods.locator('summary').click()
                        product=page.locator('.next-products').first
                        product.locator('summary').click()
                        product.locator('[data-product-search]').fill('Нет такого значения')
                        self.assertTrue(product.locator('input[type=checkbox]').first.evaluate('el=>el.closest("label").hidden'))
                        product.locator('[data-product-search]').fill('')
                        product.locator('input[type=checkbox]').first.check()
                        self.assertNotEqual(product.locator('summary').inner_text(),'Все доступные')
                        product.locator('summary').click()

                    self.assertTrue(all(str(self.run.pk) in href for href in page.locator('nav.sh-nav a').evaluate_all('els=>els.map(el=>el.getAttribute("href"))')))
                    if kind=='executive':
                        expect(page.locator('select[name=author]')).to_have_count(0)
                        self.assertGreater(page.locator('.sh-premium-card').count(),0)
                        expect(page.locator('.sh-executive table')).to_have_count(0)
                        page.locator('.sh-premium-card summary').first.click()
                        expect(page.locator('.sh-premium-period').first).to_be_visible()
                        page.set_viewport_size({'width':390,'height':844})
                        self.assertTrue(page.locator('.sh-executive').evaluate('el=>el.scrollWidth<=el.clientWidth+1'))
                        page.screenshot(path='/tmp/shareholders-cards-mobile.png',full_page=True)
                        page.set_viewport_size({'width':1440,'height':900})
                    if kind=='detail':
                        for frame in page.locator('.manager-period-summary>.report-scroll,#manager-detail-scroll').all():self.assertTrue(frame.evaluate('el=>el.scrollHeight<=el.clientHeight+1'))
                    page.screenshot(path='/tmp/shareholders-'+kind+'.png',full_page=True)
                self.assertEqual(errors,[])
            finally:browser.close()
