import json
from copy import deepcopy
from django.test import TestCase,override_settings
from . import test_split_workspace_ux as fixtures


class LargePlanSaveTests(TestCase):
    setUp=fixtures.WorkspaceUXTests.setUp

    def fields(self):
        source=next(iter(self.scenario.state['groups'].values()))
        groups={f'g{i}':deepcopy(source) for i in range(124)}
        self.scenario.state['groups']=groups;self.scenario.save(update_fields=['state'])
        fields={}
        for gid,g in groups.items():
            fields['ddp_'+gid]=str(g['ddp']);fields['buy_'+gid]='7'
            fields['group_plan_'+gid]='90';fields['ddp_proposal_'+gid]=''
            for cid,line in g['channels'].items():
                fields['q_'+gid+'_'+cid]=str(line['input'])
                fields['markup_'+gid+'_'+cid]=str(line['markup'])
                fields['fact_'+gid+'_'+cid]=str(line.get('fact') or '')
                if line.get('lock_qty'):fields['lockq_'+gid+'_'+cid]='on'
                if line.get('lock_markup'):fields['lockm_'+gid+'_'+cid]='on'
        self.assertGreater(len(fields),1000)
        return fields

    @override_settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=1000)
    def test_124_group_form_exceeds_field_limit_but_compact_save_is_atomic(self):
        fields=self.fields();revision=self.scenario.revision
        # Reproduce the HTML 400 returned for the original wide form.
        response=self.client.post(self.url,{**fields,'action':'save_plan_rows','revision':revision})
        self.assertEqual(response.status_code,400)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.revision,revision)
        compact={'action':'save_plan_rows','revision':revision,'pending_group_plans':json.dumps(['g123']),
                 'plan_payload':json.dumps(list(fields.items()))}
        response=self.client.post(self.url,compact)
        self.assertEqual(response.status_code,200,response.content[:500]);self.assertTrue(response.json()['ok'])
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.revision,revision+1)
        self.assertEqual(self.scenario.state['groups']['g0']['buy'],'7')
        self.assertEqual(self.scenario.state['groups']['g123']['buy'],'7')
        self.assertEqual(sum(float(c['input']) for c in self.scenario.state['groups']['g123']['channels'].values()),90)
        self.assertEqual(self.client.post(self.url,compact).status_code,400)

    def test_invalid_payload_does_not_persist_and_inner_revision_cannot_override_outer(self):
        before=deepcopy(self.scenario.state);revision=self.scenario.revision
        for payload in ('{bad','{}','[1]','[["field",null]]'):
            response=self.client.post(self.url,{'action':'save_plan_rows','revision':revision,'plan_payload':payload})
            self.assertEqual(response.status_code,400);self.assertFalse(response.json()['ok'])
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before);self.assertEqual(self.scenario.revision,revision)
        from .split_plan_payload import unpack
        from django.http import QueryDict
        post=QueryDict('',mutable=True);post.update(action='save_plan_rows',revision='2',plan_payload=json.dumps([['revision','9'],['action','approve'],['x','1'],['x','2']]))
        result=unpack(post)
        self.assertEqual(result['revision'],'2');self.assertEqual(result['action'],'save_plan_rows')
        self.assertEqual(result.getlist('x'),['1','2'])
