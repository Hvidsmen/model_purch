import json
from copy import deepcopy
from django.test import TestCase
from . import test_split_power_grid as fixtures
from .split_workflow import changed_state


def setup_brands(test):
    fixtures.PowerGridTests.setUp(test)
    group=test.scenario.state['groups'][test.gid]
    group['ref'].setdefault('details',{})['brand']='Alpha'
    other=deepcopy(group);other['ref']['name']='Beta group';other['ref']['details']['brand']='Beta'
    test.scenario.state['groups']['other']=other;test.scenario.save(update_fields=['state'])


class BrandPageTests(TestCase):
    setUp=setup_brands

    def test_one_brand_is_rendered_per_page_on_all_large_tables(self):
        for tab in ('plan','stock','distribution'):
            for brand,gid in [('alpha',self.gid),('beta','other')]:
                response=self.client.get(self.url,{'tab':tab,'brand_page':brand})
                self.assertEqual(response.status_code,200)
                self.assertEqual(set(response.context['state']['groups']),{gid})
                self.assertEqual(response.context['brand_page']['total'],2)
                self.assertContains(response,'Страницы по брендам')
                if tab=='plan':
                    self.assertContains(response,'data-plan-group="'+gid+'"')
                    self.assertContains(response,'name="plan_group_ids"')
                if tab=='stock':self.assertEqual([r['id'] for r in response.context['stock_overview']['groups']],[gid])
                if tab=='distribution':self.assertEqual({r['gid'] for r in response.context['distribution_overview']['rows']},{gid})
        self.scenario.refresh_from_db();self.assertEqual(len(self.scenario.state['groups']),2)

    def test_default_focus_all_and_links(self):
        response=self.client.get(self.url,{'tab':'stock','filter_q':'keep'})
        pager=response.context['brand_page'];self.assertEqual(pager['key'],'alpha')
        self.assertIn('filter_q=keep',pager['next']);self.assertIn('brand_page=beta',pager['next'])
        response=self.client.get(self.url,{'tab':'stock','group':'other','focus':'1'})
        self.assertEqual(response.context['brand_page']['key'],'beta')
        response=self.client.get(self.url,{'tab':'plan','brand_page':'_all_'})
        self.assertEqual(len(response.context['state']['groups']),2)

    def test_native_plan_save_keeps_brand_page(self):
        group=self.scenario.state['groups']['other']
        fields={'action':'lines','revision':self.scenario.revision,'plan_group_ids':json.dumps(['other']),'ddp_other':'125'}
        for cid,line in group['channels'].items():
            for prefix,field in [('q','input'),('markup','markup'),('fact','fact')]:fields[prefix+'_other_'+cid]=str(line.get(field) or '')
        response=self.client.post(self.url+'?tab=plan&brand_page=beta',fields)
        self.assertEqual(response.status_code,302)
        self.assertIn('brand_page=beta',response.url)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups']['other']['ddp'],'125')

    def test_partial_plan_save_preserves_unrendered_brand_and_revision(self):
        other=deepcopy(self.scenario.state['groups']['other']);group=self.scenario.state['groups'][self.gid]
        fields={'plan_group_ids':json.dumps([self.gid]),'ddp_'+self.gid:'123'}
        for cid,line in group['channels'].items():
            for prefix,field in [('q','input'),('markup','markup'),('fact','fact')]:fields[prefix+'_'+self.gid+'_'+cid]=str(line.get(field) or '')
        response=self.client.post(self.url+'?tab=plan&brand_page=alpha',{'action':'save_plan_rows','revision':self.scenario.revision,
            'plan_payload':json.dumps(list(fields.items()))})
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups']['other'],other)
        self.assertEqual(self.scenario.state['groups'][self.gid]['ddp'],'123')
        for scope in ('[]','["missing"]',json.dumps([self.gid,self.gid])):
            from django.core.exceptions import ValidationError
            with self.assertRaises(ValidationError):changed_state(self.scenario,{'action':'lines','plan_group_ids':scope})

    def test_brand_and_all_brand_summaries_have_distinct_weighted_totals(self):
        from decimal import Decimal
        other=self.scenario.state['groups']['other'];other['ddp']='200'
        for line in other['channels'].values():line['markup']='2'
        self.scenario.save(update_fields=['state'])
        before=deepcopy(self.scenario.state)
        for brand,revenue in [('alpha',4500),('beta',12000)]:
            response=self.client.get(self.url,{'tab':'plan','brand_page':brand})
            self.assertEqual(response.context['matrix_totals']['total']['revenue'],Decimal(revenue))
            total=response.context['all_matrix_totals']['total']
            self.assertEqual(total['qty'],60)
            self.assertEqual(total['cost'],9000)
            self.assertEqual(total['revenue'],16500)
            self.assertAlmostEqual(total['markup'],Decimal(16500)/Decimal(9000))
            self.assertContains(response,'data-plan-summary="brand"',count=1)
            self.assertContains(response,'data-plan-summary="all"',count=1)
            self.assertGreater(response.context['all_plan_inventory']['total']['stock'],response.context['plan_inventory']['total']['stock'])
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)

    def test_history_summary_keeps_same_year_columns_for_brand_and_all(self):
        from .split_history import normalized
        groups=self.scenario.state['groups'];groups['other']['ref']['source_key']='beta-source'
        alpha=groups[self.gid]['ref']['source_key']
        channel=normalized(next(iter(self.scenario.state['channels'].values()))['name'])
        self.scenario.state['history']={'through':'2026-11-19','groups':[alpha,'beta-source'],
            'channels':[normalized(ch['name']) for ch in self.scenario.state['channels'].values()],
            'rows':[{'group':key,'channel':channel,'year':year,'month':1,'qty':str(qty),'revenue':'100','cost':'50'}
                    for key,year,qty in [(alpha,2026,10),('beta-source',2025,5)]]}
        self.scenario.save(update_fields=['state'])
        for brand in ('alpha','beta'):
            response=self.client.get(self.url,{'tab':'plan','brand_page':brand})
            self.assertEqual(response.context['history']['years'],[2025,2026])
            total=response.context['all_history_total']
            self.assertEqual([year['total']['qty'] for year in total['years']],[5,10])
            self.assertEqual(len(response.context['history_total']['years']),2)


    def test_multiple_brand_combinations_share_other_page_without_changing_references(self):
        groups=self.scenario.state['groups']
        groups['other']['ref']['details']['brand']='BOSCH, KENTATSU'
        third=deepcopy(groups['other']);third['ref']['details']['brand']='AIRWAVE, MIDEA, Other'
        groups['third']=third
        self.scenario.save(update_fields=['state'])
        before=deepcopy(self.scenario.state)
        for tab in ('plan','stock','distribution'):
            response=self.client.get(self.url,{'tab':tab,'brand_page':'прочее'})
            self.assertEqual(set(response.context['state']['groups']),{'other','third'})
            self.assertEqual([(p['name'],p['count']) for p in response.context['brand_page']['pages']],[('Alpha',1),('Прочее',2)])
            self.assertEqual(response.context['state']['groups']['other']['ref']['details']['brand'],'BOSCH, KENTATSU')
        response=self.client.get(self.url,{'tab':'plan','brand_page':'bosch, kentatsu'})
        self.assertEqual(response.context['brand_page']['name'],'Прочее')
        self.assertEqual(response.context['matrix_totals']['total']['qty'],60)
        self.assertEqual(response.context['all_matrix_totals']['total']['qty'],90)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)
