from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from . import test_split_cycle
from .split_distribution_history import period, load_basis, proposal
from .split_workflow import changed_state, approve


class DistributionHistoryTests(TestCase):
    def setUp(self):
        test_split_cycle.SplitCycleTests.setUp(self)
        self.scenario.stock_date = date(2026,11,6)
        self.scenario.save()
        self.gid = str(self.group.pk)
        self.cid = str(self.channels[0].pk)
        self.group_state = self.scenario.state['groups'][self.gid]
        self.url = reverse('split_scenario',args=[self.scenario.pk])
        cache.clear()

    def basis(self):
        return {'first_year':2022,'last_year':2026,'through':'2026-10-31','stock_date':'2026-11-06',
                'loaded_at':'2026-11-10T00:00:00Z','rows':[
                    {'channel':'дилеры','year':year,'month':month,'model':'in','qty':'100'}
                    for month in (1,12) for year in range(2022,2027 if month == 1 else 2026)]}

    def test_cutoff_uses_stock_date_and_includes_month_only_at_month_end(self):
        self.assertEqual(period(self.scenario),(date(2022,1,1),date(2026,11,1)))
        self.scenario.stock_date = date(2026,11,30)
        self.assertEqual(period(self.scenario)[1],date(2026,12,1))
        self.scenario.stock_date = date(2026,11,29)
        self.assertEqual(period(self.scenario)[1],date(2026,11,1))
        self.scenario.stock_date = date(2026,12,31)
        self.assertEqual(period(self.scenario)[1],date(2027,1,1))
        self.scenario.stock_date = date(2024,2,29)
        self.assertEqual(period(self.scenario)[1],date(2024,3,1))

    def test_incomplete_year_is_normalized_by_observation_count(self):
        values = proposal(self.scenario,self.group_state,'Дилеры',self.basis(),'months')
        self.assertEqual(Decimal(values['2027-01']),50)
        self.assertEqual(Decimal(values['2027-12']),50)
        self.assertEqual(Decimal(values['2027-02']),0)
        self.assertEqual(sum(map(Decimal,values.values())),100)
        with self.assertRaises(ValidationError):
            proposal(self.scenario,self.group_state,'Розница',self.basis(),'months')

    def test_partial_planned_months_are_prorated_and_rounding_is_exact(self):
        self.scenario.start,self.scenario.end = date(2027,1,16),date(2027,12,31)
        values = proposal(self.scenario,self.group_state,'Дилеры',self.basis(),'months')
        self.assertLess(Decimal(values['2027-01']),Decimal(values['2027-12']))
        self.assertEqual(sum(map(Decimal,values.values())),100)

    def test_power_mix_uses_all_channels_and_rejects_unknown_models(self):
        group = deepcopy(self.group_state)
        group['powers'] = {'07':{'active':True,'models':[{'name':'IN'}]},
                           '09':{'active':True,'models':[{'name':'BIG'}]}}
        basis = self.basis()
        basis['rows'] = [{'channel':'дилеры','year':2025,'month':1,'model':'in','qty':'75'},
                         {'channel':'розница','year':2025,'month':1,'model':'big','qty':'25'}]
        values = proposal(self.scenario,group,'Дилеры',basis,'powers')
        self.assertEqual({key:Decimal(value) for key,value in values.items()},{'07':75,'09':25})
        basis['rows'][1]['model'] = 'unknown'
        with self.assertRaises(ValidationError):
            proposal(self.scenario,group,'Дилеры',basis,'powers')

    def test_source_sql_cutoff_cache_and_resources(self):
        connection,cursor = Mock(),Mock()
        cursor.fetchall.return_value = [('Дилеры',2026,10,'IN',10)]
        with patch('base.split_distribution_history.connect_database',return_value=(connection,cursor)) as source:
            basis = load_basis(self.scenario,self.group_state)
            self.assertEqual(load_basis(self.scenario,self.group_state),basis)
            source.assert_called_once()
        self.assertEqual(cursor.execute.call_args.args[1:3],('2022-01-01','2026-11-01'))
        self.assertEqual(basis['rows'][0]['qty'],'10')
        cursor.close.assert_called_once(); connection.close.assert_called_once()

    def test_forms_preserve_other_section_and_channel(self):
        test_split_cycle.SplitCycleTests.complete(self)
        powers = deepcopy(self.scenario.state['groups'][self.gid]['powers'])
        state = changed_state(self.scenario,{'action':'distribution_months','group':self.gid,'channel':self.cid,
                    **{'month_2027-'+f'{month:02}':'100' if month == 1 else '0' for month in range(1,13)}})
        self.assertEqual(state['groups'][self.gid]['powers'],powers)
        self.assertEqual(state['groups'][self.gid]['channels'][str(self.channels[1].pk)],
                         self.scenario.state['groups'][self.gid]['channels'][str(self.channels[1].pk)])
        old_months = deepcopy(self.scenario.state['groups'][self.gid]['channels'])
        state = changed_state(self.scenario,{'action':'distribution_powers','group':self.gid,'channel':self.cid,
                   'share_0':'100','factor_0':'1','cost_0':'40','indoor_0':str(self.models[0].pk),'outdoor_0':str(self.models[1].pk)})
        self.assertEqual(state['groups'][self.gid]['channels'],old_months)

    def test_preview_is_read_only_until_grid_saved_and_manual_values_are_not_replaced(self):
        import json
        original = deepcopy(self.scenario.state)
        with patch('base.split_distribution_history.load_basis',return_value=self.basis()):
            response = self.client.post(self.url,{'action':'preview_month_grid','revision':self.scenario.revision,'group':self.gid,'channel':self.cid})
        self.assertEqual(response.status_code,200)
        data = response.json()
        self.assertEqual(Decimal(data['values']['2027-01']),50)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original)
        payload = [{'gid':self.gid,'cid':self.cid,'mode':'fact','unit':'percent','values':data['values'],'token':data['token']}]
        response = self.client.post(self.url,{'action':'save_month_grid','revision':self.scenario.revision,'grid_payload':json.dumps(payload)})
        self.assertEqual(response.status_code,200)
        self.scenario.refresh_from_db()
        self.assertEqual(Decimal(self.scenario.state['groups'][self.gid]['channels'][self.cid]['months']['2027-01']),50)
        self.assertFalse(self.scenario.state['groups'][self.gid]['distribution_sources']['months:'+self.cid].get('adjusted',False))
        with patch('base.split_distribution_history.load_basis') as source:
            self.client.get(self.url,{'tab':'distribution'})
            source.assert_not_called()

    def test_approved_and_stale_requests_never_query_source(self):
        with patch('base.split_distribution_history.load_basis') as source:
            self.client.post(self.url,{'action':'fact_distribution','revision':0,'group':self.gid,'channel':self.cid})
            source.assert_not_called()
        self.scenario.stock_date = date(2026,12,31)
        self.scenario.save()
        test_split_cycle.SplitCycleTests.complete(self)
        approve(self.scenario,self.actor,self.scenario.revision,False)
        with patch('base.split_distribution_history.load_basis') as source:
            self.client.get(self.url,{'tab':'distribution','section':'powers'})
            self.client.post(self.url,{'action':'fact_distribution','revision':self.scenario.revision,'group':self.gid,'channel':self.cid})
            source.assert_not_called()

    def test_source_failure_leaves_manual_form_and_no_state_changes(self):
        original = deepcopy(self.scenario.state)
        with patch('base.split_distribution_history.load_basis',side_effect=ValidationError('Источник недоступен')):
            response = self.client.post(self.url,{'action':'preview_month_grid','revision':self.scenario.revision,'group':self.gid,'channel':self.cid})
        self.assertEqual(response.status_code,400)
        self.assertIn('Источник недоступен',response.json()['error'])
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original)

    def test_explicit_reload_changes_only_selected_section_and_records_cutoff(self):
        test_split_cycle.SplitCycleTests.complete(self)
        original = deepcopy(self.scenario.state['groups'][self.gid]['powers'])
        with patch('base.split_distribution_history.load_basis',return_value=self.basis()):
            response = self.client.post(self.url+'?tab=distribution&section=months',
                {'action':'fact_distribution','revision':self.scenario.revision,'group':self.gid,'channel':self.cid})
        self.assertEqual(response.status_code,302)
        self.scenario.refresh_from_db()
        group = self.scenario.state['groups'][self.gid]
        self.assertEqual(group['powers'],original)
        self.assertEqual(group['distribution_sources']['months:'+self.cid]['through'],'2026-10-31')
        self.assertEqual(Decimal(group['channels'][self.cid]['months']['2027-12']),50)

    def test_invalid_basis_signature_never_persists_inputs(self):
        original = deepcopy(self.scenario.state)
        response = self.client.post(self.url+'?tab=distribution&section=months',
            {'action':'distribution_months','revision':self.scenario.revision,'group':self.gid,'channel':self.cid,'basis_token':'tampered'})
        self.assertContains(response,'Основание распределения устарело')
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original)
