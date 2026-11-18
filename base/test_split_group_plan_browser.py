import importlib.util
import os
from unittest import skipUnless

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from . import test_split_cycle as fixtures


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Requires Chromium')
class SplitGroupPlanBrowserTests(StaticLiveServerTestCase):
    setUp = fixtures.SplitCycleTests.setUp
    complete = fixtures.SplitCycleTests.complete

    def test_edit_total_distributes_channels_and_localized_decimal_stays_editable(self):
        from playwright.sync_api import sync_playwright, expect
        self.complete()
        gid, group = next(iter(self.scenario.state['groups'].items()))
        cids = list(group['channels'])
        with sync_playwright() as driver:
            browser = driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'), headless=True)
            context = browser.new_context(viewport={'width': 1600, 'height': 1000})
            try:
                context.add_cookies([{'name': 'sessionid', 'value': self.client.cookies['sessionid'].value, 'url': self.live_server_url}])
                page = context.new_page()
                page.goto(self.live_server_url + reverse('split_scenario', args=[self.scenario.pk]))
                total = page.get_by_label('Общий план G', exact=True)
                expect(total).to_have_value('30')
                total.fill('90.000003')
                page.get_by_role('button', name='Распределить', exact=True).click()
                expect(page.locator('[name="q_' + gid + '_' + cids[0] + '"]')).to_have_value('30.000001')
                expect(page.locator('[name="q_' + gid + '_' + cids[1] + '"]')).to_have_value('60.000002')
                expect(total).to_have_value('90.000003')
            finally:
                context.close()
                browser.close()
