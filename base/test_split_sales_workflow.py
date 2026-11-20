from copy import deepcopy
from decimal import Decimal
import importlib.util,os
from unittest import skipUnless
from django.test import TestCase
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_split_brand_pages import setup_brands
from .split_brand_summary import candidate

class SalesWorkflowTests(TestCase):
    setUp=setup_brands
    def call(self,action,**values):
        return self.client.post(self.url+'?tab=plan&sales_view=brands',{'action':action,'revision':self.scenario.revision,**values})
    def test_summary_preview_is_read_only_and_confirmation_is_revision_bound(self):
        response=self.client.get(self.url,{'tab':'plan','sales_view':'brands'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.context['sales_summary']['rows']),2)
        cid=next(iter(self.scenario.state['channels']));before=deepcopy(self.scenario.state)
        preview=self.call('brand_preview',brand='alpha',channel=cid,quantity='20',markup='',method='saved')
        self.assertEqual(preview.status_code,200,preview.content);result=preview.json()
        self.assertEqual(Decimal(result['before']['qty']),10);self.assertEqual(Decimal(result['after']['qty']),20)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)
        self.assertEqual(self.call('brand_apply',token=result['token']).status_code,400)
        applied=self.call('brand_apply',token=result['token'],confirm='on');self.assertEqual(applied.status_code,200,applied.content)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups']['other'],before['groups']['other'])
        self.assertEqual(Decimal(self.scenario.state['groups'][self.gid]['channels'][cid]['input']),20)
        self.assertEqual(self.call('brand_apply',token=result['token'],confirm='on').status_code,400)
    def test_markup_only_and_equal_zero_base_preserve_locks_and_other_brands(self):
        cid=next(iter(self.scenario.state['channels']))
        state,result=candidate(self.scenario,{'brand':'alpha','channel':cid,'quantity':'','markup':'2','method':'saved'})
        self.assertEqual(state['groups'][self.gid]['channels'][cid]['input'],'10')
        self.assertEqual(Decimal(state['groups'][self.gid]['channels'][cid]['markup']),2)
        for line in self.scenario.state['groups'][self.gid]['channels'].values():line['input']='0'
        state,result=candidate(self.scenario,{'brand':'alpha','channel':cid,'quantity':'20','markup':'','method':'equal'})
        self.assertEqual(Decimal(state['groups'][self.gid]['channels'][cid]['input']),20)
        self.scenario.state['groups'][self.gid]['channels'][cid]['lock_qty']=True
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):candidate(self.scenario,{'brand':'alpha','channel':cid,'quantity':'20','markup':'','method':'equal'})
    def test_other_bucket_does_not_duplicate_groups_and_approved_is_read_only(self):
        self.scenario.state['groups']['other']['ref']['details']['brand']='BOSCH, KENTATSU'
        self.scenario.save(update_fields=['state'])
        response=self.client.get(self.url,{'tab':'plan','sales_view':'brands'})
        self.assertEqual([row['name'] for row in response.context['sales_summary']['rows']],['Alpha','Прочее'])
        self.scenario.status='approved';self.scenario.save(update_fields=['status'])
        self.assertEqual(self.call('brand_preview',brand='прочее',channel=next(iter(self.scenario.state['channels'])),quantity='20',method='equal').status_code,400)
    def test_equal_group_plan_can_start_without_saved_channel_shares(self):
        from .split_group_plan import distribute
        for line in self.scenario.state['groups'][self.gid]['channels'].values():line['input']='0'
        state=distribute(self.scenario,deepcopy(self.scenario.state),self.gid,'40','equal')
        self.assertEqual([Decimal(line['input']) for line in state['groups'][self.gid]['channels'].values()],[20,20])

    def test_mixed_brand_adjustment_respects_fixed_volume_and_markup(self):
        groups=self.scenario.state['groups'];groups['other']['ref']['details']['brand']='BOSCH, KENTATSU'
        groups['third']=deepcopy(groups['other']);groups['third']['ref']['details']['brand']='MIDEA, Other'
        cid=next(iter(self.scenario.state['channels']))
        groups['other']['channels'][cid].update(lock_qty=True,lock_markup=True)
        before=deepcopy(groups[self.gid])
        state,result=candidate(self.scenario,{'brand':'прочее','channel':cid,'quantity':'30','markup':'2','method':'saved'})
        self.assertEqual(state['groups'][self.gid],before)
        fixed=state['groups']['other']['channels'][cid];free=state['groups']['third']['channels'][cid]
        self.assertEqual(Decimal(fixed['input']),10);self.assertEqual(Decimal(fixed['markup']),Decimal('1.5'))
        self.assertEqual(Decimal(free['input']),20);self.assertEqual(Decimal(free['markup']),Decimal('2.25'))
        self.assertEqual(free['months'],groups['third']['channels'][cid]['months'])
        self.assertEqual(result['locked_qty'],1);self.assertEqual(result['locked_markup'],1)

    def test_brand_total_can_change_all_channels_without_touching_other_brand(self):
        before=deepcopy(self.scenario.state['groups']['other'])
        state,result=candidate(self.scenario,{'brand':'alpha','channel':'_all_','quantity':'60','markup':'2','method':'saved'})
        self.assertEqual(state['groups']['other'],before)
        self.assertEqual(result['before']['qty'],30)
        self.assertEqual(result['after']['qty'],60)
        self.assertEqual(result['after']['revenue'],12000)
        self.assertEqual(result['after']['markup'],2)
        self.assertEqual([Decimal(line['input']) for line in state['groups'][self.gid]['channels'].values()],[20,40])

    def test_saved_zero_is_distinguished_from_initial_zero(self):
        from .split_workspace_ux import presentation
        from .split_engine import evaluate
        from .split_workflow import changed_state
        from django.http import QueryDict
        for line in self.scenario.state['groups'][self.gid]['channels'].values():line['input']='0'
        before=presentation(self.scenario,evaluate(self.scenario),'plan')
        stage=next(row for row in before['stages'] if row['key']=='plan')
        self.assertIn('1 из 2',stage['detail'])
        post=QueryDict('',mutable=True);post.update(action='lines',plan_group_ids='["'+self.gid+'"]')
        post['ddp_'+self.gid]='100'
        for cid,line in self.scenario.state['groups'][self.gid]['channels'].items():post['q_'+self.gid+'_'+cid]='0'
        draft=deepcopy(self.scenario);draft.state=changed_state(self.scenario,post)
        stage=next(row for row in presentation(draft,evaluate(draft),'plan')['stages'] if row['key']=='plan')
        self.assertIn('2 из 2',stage['detail'])

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Requires Chromium')
class SalesWorkflowBrowserTests(StaticLiveServerTestCase):
    setUp=setup_brands
    def test_groups_toolbar_and_confirmed_brand_adjustment(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(self.live_server_url+self.url+'?tab=plan')
                expect(page.locator('.split-plan-matrix')).to_have_attribute('data-table-view','flat')
                expect(page.get_by_role('button',name='Заполнить DDP',exact=True)).to_be_visible()
                expect(page.locator('.split-stock-protected')).to_have_count(0)
                expect(page.locator('[data-plan-group] .split-remove')).to_be_disabled()
                page.locator('[data-sales-search]').fill('not found')
                expect(page.locator('[data-plan-group]')).not_to_be_visible()
                page.locator('[data-sales-search]').fill('')
                page.screenshot(path='/tmp/split-sales-groups.png',full_page=True)
                page.get_by_role('link',name='Сводка по брендам',exact=True).click()
                page.locator('[data-sales-expand]').first.click()
                page.locator('[data-brand-adjust]:not([data-channel="_all_"])').first.click()
                dialog=page.get_by_role('dialog',name='Изменить итог бренда',exact=True)
                expect(dialog).to_be_visible();dialog.locator('[name=quantity]').fill('25')
                dialog.get_by_role('button',name='Проверить изменения',exact=True).click()
                expect(dialog.locator('[data-sales-adjust-preview]')).to_be_visible()
                expect(dialog.locator('[data-sales-adjust-apply]')).to_be_visible()
                page.screenshot(path='/tmp/split-sales-preview.png',full_page=True)
                with page.expect_navigation():dialog.get_by_role('button',name='Применить изменения',exact=True).click()
                expect(page.locator('.split-sales-adjust-dialog')).not_to_be_visible()
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.scenario.refresh_from_db();cid=next(iter(self.scenario.state['channels']))
        self.assertEqual(Decimal(self.scenario.state['groups'][self.gid]['channels'][cid]['input']),25)

    def test_equal_group_plan_save_handles_method_control_and_ctrl_s(self):
        from playwright.sync_api import sync_playwright,expect
        for line in self.scenario.state['groups'][self.gid]['channels'].values():line['input']='0'
        self.scenario.save(update_fields=['state'])
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();page.goto(self.live_server_url+self.url+'?tab=plan')
                page.get_by_label('Общий план G',exact=True).fill('40')
                page.get_by_label('Способ распределения G',exact=True).select_option('equal')
                with page.expect_navigation():page.keyboard.press('Control+s')
                for cid in self.scenario.state['channels']:
                    expect(page.locator('[name="q_'+self.gid+'_'+cid+'"]')).to_have_value('20.000000')
            finally:context.close();browser.close()

    def test_channel_multiselect_hides_columns_and_preserves_saved_inputs(self):
        from playwright.sync_api import sync_playwright,expect
        cid=next(iter(self.scenario.state['channels']))
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            try:
                context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(self.live_server_url+self.url+'?tab=plan')
                field=page.locator('[name="q_'+self.gid+'_'+cid+'"]');field.fill('37')
                picker=page.locator('[data-sales-channel-filter]');picker.locator('summary').click()
                picker.locator('[data-sales-channel-choice="'+cid+'"]').uncheck()
                expect(field).not_to_be_visible()
                expect(page.locator('[data-plan-channel="'+cid+'"]').first).not_to_be_visible()
                self.assertEqual(page.locator('[data-sales-channel="'+cid+'"]:visible').count(),0)
                picker.locator('summary').click()
                with page.expect_navigation():page.keyboard.press('Control+s')
                expect(page.locator('[name="q_'+self.gid+'_'+cid+'"]')).to_have_value('37')
                expect(page.locator('[name="q_'+self.gid+'_'+cid+'"]')).not_to_be_visible()
                picker=page.locator('[data-sales-channel-filter]');picker.locator('summary').click()
                picker.locator('[data-sales-channels-all]').click()
                expect(page.locator('[name="q_'+self.gid+'_'+cid+'"]')).to_be_visible()
                picker.locator('[data-sales-channels-none]').click()
                self.assertEqual(page.locator('[data-plan-channel]:visible').count(),0)
                expect(page.locator('.split-plan-matrix')).to_be_visible()
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        self.scenario.refresh_from_db()
        self.assertEqual(Decimal(self.scenario.state['groups'][self.gid]['channels'][cid]['input']),37)

    def test_brand_and_channel_dropdowns_are_not_clipped_by_toolbar(self):
        from playwright.sync_api import sync_playwright,expect
        with sync_playwright() as driver:
            browser=driver.chromium.launch(executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE'),headless=True)
            context=browser.new_context(viewport={'width':1700,'height':1100})
            context.add_cookies([{'name':'sessionid','value':self.client.cookies['sessionid'].value,'url':self.live_server_url}])
            page=context.new_page()
            try:
                for tab,step in [('composition',''),('plan',''),('distribution',''),('stock','opening'),('stock','targets'),('stock','supplies')]:
                    page.goto(self.live_server_url+self.url+'?tab='+tab+'&stock_step='+step)
                    for selector,choice in [('[data-brand-multiselect]','[data-brand-choice]')]+([('[data-sales-channel-filter]','[data-sales-channel-choice]')] if tab=='plan' else []):
                        picker=page.locator(selector);summary=picker.locator('summary')
                        summary.evaluate("node=>node.scrollIntoView({block:'start'})")
                        summary.click()
                        checkbox=picker.locator(choice).first;expect(checkbox).to_be_visible()
                        self.assertTrue(checkbox.evaluate("node=>{const r=node.getBoundingClientRect();return node===document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)}"),tab+' '+selector+' is clipped')
                        if tab=='plan':
                            panel=page.locator('.split-sales-toolbar')
                            self.assertEqual(panel.evaluate('node=>getComputedStyle(node).overflowY'),'visible')
                            page.screenshot(path='/tmp/split-dropdown-'+('brands' if 'brand' in selector else 'channels')+'.png')
                        if tab=='stock':
                            self.assertEqual(page.locator('.split-stock-controls').evaluate('node=>getComputedStyle(node).overflowY'),'visible')
                            self.assertEqual(checkbox.evaluate('node=>getComputedStyle(node).minHeight'),'0px')
                            page.screenshot(path='/tmp/stock-brands-'+step+'.png')
                        summary.click()
            finally:context.close();browser.close()
