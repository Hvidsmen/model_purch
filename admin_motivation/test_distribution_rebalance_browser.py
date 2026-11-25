"""Exercise fund limits with the actual browser range controls."""
import importlib.util
import os
from pathlib import Path
from unittest import skipUnless
from django.test import SimpleTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Browser checks are opt-in')
class RebalanceBrowserTests(SimpleTestCase):
    def test_release_then_allocate_cents_without_crossing_payment_funds(self):
        from playwright.sync_api import sync_playwright
        cards = ''.join(f'<article data-fund-kind="Политики" data-fund-payment="{payment}" data-fund-amount="100.00"><span data-allocated></span><b data-remaining></b></article>' for payment in ('annual', 'catchup'))
        inputs = ''.join(f'<input name="{payment}_{i}" data-rebalance-kind="Политики" data-rebalance-payment="{payment}" value="{amount}" aria-label="{payment} {i}">' for payment in ('annual', 'catchup') for i, amount in enumerate(('60.00', '40.00')))
        script = (Path(__file__).parent / 'static/admin_motivation/distribution_rebalance.js').read_text()
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None, headless=True)
            try:
                page = browser.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.set_content(cards + '<form>' + inputs + '</form>')
                page.add_script_tag(content=script)
                ranges = page.locator('input[type=range]')
                ranges.nth(1).press('End')  # Full fund: increasing is refused.
                self.assertEqual(page.locator('[name=annual_1]').input_value(), '40.00')
                ranges.nth(0).press('ArrowLeft')  # Release exactly one cent.
                self.assertEqual(page.locator('[name=annual_0]').input_value(), '59.99')
                ranges.nth(3).press('End')  # The other payment cannot spend that cent.
                self.assertEqual(page.locator('[name=catchup_1]').input_value(), '40.00')
                ranges.nth(1).press('End')  # Spend only the released cent.
                self.assertEqual(page.locator('[name=annual_1]').input_value(), '40.01')
                ranges.nth(1).press('End')
                self.assertEqual(page.locator('[name=annual_1]').input_value(), '40.01')
                self.assertEqual(page.locator('[data-fund-payment=annual] [data-remaining]').inner_text(), '0,00')
                self.assertFalse(errors)
            finally:
                browser.close()
