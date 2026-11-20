from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.contrib.auth.models import User
from django.urls import reverse

from . import test_split_cycle as fixtures
from .split_stock_workspace import candidate, presentation, propose, supply_proposal
from .sales_planning_group_service import group_key


class StockWorkspaceTests(TestCase):
    def setUp(self):
        fixtures.SplitCycleTests.setUp(self)
        fixtures.SplitCycleTests.complete(self)
        self.gid=str(self.group.pk)
        self.url=reverse('split_scenario',args=[self.scenario.pk])
        self.group_state=self.scenario.state['groups'][self.gid]
        self.group_state['target_stock_percent']='15'
        self.power=self.group_state['powers']['07']
        self.power.update(stock_indoor='300',stock_outdoor='300',incoming_indoor='200',incoming_outdoor='200')
        for line in self.group_state['channels'].values():line['input']='500'
        self.group_state['buy']='0'
        self.scenario.save(update_fields=['state'])

    def post(self,action,**data):
        return self.client.post(self.url,{'action':action,'revision':self.scenario.revision,**data})

    def test_balance_and_proposal_do_not_depend_on_prices_or_monthly_sales_completion(self):
        self.group_state['ddp']=None
        for line in self.group_state['channels'].values():line['markup']=None;line['months']={month:None for month in line['months']}
        row=presentation(self.scenario)['rows'][0]
        self.assertEqual(row['target'],D(150))
        self.assertEqual(row['closing'],D(-500))
        self.assertEqual(supply_proposal(self.scenario,self.group_state,row),D(650))

    def test_power_excess_does_not_cover_other_power_and_proposal_respects_saved_shares(self):
        self.group_state['target_stock_percent']='0'
        for line in self.group_state['channels'].values():line['input']='50'
        self.power.update(share='50',stock_indoor='0',stock_outdoor='0',incoming_indoor='0',incoming_outdoor='0')
        other=deepcopy(self.power);other.update(stock_indoor='1000',stock_outdoor='1000')
        self.group_state['powers']['09']=other
        row=presentation(self.scenario)['rows'][0]
        self.assertEqual(row['statuses']['supplies']['code'],'deficit')
        self.assertEqual(supply_proposal(self.scenario,self.group_state,row),D(100))
        self.group_state['buy']='100'
        row=presentation(self.scenario)['rows'][0]
        self.assertTrue(all(p['closing']>=0 for p in row['powers']))

    def test_target_shortage_is_visible_per_power_and_is_a_final_approval_warning(self):
        from .split_engine import evaluate
        from .split_workflow import approve
        self.group_state['buy']='550'
        result=evaluate(self.scenario)
        self.assertFalse(result['errors'],result['errors'])
        self.assertTrue(any('ниже целевого запаса' in warning for warning in result['warnings']))
        with self.assertRaisesMessage(ValidationError,'предупреждения'):
            approve(self.scenario,self.actor,self.scenario.revision,False)
        self.group_state['target_stock_percent']='20'
        for line in self.group_state['channels'].values():line['input']='50'
        self.group_state['buy']='0'
        self.power.update(share='50',stock_indoor='50',stock_outdoor='50',incoming_indoor='0',incoming_outdoor='0')
        other=deepcopy(self.power);other.update(stock_indoor='1000',stock_outdoor='1000')
        self.group_state['powers']['09']=other
        row=presentation(self.scenario)['rows'][0]
        self.assertGreater(row['closing'],row['target'])
        self.assertEqual(row['statuses']['supplies']['code'],'deficit')
        self.assertEqual(supply_proposal(self.scenario,self.group_state,row),D(20))

    def test_unknown_forecast_and_percent_are_not_zero_and_zero_plan_requires_confirmation(self):
        self.group_state['target_stock_percent']=None
        with self.assertRaisesMessage(ValidationError,'процент'):supply_proposal(self.scenario,self.group_state,presentation(self.scenario)['rows'][0])
        self.scenario.stock_date=date(2026,11,6)
        self.group_state['forecast']={'2026-11':None,'2026-12':None}
        row=presentation(self.scenario)['rows'][0]
        self.assertEqual(row['statuses']['opening']['code'],'missing')
        self.assertIsNone(row['opening']['totals']['sets'])
        for line in self.group_state['channels'].values():line['input']='0'
        self.assertIsNone(presentation(self.scenario)['rows'][0]['planned'])
        self.scenario.state['sales_reviewed_groups']=[self.gid]
        self.assertEqual(presentation(self.scenario)['rows'][0]['planned'],D(0))

    def test_unknown_pairs_and_forged_readonly_receipts_are_not_accepted(self):
        self.power['indoor']=None
        row=presentation(self.scenario)['rows'][0]
        self.assertEqual(row['statuses']['opening']['target'],'distribution')
        with self.assertRaisesMessage(ValidationError,'пары'):supply_proposal(self.scenario,self.group_state,row)
        self.power['incoming_sql_indoor']='200'
        with self.assertRaisesMessage(ValidationError,'источника'):
            candidate(self.scenario,json.dumps({self.gid:{'07:incoming_indoor':'999'}}))

    def test_monthly_forecast_patch_preserves_other_months_and_modes_are_explicit(self):
        self.scenario.stock_date=date(2026,11,6)
        self.group_state['forecast']={'2026-11':'10','2026-12':'20'}
        result=candidate(self.scenario,json.dumps({self.gid:{'month:2026-11':'15'}}))
        self.assertEqual(result['groups'][self.gid]['forecast'],{'2026-11':'15','2026-12':'20'})
        self.assertEqual(result['groups'][self.gid]['forecast_mode'],'monthly')
        with self.assertRaisesMessage(ValidationError,'один способ'):
            candidate(self.scenario,json.dumps({self.gid:{'month:2026-11':'15','forecast_group':'30'}}))

    def test_history_forecast_stages_without_saving_until_the_common_save(self):
        from .split_forecast_history import preview
        self.scenario.stock_date=date(2026,11,6)
        self.group_state['forecast_mode']='group';self.group_state['forecast_before_total']=None
        self.scenario.save(update_fields=['stock_date','state'])
        before=deepcopy(self.scenario.state)
        facts={(self.group_state['ref']['source_key'],m['name'].casefold()):{'qty':D(2),'unknown':False} for m in self.power['models']}
        with patch('base.split_forecast_history.source',return_value=(facts,['Дилеры'])):
            proposal=preview(self.scenario,{'targets':json.dumps([self.gid]),'year':'2025'})['rows'][0]
        response=self.post('preview_stock_workspace',forecast_tokens=json.dumps([proposal['token']]))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(D(response.json()['rows'][self.gid]['forecast']),2)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)
        response=self.post('save_stock_workspace',forecast_tokens=json.dumps([proposal['token']]))
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups'][self.gid]['forecast_mode'],'powers')
        self.assertEqual(D(self.scenario.state['groups'][self.gid]['powers']['07']['forecast_before']),2)

    def test_procurement_access_is_required_and_percent_import_token_keeps_source(self):
        from django.core.exceptions import PermissionDenied
        user=User.objects.create_user('no-purchases')
        with self.assertRaises(PermissionDenied):
            propose(self.scenario,{'kind':'purchase','groups':json.dumps([self.gid])},user)

    def test_patch_preserves_other_stock_forecast_and_plan_values_and_preview_is_read_only(self):
        before=deepcopy(self.scenario.state)
        response=self.post('preview_stock_workspace',changes=json.dumps({self.gid:{'buy':'650'}}))
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['rows'][self.gid]['closing'],'150.000000')
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)
        response=self.post('save_stock_workspace',changes=json.dumps({self.gid:{'buy':'650'}}))
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db()
        actual=self.scenario.state['groups'][self.gid]
        self.assertEqual(actual['channels'],before['groups'][self.gid]['channels'])
        self.assertEqual(actual['powers'],before['groups'][self.gid]['powers'])
        self.assertEqual(actual['forecast'],before['groups'][self.gid]['forecast'])

    def test_signed_proposal_requires_confirmation_and_cannot_be_reused_after_revision_change(self):
        response=self.post('propose_stock_workspace',kind='supplies',groups=json.dumps([self.gid]))
        self.assertEqual(response.status_code,200,response.content)
        proposal=response.json()['rows'][0];self.assertEqual(D(proposal['value']),650)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state['groups'][self.gid]['buy'],'0')
        response=self.post('save_stock_workspace',proposal_tokens=json.dumps([proposal['token']]))
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db();self.assertEqual(D(self.scenario.state['groups'][self.gid]['buy']),650)
        response=self.post('save_stock_workspace',proposal_tokens=json.dumps([proposal['token']]))
        self.assertEqual(response.status_code,400)

    def test_invalid_patch_is_atomic_and_readonly_scenario_and_foreign_groups_are_rejected(self):
        original=deepcopy(self.scenario.state)
        for changes in ({self.gid:{'buy':'-1'}},{'foreign':{'buy':'10'}},{self.gid:{'ddp':'100'}}):
            response=self.post('save_stock_workspace',changes=json.dumps(changes));self.assertEqual(response.status_code,400)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,original)
        self.scenario.status='approved';self.scenario.save(update_fields=['status'])
        response=self.post('save_stock_workspace',changes=json.dumps({self.gid:{'buy':'10'}}));self.assertEqual(response.status_code,400)

    def test_procurement_import_converts_fraction_and_keeps_manual_values_unless_explicitly_replaced(self):
        from model_purch.models import PGGoods,ScenarioModel
        purchase=ScenarioModel.objects.create(name='Purchase source',date_start_plan=date(2027,1,1),date_end_plan=date(2027,12,31))
        base={'kind':'purchase','groups':json.dumps([self.gid]),'purchase':str(purchase.pk),'only_empty':'0'}
        with patch.object(PGGoods.objects,'filter') as query:
            query.return_value.values_list.return_value=[(self.group.name,.15)]
            rows=propose(self.scenario,base,self.actor)
            self.assertEqual(D(rows[0]['value']),15)
            self.assertEqual(rows[0]['source']['id'],purchase.pk)
            preserved=propose(self.scenario,{**base,'only_empty':'1'},self.actor)
            self.assertIn('оставлен',preserved[0]['error'])
            query.return_value.values_list.return_value=[(self.group.name,.15),(self.group.name,.20)]
            self.assertIn('разные проценты',propose(self.scenario,base,self.actor)[0]['error'])

    def test_stock_steps_render_without_old_row_save_buttons_and_scope_inventory_load(self):
        for step in ('opening','targets','supplies'):
            response=self.client.get(self.url,{'tab':'stock','stock_step':step})
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'data-stock-workspace')
            self.assertNotContains(response,'>Сохранить поставки</button>')
            self.assertNotContains(response,'>Распределить план</button>')
        with patch('base.split_inventory.source_rows_many',return_value={self.gid:([],[])}) as source, patch('base.split_inventory.apply_inventory'):
            response=self.post('load_stock_workspace_inventory',groups=json.dumps([self.gid]))
            self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(set(source.call_args.args[1]),{self.gid})

