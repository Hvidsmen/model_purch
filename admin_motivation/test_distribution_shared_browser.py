from pathlib import Path
from unittest import TestCase,skipUnless
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None


@skipUnless(sync_playwright and Path('/usr/bin/chromium').exists(),'Chromium required')
class SharedPoolBrowserTests(TestCase):
    def test_common_pool_floor_and_independent_payment(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            cards=''.join(f'<article data-pool-payment="{payment}" data-fund-amount="300" data-fixed-allocated="0"><span data-allocated></span><span data-remaining></span></article>' for payment in ('annual','catchup'))
            inputs=''.join(f'<input name="{payment}_{kind}" value="100.00" data-shared-input data-rebalance-kind="{kind}" data-rebalance-payment="{payment}" data-minimum="{0 if kind=="sales" else 75}.00" data-original="100.00">' for payment in ('annual','catchup') for kind in ('sales','policies','directors'))
            page.set_content(cards+'<form>'+inputs+'</form>')
            page.add_script_tag(path=str(Path(__file__).parent/'static/admin_motivation/distribution_shared.js'))
            ranges=page.locator('input[type=range]')
            ranges.nth(1).press('Home')
            self.assertEqual(page.locator('[name=annual_policies]').input_value(),'75.00')
            self.assertEqual(page.locator('[data-pool-payment=annual] [data-remaining]').inner_text(),'25,00')
            ranges.nth(3).press('End')
            self.assertEqual(page.locator('[name=catchup_sales]').input_value(),'100.00')
            ranges.nth(0).press('End')
            self.assertEqual(page.locator('[name=annual_sales]').input_value(),'125.00')
            self.assertEqual(page.locator('[data-pool-payment=annual] [data-remaining]').inner_text(),'0,00')
            ranges.nth(1).press('ArrowLeft')
            self.assertEqual(page.locator('[name=annual_policies]').input_value(),'75.00')
            page.locator('[name=annual_directors]').fill('74.99')
            self.assertFalse(page.locator('form').evaluate('(form)=>form.checkValidity()'))
            page.locator('[name=annual_directors]').fill('100.00')
            page.locator('[name=annual_sales]').fill('126.00')
            self.assertFalse(page.locator('form').evaluate('(form)=>form.checkValidity()'))
            page.locator('[name=annual_sales]').fill('125.00')
            self.assertTrue(page.locator('form').evaluate('(form)=>form.checkValidity()'))
            self.assertFalse(errors)
            browser.close()
