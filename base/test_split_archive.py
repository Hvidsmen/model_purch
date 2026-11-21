from copy import deepcopy
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from django.contrib.auth.models import User
from . import test_split_cycle as fixtures
from .split_archive import set_archived


class ArchiveTests(TestCase):
    setUp=fixtures.SplitCycleTests.setUp

    def test_archive_restore_filter_revision_and_data_preservation(self):
        original=deepcopy(self.scenario.state);revision=self.scenario.revision
        url=reverse('sales_planning_split')
        response=self.client.post(url,{'action':'archive_scenario','scenario_id':self.scenario.pk,'revision':revision})
        self.assertEqual(response.status_code,302)
        self.scenario.refresh_from_db()
        self.assertIsNotNone(self.scenario.archived_at)
        self.assertEqual(self.scenario.state,original)
        self.assertEqual(self.scenario.status,'draft')
        self.assertEqual(self.scenario.revision,revision+1)
        self.assertNotIn(self.scenario.pk,[obj.pk for obj in self.client.get(url).context['page']])
        self.assertIn(self.scenario.pk,[obj.pk for obj in self.client.get(url,{'archived':'1'}).context['page']])
        self.assertContains(self.client.get(reverse('split_scenario',args=[self.scenario.pk])),'Восстановить из архива')
        set_archived(self.scenario,self.actor,self.scenario.revision,False)
        self.assertIsNone(self.scenario.archived_at)
        self.assertEqual(self.scenario.state,original)
        self.assertEqual(self.scenario.events.filter(action__contains='архив').count(),2)

    def test_approved_plan_can_be_archived_without_modifying_approval_or_data(self):
        from .split_workflow import approve
        fixtures.SplitCycleTests.complete(self)
        approve(self.scenario,self.actor,self.scenario.revision,False)
        self.scenario.refresh_from_db();original=deepcopy(self.scenario.state);approved=self.scenario.approved_at
        set_archived(self.scenario,self.actor,self.scenario.revision,True)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.status,'approved');self.assertEqual(self.scenario.approved_at,approved)
        self.assertEqual(self.scenario.state,original)
        with self.assertRaises(ValidationError):set_archived(self.scenario,self.actor,self.scenario.revision-1,False)
        outsider=User.objects.create_user('archive-outsider')
        with self.assertRaises(PermissionDenied):set_archived(self.scenario,outsider,self.scenario.revision,False)

    def test_workspace_actions_support_ajax_and_restore(self):
        url=reverse('split_scenario',args=[self.scenario.pk])
        for action in ('archive_scenario','restore_scenario'):
            response=self.client.post(url,{'action':action,'revision':self.scenario.revision})
            self.assertEqual(response.status_code,200,response.content)
            self.assertTrue(response.json()['ok']);self.scenario.refresh_from_db()


import importlib.util,os
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class ArchiveBrowserTests(StaticLiveServerTestCase):
    setUp=fixtures.SplitCycleTests.setUp

    def test_archive_restore_in_workspace_without_document_reload(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            page=browser.new_page()
            page.context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page.goto(self.live_server_url+reverse('split_scenario',args=[self.scenario.pk])+'?tab=checks')
            page.evaluate('window.archiveSentinel=7')
            page.get_by_role('button',name='В архив',exact=True).click()
            expect(page.get_by_role('button',name='Восстановить из архива',exact=True)).to_be_visible()
            self.assertEqual(page.evaluate('window.archiveSentinel'),7)
            page.get_by_role('button',name='Восстановить из архива',exact=True).click()
            expect(page.get_by_role('button',name='В архив',exact=True)).to_be_visible()
            self.assertEqual(page.evaluate('window.archiveSentinel'),7)
            browser.close()
