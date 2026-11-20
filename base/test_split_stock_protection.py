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

    def test_any_block_stock_requires_confirmation_for_direct_and_composition_removal(self):
        power=next(iter(self.group['powers'].values()))
        power.update(stock_indoor='1',stock_outdoor='0')
        self.assertIn(self.gid,protected_groups(self.scenario.state))
        with self.assertRaisesMessage(ValidationError,'Подтвердите исключение'):
            changed_state(self.scenario,{'action':'remove_group:'+self.gid})
        post=QueryDict('',mutable=True);post.update(action='composition',confirm='on')
        post.setlist('groups',[]);post.setlist('channels',list(self.scenario.state['channels']))
        with self.assertRaisesMessage(ValidationError,'Подтвердите исключение'):
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

    def test_http_rejection_keeps_revision_and_composition_allows_confirmation(self):
        original=deepcopy(self.scenario.state);revision=self.scenario.revision
        response=self.client.post(self.url,{'action':'remove_group:'+self.gid,'revision':revision})
        self.assertContains(response,'Подтвердите исключение')
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original);self.assertEqual(self.scenario.revision,revision)
        response=self.client.get(self.url,{'tab':'composition'})
        self.assertContains(response,'name="removed_groups" value="'+self.gid+'"')
        self.assertNotContains(response,'🔒 Удаление недоступно')
        self.assertContains(response,'компл. · все каналы')

    def test_confirmed_composition_exclusion_archives_stock_and_can_restore_it(self):
        import json
        before=deepcopy(self.group)
        data={'action':'composition_edit','revision':self.scenario.revision,'channels':list(self.scenario.state['channels']),
              'removed_groups':[self.gid],'confirmed_stock_removals':json.dumps([self.gid])}
        response=self.client.post(self.url+'?tab=composition',data,HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db()
        self.assertNotIn(self.gid,self.scenario.state['groups'])
        self.assertEqual(self.scenario.state['archived_groups'][self.gid],before)
        response=self.client.post(self.url+'?tab=composition',{'action':'composition_edit','revision':self.scenario.revision,
            'channels':list(self.scenario.state['channels']),'added_groups':[self.gid]},HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['powers'],before['powers'])
        self.assertEqual(self.scenario.state['groups'][self.gid]['channels'],before['channels'])

    def test_initial_stock_is_common_to_channels_and_unknown_stays_unknown(self):
        from decimal import Decimal
        response=self.client.get(self.url,{'tab':'composition'})
        self.assertEqual(response.context['composition_rows'][0]['opening_stock']['quantity'],Decimal(100))
        power=next(iter(self.group['powers'].values()));power['stock_outdoor']=None
        self.scenario.save(update_fields=['state'])
        response=self.client.get(self.url,{'tab':'composition'})
        self.assertIsNone(response.context['composition_rows'][0]['opening_stock']['quantity'])
        self.assertContains(response,'Начальный запас не рассчитан; наличие товара подтверждено')

    def test_future_opening_stock_also_requires_confirmation_and_wrong_ids_do_not_acknowledge(self):
        from .split_stock_protection import validate_removal
        self.scenario.stock_date=self.scenario.stock_date.replace(month=11,day=1)
        self.group.update(forecast_mode='powers')
        for power in self.group['powers'].values():
            power.update(stock_indoor='0',stock_outdoor='0',prior_indoor='10',prior_outdoor='10',forecast_before='0')
        self.assertNotIn(self.gid,protected_groups(self.scenario.state))
        for confirmation in ['[]','["other"]','true','bad']:
            with self.assertRaisesMessage(ValidationError,'Подтвердите исключение'):
                validate_removal(self.scenario,[self.gid],confirmation)
        import json
        validate_removal(self.scenario,[self.gid],json.dumps([self.gid]))
