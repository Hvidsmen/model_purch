from copy import deepcopy
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle as fixtures
from .split_workflow import changed_state
from .split_stock_protection import protected_groups

class StockProtectionTests(TestCase):
    def setUp(self):
        fixtures.SplitCycleTests.setUp(self)
        fixtures.SplitCycleTests.complete(self)
        self.gid,self.group=next(iter(self.scenario.state['groups'].items()))
        self.url=reverse('split_scenario',args=[self.scenario.pk])

    def test_any_block_stock_locks_direct_and_composition_removal(self):
        power=next(iter(self.group['powers'].values()))
        power.update(stock_indoor='1',stock_outdoor='0')
        self.assertIn(self.gid,protected_groups(self.scenario.state))
        with self.assertRaisesMessage(ValidationError,'Нельзя удалить'):
            changed_state(self.scenario,{'action':'remove_group:'+self.gid})
        post=QueryDict('',mutable=True);post.update(action='composition',confirm='on')
        post.setlist('groups',[]);post.setlist('channels',list(self.scenario.state['channels']))
        with self.assertRaisesMessage(ValidationError,'Нельзя удалить'):
            changed_state(self.scenario,post,allow_empty=True)

    def test_selection_and_import_evidence_cannot_be_cleared_by_manual_zero(self):
        for power in self.group['powers'].values():power.update(stock_indoor='0',stock_outdoor='0')
        self.scenario.state['inventory_selection']={'checked_at':'2026-11-19T01:00:00+00:00','groups':[{'id':self.gid,'stock':True,'transit':False}]}
        with self.assertRaises(ValidationError):changed_state(self.scenario,{'action':'remove_group:'+self.gid})
        self.group['inventory_source']={'has_stock':False,'stock_models':0,'loaded_at':'2026-11-19T02:00:00+00:00'}
        self.assertNotIn(self.gid,protected_groups(self.scenario.state))
        self.group['inventory_source']['has_stock']=True
        self.assertIn(self.gid,protected_groups(self.scenario.state))
        self.group['inventory_source']={'stock_models':1}
        self.assertIn(self.gid,protected_groups(self.scenario.state))

    def test_zero_stock_group_can_be_removed_without_losing_other_state(self):
        for power in self.group['powers'].values():power.update(stock_indoor='0',stock_outdoor='0')
        self.scenario.state['inventory_selection']={'groups':[{'id':self.gid,'stock':False,'transit':True}]}
        state=changed_state(self.scenario,{'action':'remove_group:'+self.gid})
        self.assertNotIn(self.gid,state['groups'])
        self.assertIn(self.gid,state['archived_groups'])

    def test_http_rejection_keeps_revision_and_ui_explains_lock(self):
        original=deepcopy(self.scenario.state);revision=self.scenario.revision
        response=self.client.post(self.url,{'action':'remove_group:'+self.gid,'revision':revision})
        self.assertContains(response,'Нельзя удалить')
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original);self.assertEqual(self.scenario.revision,revision)
        self.assertContains(self.client.get(self.url),'Есть остатки · удаление недоступно')
        response=self.client.get(self.url,{'tab':'composition'})
        self.assertContains(response,'name="groups" value="'+self.gid+'"')
