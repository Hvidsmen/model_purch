import importlib.util
import os
from pathlib import Path
from unittest import skipUnless
from django.test import SimpleTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Browser checks are opt-in')
class GoodsHeaderBrowserTests(SimpleTestCase):
    def test_document_scroll_header_alignment_and_select_all(self):
        from playwright.sync_api import sync_playwright, expect
        template = (Path(__file__).parent / 'templates/model_purch/pggoods_list.html').read_text()
        css = template.split('<style>', 2)[2].split('</style>', 1)[0]
        script = (Path(__file__).parent / 'static/model_purch/goods_table_header.js').read_text()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None, headless=True)
            try:
                page = browser.new_page(viewport={'width': 900, 'height': 650})
                rows = ''.join('<tr style="height:90px"><td>Товар</td><td>Цена</td></tr>' for _ in range(30))
                page.set_content('<style>'+css+'body{margin:0}.goods-table-scroll{margin-left:100px;width:750px}.product-table{width:1400px;table-layout:fixed}th{height:45px}</style><div style="height:300px"></div><div class="table-responsive goods-table-scroll"><table class="table product-table"><colgroup><col style="width:700px"><col style="width:700px"></colgroup><thead><tr><th><input id="goodsFillSelectAll" type="checkbox">Товар</th><th>Стоимость</th></tr></thead><tbody>'+rows+'</tbody></table></div><div style="height:700px"></div>')
                page.evaluate("document.querySelector('#goodsFillSelectAll').addEventListener('change',()=>document.body.dataset.selected='yes')")
                page.add_script_tag(content=script)
                expect(page.locator('.goods-fixed-heading')).to_be_hidden()
                self.assertTrue(page.locator('.goods-table-scroll').evaluate('el=>el.scrollHeight===el.clientHeight'))
                page.evaluate('window.scrollTo(0,700)')
                expect(page.locator('.goods-fixed-heading')).to_be_visible()
                page.locator('.goods-fixed-heading input').check()
                self.assertTrue(page.locator('#goodsFillSelectAll').is_checked())
                self.assertEqual(page.locator('body').get_attribute('data-selected'), 'yes')
                page.locator('.goods-table-scroll').evaluate('el=>el.scrollLeft=350')
                page.wait_for_timeout(100)
                source = page.locator('.goods-table-scroll th').nth(1).bounding_box()
                fixed = page.locator('.goods-fixed-heading th').nth(1).bounding_box()
                self.assertAlmostEqual(source['x'], fixed['x'], delta=1)
                self.assertAlmostEqual(page.locator('.goods-fixed-heading').bounding_box()['y'], 0, delta=1)
                page.evaluate('window.scrollTo(0,document.body.scrollHeight)')
                expect(page.locator('.goods-fixed-heading')).to_be_hidden()
            finally:
                browser.close()
