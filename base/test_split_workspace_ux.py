import json
from copy import deepcopy
from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle as fixtures
from .split_workspace_ux import presentation

class WorkspaceUXTests(TestCase):
    def setUp(self):
        fixtures.SplitCycleTests.setUp(self)
        fixtures.SplitCycleTests.complete(self)
        self.url=reverse('split_scenario',args=[self.scenario.pk])

    def test_five_steps_and_composition_are_accessible(self):
        for tab in ['composition','plan','distribution','stock','checks','detail']:
            response=self.client.get(self.url,{'tab':tab})
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'Этапы подготовки плана')
            self.assertEqual(len(response.context['workflow_ux']['stages']),5)
        response=self.client.get(self.url,{'tab':'composition'})
        self.assertContains(response,'Добавить группы со стока')

    def stock_row(self,gid,buy):
        group=self.scenario.state['groups'][gid]
        entries=[['buy',buy],['channel',next(iter(group['channels']))],['forecast_mode','monthly']]
        for index,power in enumerate(group['powers'].values()):
            for field in ['stock','prior','incoming']:
                for kind in ['indoor','outdoor']:
                    entries.append([f'{field}_{kind}_{index}',str(power.get(f'{field}_{kind}') or '0')])
        return {'gid':gid,'entries':entries}

    def test_stock_batch_is_atomic_and_revision_protected(self):
        gid=next(iter(self.scenario.state['groups']))
        second=deepcopy(self.scenario.state['groups'][gid])
        self.scenario.state['groups']['other']=second
        self.scenario.save(update_fields=['state'])
        revision=self.scenario.revision
        post={'action':'save_stock_rows','revision':revision,'rows':json.dumps([self.stock_row(gid,'7'),self.stock_row('other','-1')])}
        self.assertEqual(self.client.post(self.url,post).status_code,400)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.revision,revision)
        post['rows']=json.dumps([self.stock_row(gid,'7'),self.stock_row('other','9')])
        self.assertEqual(self.client.post(self.url,post).status_code,200)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][gid]['buy'],'7')
        self.assertEqual(self.scenario.state['groups']['other']['buy'],'9')
        self.assertEqual(self.scenario.revision,revision+1)
        self.assertEqual(self.client.post(self.url,post).status_code,400)

    def test_issue_links_resolve_group_and_distribution_section(self):
        gid,group=next(iter(self.scenario.state['groups'].items()))
        result={'errors':[group['ref']['name']+' · 07: выберите действующую пару блоков.'],'warnings':[]}
        ux=presentation(self.scenario,result,'checks')
        self.assertIn('section=powers',ux['errors'][0]['url'])
        self.assertIn('power=07',ux['errors'][0]['url'])
        self.assertIn('group='+gid,ux['errors'][0]['url'])

    def test_quick_save_preserves_supplies_and_applies_edited_total_atomically(self):
        gid,group=next(iter(self.scenario.state['groups'].items()))
        post={'action':'save_plan_rows','revision':self.scenario.revision,'ddp_'+gid:group['ddp'],'buy_'+gid:'17','pending_group_plans':json.dumps([gid]),'group_plan_'+gid:'90'}
        for cid,line in group['channels'].items():
            post.update({'q_'+gid+'_'+cid:line['input'],'markup_'+gid+'_'+cid:line['markup'],'fact_'+gid+'_'+cid:line.get('fact') or ''})
        invalid={**post,'group_plan_'+gid:'-1'}
        self.assertEqual(self.client.post(self.url,invalid).status_code,400)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.revision,post['revision'])
        self.assertEqual(self.client.post(self.url,post).status_code,200)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][gid]['buy'],'17')
        self.assertEqual(sum(float(line['input']) for line in self.scenario.state['groups'][gid]['channels'].values()),90)
        self.assertEqual(self.client.post(self.url,post).status_code,400)