class StockWorkspaceInventoryTests(TestCase):
    def setUp(self):
        from .test_split_inventory import SplitInventoryImportTests
        SplitInventoryImportTests.setUp(self)
        other=deepcopy(self.scenario.state['groups'][self.gid]);other['ref'].update(id='other',name='H',source_key='H')
        self.scenario.state['groups']['other']=other;self.scenario.save(update_fields=['state'])

    def test_loaded_receipts_and_manual_monthly_forecast_can_render_without_sales_plan(self):
        with patch('base.split_inventory.source_rows_many',return_value={self.gid:(self.stock,self.incomes),'other':([],[])}):
            response=self.client.post(self.url,{'action':'load_stock_workspace_inventory','revision':self.scenario.revision,'groups':json.dumps([self.gid,'other'])})
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db()
        response=self.client.post(self.url,{'action':'save_stock_workspace','revision':self.scenario.revision,'changes':json.dumps({self.gid:{'month:2026-11':'2','month:2026-12':'3'}})})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.client.get(self.url).status_code,200)



class StockWorkspaceDirectSaveTests(TestCase):
    setUp=StockWorkspaceTests.setUp
    post=StockWorkspaceTests.post

    def test_calculate_and_save_without_preview_preserves_revision_and_other_inputs(self):
        before=deepcopy(self.scenario.state)
        response=self.post('calculate_save_stock_workspace',kind='supplies',groups=json.dumps([self.gid]))
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(response.json()['count'],1)
        self.scenario.refresh_from_db();group=self.scenario.state['groups'][self.gid]
        self.assertEqual(D(group['buy']),650);self.assertEqual(group['channels'],before['groups'][self.gid]['channels'])
        self.assertEqual(self.post('calculate_save_stock_workspace',kind='supplies',groups=json.dumps([self.gid]),revision=0).status_code,400)

    def test_calculate_and_save_is_atomic_when_one_group_cannot_be_calculated(self):
        second=deepcopy(self.group_state);second['target_stock_percent']=None
        self.scenario.state['groups']['bad']=second;self.scenario.save(update_fields=['state'])
        before=deepcopy(self.scenario.state)
        response=self.post('calculate_save_stock_workspace',kind='supplies',groups=json.dumps([self.gid,'bad']))
        self.assertEqual(response.status_code,400,response.content)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)

    def test_import_directly_skips_existing_zero_percent_and_saves_missing_only(self):
        from model_purch.models import PGGoods,ScenarioModel
        purchase=ScenarioModel.objects.create(name='Direct source',date_start_plan=date(2027,1,1),date_end_plan=date(2027,12,31))
        self.group_state['target_stock_percent']='0';self.scenario.save(update_fields=['state'])
        with patch.object(PGGoods.objects,'filter') as query:
            query.return_value.values_list.return_value=[('G',D('.15'))]
            response=self.post('calculate_save_stock_workspace',kind='purchase',groups=json.dumps([self.gid]),purchase=str(purchase.pk),only_empty='1')
            self.assertEqual(response.status_code,200,response.content);self.assertEqual(response.json()['count'],0)
            response=self.post('calculate_save_stock_workspace',kind='purchase',groups=json.dumps([self.gid]),purchase=str(purchase.pk),only_empty='0')
            self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db();self.assertEqual(D(self.scenario.state['groups'][self.gid]['target_stock_percent']),15)
