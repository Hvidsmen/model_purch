from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import json
from unittest.mock import Mock,patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from . import test_split_power_grid as fixtures
from .split_ddp import preview,fact,catalog
from .split_workflow import changed_state
from model_purch.models import PGGoods,ScenarioModel,KindPurch


class DdpTests(TestCase):
    def setUp(self):
        fixtures.PowerGridTests.setUp(self)
        self.costs={}
        for model,qty,cost in [('IN',10,300),('OUT',10,700),('NINE indoor',20,800),('NINE outdoor',20,3200)]:
            self.costs[(self.group.source_key,model.casefold())]={'qty':D(qty),'cost':D(cost),'unknown':False}
        self.metadata={'year':2025,'from':'2025-01-01','through':'2025-12-31','source':'Fact DDP'}

    def proposal(self,**extra):
        post={'targets':json.dumps([self.gid]),'source':'fact','year':'2025','surcharge':'8',**extra}
        with patch('base.split_ddp.fact',return_value=(self.costs,self.metadata)):
            return preview(self.scenario,post)['rows'][0]

    def lines(self,proposal):
        group=self.scenario.state['groups'][self.gid]
        post={'action':'lines','ddp_'+self.gid:proposal['proposed'],'ddp_proposal_'+self.gid:proposal['token']}
        for cid,line in group['channels'].items():
            for prefix,field in [('q','input'),('fact','fact'),('markup','markup')]:post[prefix+'_'+self.gid+'_'+cid]=line[field]
        return post

    def test_annual_pair_cost_and_power_mix_are_decimal_and_preview_is_read_only(self):
        before=deepcopy(self.scenario.state);row=self.proposal()
        self.assertEqual(row['proposed'],'162.000000')
        self.assertEqual([p['ddp'] for p in row['powers']],['108.000000','216.000000'])
        self.assertEqual(self.scenario.state,before)
        state=changed_state(self.scenario,self.lines(row));group=state['groups'][self.gid]
        self.assertEqual(group['channels'],before['groups'][self.gid]['channels'])
        self.assertEqual(group['powers']['09']['factor'],'2.000000')
        self.assertEqual(group['powers']['07']['indoor_cost_percent'],'30.000000')
        self.assertEqual(group['ddp_source']['year'],2025)
        self.assertEqual(self.scenario.state,before)

    def test_missing_model_cost_unknown_quantity_and_incomplete_shares_do_not_become_zero(self):
        for mutate in ('missing','unknown','zero','shares'):
            with self.subTest(mutate=mutate):
                original=deepcopy(self.costs);old=deepcopy(self.scenario.state)
                key=(self.group.source_key,'out')
                if mutate=='missing':self.costs.pop(key)
                elif mutate=='unknown':self.costs[key]['unknown']=True
                elif mutate=='zero':self.costs[key]['qty']=0
                else:self.scenario.state['groups'][self.gid]['powers']['09']['share']='49'
                row=self.proposal();self.assertTrue(row['error']);self.assertIsNone(row['proposed']);self.assertNotIn('token',row)
                self.costs=original;self.scenario.state=old

    def test_signed_proposals_reject_tampering_other_scenario_and_changed_shares(self):
        row=self.proposal();post=self.lines(row)
        post['ddp_proposal_'+self.gid]+='tamper'
        with self.assertRaises(ValidationError):changed_state(self.scenario,post)
        post=self.lines(row);self.scenario.state['groups'][self.gid]['powers']['09']['share']='49'
        with self.assertRaises(ValidationError):changed_state(self.scenario,post)

    def test_manual_edit_after_preview_keeps_manual_price_and_old_power_parameters(self):
        row=self.proposal();post=self.lines(row);post['ddp_'+self.gid]='321'
        state=changed_state(self.scenario,post)
        self.assertEqual(state['groups'][self.gid]['ddp'],'321')
        self.assertEqual(state['groups'][self.gid]['powers'],self.scenario.state['groups'][self.gid]['powers'])
        self.assertNotIn('ddp_source',state['groups'][self.gid])

    def purchase(self,name='G',price=300):
        scenario=ScenarioModel.objects.create(name='Закупки',date_start_plan='2027-01-01',date_end_plan='2027-12-31')
        kind=KindPurch.objects.create(name='Import')
        good=PGGoods.objects.create(scenario_plan=scenario,planning_group=name,planning_sales='Sales',group_goods='Group',brand='Brand',
            kind_purch=kind,volume=1,exw_usd=price,stock_cnt_day=30,percent_stock_end=20)
        PGGoods.objects.filter(pk=good.pk).update(ddp_usd=price+25)
        return scenario,good

    def test_procurement_uses_final_ddp_not_exw_and_matches_groups_without_fake_power_details(self):
        purchase,good=self.purchase()
        rows=catalog(self.scenario,str(purchase.pk));self.assertEqual(rows['groups'][0]['matches'],[str(good.pk)])
        row=preview(self.scenario,{'targets':json.dumps([self.gid]),'source':'purchase','purchase':str(purchase.pk),'mapping':json.dumps({self.gid:str(good.pk)})})['rows'][0]
        self.assertEqual(row['proposed'],'325.000000');self.assertEqual(row['powers'],[])
        state=changed_state(self.scenario,self.lines(row))
        self.assertEqual(state['groups'][self.gid]['powers'],self.scenario.state['groups'][self.gid]['powers'])
        self.assertEqual(state['groups'][self.gid]['ddp_source']['basis'],'group')

    def test_procurement_power_mapping_is_explicit_and_cannot_borrow_another_scenario(self):
        purchase,good=self.purchase();other,foreign=self.purchase('Other',500)
        post={'targets':json.dumps([self.gid]),'source':'purchase','purchase':str(purchase.pk),'mapping':json.dumps({self.gid:{'07':str(good.pk),'09':str(good.pk)}})}
        row=preview(self.scenario,post)['rows'][0]
        self.assertEqual(row['proposed'],'325.000000');self.assertEqual(len(row['powers']),2)
        post['mapping']=json.dumps({self.gid:{'07':str(good.pk),'09':str(foreign.pk)}})
        self.assertTrue(preview(self.scenario,post)['rows'][0]['error'])

    def test_yearly_source_dates_nulls_and_connection_cleanup(self):
        connection,cursor=Mock(),Mock();cursor.fetchall.return_value=[('G','IN',D(10),D(300),0)]
        with patch('base.split_ddp.connect_database',return_value=(connection,cursor)):
            values,meta=fact(2025)
        query=cursor.execute.call_args.args
        self.assertEqual(query[1:3],(date(2025,1,1),date(2026,1,1)))
        self.assertIn('AmountExwAEUSD',query[0]);self.assertEqual(meta['through'],'2025-12-31')
        connection.close.assert_called_once();cursor.close.assert_called_once()
        self.assertEqual(values[(self.group.source_key,'in')]['cost'],D(300))
        cursor.fetchall.return_value=[('G','IN',D(10),None,1)]
        with patch('base.split_ddp.connect_database',return_value=(connection,cursor)):
            values,_=fact(2025)
        self.assertTrue(values[(self.group.source_key,'in')]['unknown'])

    def test_preview_endpoint_checks_revision_and_never_persists(self):
        state=deepcopy(self.scenario.state)
        with patch('base.split_ddp.fact',return_value=(self.costs,self.metadata)):
            response=self.client.post(self.url,{'action':'preview_ddp','revision':self.scenario.revision,'targets':json.dumps([self.gid]),'source':'fact','year':'2025','surcharge':'8'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['rows'][0]['proposed'],'162.000000')
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,state)
        self.assertEqual(self.client.post(self.url,{'action':'preview_ddp','revision':0}).status_code,400)
        self.assertEqual(self.client.post(self.url,{'action':'ddp_catalog','revision':self.scenario.revision,'purchase':'bad'}).status_code,400)
