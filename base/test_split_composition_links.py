import json,os,importlib.util,uuid
from copy import deepcopy
from datetime import date
from unittest import skipUnless
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from .test_sales_planning_link_picker import setup_picker
from .models import SalesPlanningChannel,UserProjectAccess
from .split_workflow import create_scenario


def setup(test):
    setup_picker(test)
    channel=SalesPlanningChannel.objects.create(name='Дилеры')
    test.scenario=create_scenario(test.actor,{'title':'Links','start':date(2027,1,1),'end':date(2027,12,31),'stock_date':date(2026,12,31),'groups':[test.group],'channels':[channel]})
    test.gid=str(test.group.pk);test.url=reverse('split_scenario',args=[test.scenario.pk])+'?tab=composition'


class CompositionLinksTests(TestCase):
    setUp=setup
    def post(self,patches,**extra):
        return self.client.post(self.url,{'action':'composition_edit','revision':self.scenario.revision,'channels':list(self.scenario.state['channels']),'composition_links':json.dumps(patches),**extra},HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    def values(self):return {'related_groups':[str(self.a.pk),str(self.old.pk)],'replacement_groups':[str(self.c.pk)]}
    def test_atomic_directory_and_snapshot_save_preserves_plan_and_other_scenarios(self):
        other=deepcopy(self.scenario);other.pk=None;other.family=uuid.uuid4();other.title='Other';other.save()
        before=deepcopy(self.scenario.state['groups'][self.gid]);self.group.notes='Keep';self.group.save()
        response=self.post({self.gid:self.values()});self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db();self.group.refresh_from_db();other.refresh_from_db()
        self.assertEqual(self.group.notes,'Keep')
        self.assertEqual(set(self.group.related_groups.values_list('pk',flat=True)),{self.a.pk,self.old.pk})
        self.assertEqual(self.group.replacement_groups.get().pk,self.c.pk)
        after=self.scenario.state['groups'][self.gid]
        self.assertEqual(after['channels'],before['channels']);self.assertEqual(after['powers'],before['powers'])
        self.assertEqual({r['pk'] for r in after['ref']['replacements']},{self.c.pk})
        self.assertEqual(other.state['groups'][self.gid],before)
        self.assertFalse(self.a.related_groups.filter(pk=self.group.pk).exists())
    def test_invalid_target_revision_approval_and_self_link_do_not_write(self):
        before=deepcopy(self.scenario.state)
        for patches,extra in [({self.gid:self.values()},{'revision':0}),({self.gid:{**self.values(),'related_groups':[self.gid]}},{}),({'bad':self.values()},{}),({self.gid:self.values(),'bad':self.values()}, {})]:
            self.assertEqual(self.post(patches,**extra).status_code,400)
            self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)
            self.assertEqual(list(self.group.related_groups.values_list('pk',flat=True)),[self.old.pk])
        self.scenario.status='approved';self.scenario.save(update_fields=['status'])
        self.assertEqual(self.post({self.gid:self.values()}).status_code,400)
    def test_planner_scope_rejects_foreign_links_preserving_existing(self):
        from django.contrib.auth.models import User
        from .models import SalesPlanningGoods
        planner=User.objects.create_user('planner');UserProjectAccess.objects.create(user=planner,project='sales_planning',role='Планировщик',sales_plan_groups=['RAC'])
        SalesPlanningGoods.objects.filter(planning_group='C other').update(sales_plan_group='VRF')
        self.group.replacement_groups.add(self.c);self.client.force_login(planner)
        response=self.client.post(self.url,{'action':'composition_links_catalog','revision':self.scenario.revision,'link_group':self.gid})
        self.assertEqual(response.status_code,200,response.content)
        self.assertNotIn(str(self.c.pk),[c['id'] for s in response.json()['sections'] for c in s['choices']])
        self.assertEqual(self.post({self.gid:self.values()}).status_code,400)
        self.assertEqual(self.post({self.gid:{'related_groups':[str(self.a.pk)],'replacement_groups':[]}}).status_code,200)
        self.assertTrue(self.group.replacement_groups.filter(pk=self.c.pk).exists())


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class CompositionLinksBrowserTests(StaticLiveServerTestCase):
    setUp=setup
    def test_edit_stage_cancel_and_common_save(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            try:
                page.goto(self.live_server_url+self.url)
                page.locator('[data-composition-links]').click()
                dialog=page.locator('.split-composition-links-dialog')
                related=dialog.locator('[data-link-kind=related_groups][value="'+str(self.a.pk)+'"]')
                expect(related).to_be_visible();related.check()
                dialog.locator('[data-links-close]').click()
                page.locator('[data-composition-links]').click();expect(related).not_to_be_checked()
                related.check();dialog.locator('[data-link-kind=replacement_groups][value="'+str(self.c.pk)+'"]').check()
                dialog.locator('[data-links-apply]').click()
                expect(page.locator('[data-composition-links]')).to_contain_text('Не сохранено')
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor() as pool:self.assertFalse(pool.submit(lambda:self.group.related_groups.filter(pk=self.a.pk).exists()).result())
                with page.expect_navigation():page.get_by_role('button',name='Сохранить состав плана',exact=True).click()
                page.locator('[data-composition-links]').click();expect(related).to_be_checked()
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.assertTrue(self.group.related_groups.filter(pk=self.a.pk).exists())
        self.assertTrue(self.group.replacement_groups.filter(pk=self.c.pk).exists())
