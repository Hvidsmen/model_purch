from unittest.mock import patch
from django.urls import reverse
from django.test import TestCase
from .test_distribution_stages import ClosingWorkflowTests
from .services import premium_distribution as service


class DistributionWorkspaceTests(TestCase):
    setUp=ClosingWorkflowTests.setUp
    create=ClosingWorkflowTests.create
    changes=ClosingWorkflowTests.changes

    def page(self, year=2025, period=4):
        with patch('admin_motivation.premium_distribution_views.latest_calculation',return_value=None):
            return self.client.get(reverse('motivation_distributions'),{'year':year,'period':period})

    def test_single_period_registry_has_bound_coefficient_and_stage_action(self):
        response=self.page()
        self.assertEqual(response.context['selected_period'],4)
        self.assertTrue(response.context['skip_motivation_workflow'])
        self.assertContains(response,'form="pw-coefficients"')
        self.assertNotContains(response,'Зафиксировать фонды из расчёта')
        self.assertEqual(response.context['registry_counts']['waiting'],1)
        batch=self.create()
        response=self.page()
        row=response.context['registry_rows'][0]
        self.assertEqual(row['batch'].pk,batch.pk)
        self.assertEqual(row['sales_usd'],100)
        self.assertEqual(row['sales_rub'],100)
        self.assertEqual(row['stage_label'],'Продажи')
        self.assertNotContains(response,'Распределить продажи</button>')
        self.assertContains(response,'Продолжить')
        self.assertIsNone(self.page(period=3).context['registry_rows'][0]['batch'])

    def test_financier_sees_completed_only_and_no_edit_actions(self):
        batch=self.create()
        self.client.force_login(self.finance)
        self.assertFalse(self.page().context['registry_rows'])
        self.client.force_login(self.admin)
        batch=service.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='sales_complete')
        batch=service.update(self.admin,batch.pk,batch.revision,self.changes(batch),action='complete')
        self.client.force_login(self.finance)
        response=self.page()
        self.assertEqual(len(response.context['registry_rows']),1)
        self.assertContains(response,'Excel')
        self.assertNotContains(response,'Подтвердить множители подразделений')
        self.assertNotContains(response,'Подтвердить архивирование')

    def test_branch_scope_applies_to_registry_even_without_distributions(self):
        with patch('admin_motivation.services.distribution_workspace.branches',return_value=['Other']):
            self.assertFalse(self.page().context['registry_rows'])

    def test_browser_coefficient_controls_belong_to_confirmation_form(self):
        import importlib.util
        import os
        if os.environ.get('RUN_BROWSER_TESTS')!='1' or not importlib.util.find_spec('playwright'):
            self.skipTest('Browser checks are opt-in')
        from playwright.sync_api import sync_playwright
        html=self.page().content.decode()
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or None,headless=True)
            try:
                page=browser.new_page(viewport={'width':1440,'height':900})
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/*',lambda route:route.abort())
                page.set_content(html)
                field=page.locator('.pw-grid input[name^=coefficient_]')
                field.fill('0.95')
                self.assertIn('1',page.locator('#pw-change-count').inner_text())
                values=page.locator('#pw-coefficients').evaluate('form=>Object.fromEntries(new FormData(form))')
                self.assertEqual(values[field.get_attribute('name')],'0.95')
                self.assertEqual(values['year'],'2025')
                self.assertEqual(values['period'],'4')
                self.assertTrue(values['token'])
                self.assertEqual(page.locator('form[method=get] [name=period]').count(),1)
                menu=page.locator('.pw-branch-filter')
                menu.locator('summary').click()
                menu.locator('[data-pw-branch-search]').fill('Несуществующее подразделение')
                self.assertTrue(menu.locator('input[type=checkbox]').first.evaluate('el=>el.closest("label").hidden'))
                menu.locator('[data-pw-branch-search]').fill('')
                menu.locator('[data-pw-branch-all]').click()
                self.assertGreater(menu.locator('input:checked').count(),0)
                menu.locator('[data-pw-branch-clear]').click()
                self.assertEqual(menu.locator('input:checked').count(),0)
                menu.locator('summary').click()

                page.locator('.pw-grid tbody').evaluate('body=>{const row=body.rows[0];for(let i=0;i<50;i++)body.append(row.cloneNode(true));}')
                for position in (0,1800,100000):
                    page.evaluate('position=>window.scrollTo(0,position)',position)
                    page.wait_for_timeout(80)
                    bounds=page.locator('.pw-footer').bounding_box()
                    self.assertGreaterEqual(bounds['y'],0)
                    self.assertLessEqual(bounds['y']+bounds['height'],900)
                    self.assertGreaterEqual(bounds['x'],0)
                    self.assertLessEqual(bounds['x']+bounds['width'],1440)
                self.assertFalse(errors)
                page.set_viewport_size({'width':390,'height':844})
                self.assertTrue(page.locator('.pw-grid').evaluate('el=>el.scrollWidth>el.clientWidth'))
                page.wait_for_timeout(80)
                bounds=page.locator('.pw-footer').bounding_box()
                self.assertLessEqual(bounds['x']+bounds['width'],390)
                self.assertLessEqual(bounds['y']+bounds['height'],844)
            finally:
                browser.close()
