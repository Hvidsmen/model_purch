import importlib.util
import os
from pathlib import Path
from unittest import skipUnless
from django.test import SimpleTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Browser checks are opt-in')
class CoefficientVisibilityBrowserTests(SimpleTestCase):
    def test_hidden_group_reflows_section_spans_and_fills_table(self):
        from playwright.sync_api import sync_playwright
        script = (Path(__file__).parent / 'static/admin_motivation/coefficient_groups.js').read_text()
        groups = ('policies', 'sales', 'directors', 'curators')
        toggles = ''.join(f'<input type="checkbox" data-coefficient-group-toggle="{g}" checked>' for g in groups)
        headings = ''.join(f'<th colspan="5" data-coefficient-group="{g}">{g}</th>' for g in groups)
        leaves = ''.join(f'<th data-coefficient-group="{g}">K{i}</th>' for g in groups for i in range(5))
        cells = ''.join(f'<td data-coefficient-group="{g}"><input value="0.5%" style="width:100%;box-sizing:border-box"></td>' for g in groups for _ in range(5))
        html = '<style>table{width:1500px;table-layout:fixed;border-collapse:collapse}td,th{padding:0;border:1px solid #ccc}[hidden]{display:none!important}</style>'+toggles+'<table id="coefficient-table"><thead><tr><th rowspan="2">План</th><th rowspan="2">ERP</th><th rowspan="2">Марка</th>'+headings+'</tr><tr>'+leaves+'</tr></thead><tbody><tr class="coefficient-group-heading"><td colspan="23">RAC</td></tr><tr><td>RAC</td><td>Группа</td><td>Марка</td>'+cells+'</tr></tbody></table>'
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None, headless=True)
            try:
                page = browser.new_page(viewport={'width': 1700, 'height': 800})
                page.set_content(html)
                page.add_script_tag(content=script)
                def check_span(count):
                    self.assertEqual(page.locator('.coefficient-group-heading td').get_attribute('colspan'), str(count))
                    self.assertTrue(page.evaluate("()=>{const table=document.querySelector('table'); const cells=[...table.tBodies[0].rows[1].cells].filter(c=>!c.hidden);return Math.abs(cells.at(-1).getBoundingClientRect().right-table.getBoundingClientRect().right)<2}"))
                check_span(23)
                page.locator('[data-coefficient-group-toggle=curators]').uncheck()
                check_span(18)
                page.locator('[data-coefficient-group-toggle=policies]').uncheck()
                check_span(13)
                page.locator('[data-coefficient-group-toggle=curators]').check()
                check_span(18)
                for group in groups:
                    page.locator(f'[data-coefficient-group-toggle={group}]').uncheck()
                check_span(3)
            finally:
                browser.close()
