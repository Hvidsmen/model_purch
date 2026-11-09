from copy import deepcopy
from decimal import Decimal
import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle
from .split_engine import allocate, HUNDRED, planning_months, stored
from .split_workflow import approve


class InlineMonthGridTests(TestCase):
    def setUp(self):
        test_split_cycle.SplitCycleTests.setUp(self)
        test_split_cycle.SplitCycleTests.complete(self)
        self.gid = str(self.group.pk)
        self.cid = str(self.channels[0].pk)
        self.url = reverse('split_scenario',args=[self.scenario.pk])

    def row(self, cid=None, **kwargs):
        return {'gid':self.gid,'cid':cid or self.cid,'mode':'manual','unit':'percent',
                'values':{month:'100' if month.endswith('-01') else '0' for month in planning_months(self.scenario)},**kwargs}

    def save_rows(self, rows, revision=None):
        return self.client.post(self.url,{'action':'save_month_grid','revision':self.scenario.revision if revision is None else revision,'grid_payload':json.dumps(rows)})

    def test_manual_two_rows_save_atomically_and_preserve_power_settings(self):
        powers = deepcopy(self.scenario.state['groups'][self.gid]['powers'])
        response = self.save_rows([self.row(),self.row(str(self.channels[1].pk))])
        self.assertEqual(response.status_code,200)
        self.scenario.refresh_from_db()
        group = self.scenario.state['groups'][self.gid]
        self.assertEqual(group['powers'],powers)
        for line in group['channels'].values():
            self.assertEqual(line['distribution_mode'],'manual')
            self.assertEqual(sum(map(Decimal,line['months'].values())),100)

    def test_invalid_second_row_does_not_save_first_row(self):
        original = deepcopy(self.scenario.state)
        bad = self.row(str(self.channels[1].pk)); bad['values']['2027-01'] = '99.999999'
        response = self.save_rows([self.row(),bad])
        self.assertEqual(response.status_code,400)
        self.assertIn('100%',response.json()['error'])
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original)

    def test_empty_negative_and_overprecise_cells_are_rejected(self):
        original = deepcopy(self.scenario.state)
        for invalid in ('','-1','0.0000001','=SUM(A1:B1)','NaN'):
            with self.subTest(invalid=invalid):
                row = self.row(); row['values']['2027-02'] = invalid
                self.assertEqual(self.save_rows([row]).status_code,400)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original)

    def test_fact_preview_does_not_write_and_signature_protects_values(self):
        basis = {'first_year':2022,'last_year':2026,'through':'2026-12-31','stock_date':'2026-12-31','loaded_at':'test',
                 'rows':[{'channel':'дилеры','year':2025,'month':month,'model':'in','qty':qty} for month,qty in ((1,'80'),(12,'20'))]}
        original = deepcopy(self.scenario.state)
        with patch('base.split_distribution_history.load_basis',return_value=basis):
            response = self.client.post(self.url,{'action':'preview_month_grid','revision':self.scenario.revision,'group':self.gid,'channel':self.cid})
        self.assertEqual(response.status_code,200)
        data = response.json()
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original)
        row = self.row(mode='fact',values=data['values'],token=data['token'])
        row['values'] = {**data['values'],'2027-01':'60','2027-12':'40'}
        self.assertEqual(self.save_rows([row]).status_code,400)
        row['values'] = data['values']
        self.assertEqual(self.save_rows([row]).status_code,200)
        self.scenario.refresh_from_db()
        source = self.scenario.state['groups'][self.gid]['distribution_sources']['months:'+self.cid]
        self.assertEqual(source['rule_key'],'planning_group_channel')

    def test_equal_distribution_is_verified_on_server_and_exactly_totals_100(self):
        values = dict(zip(planning_months(self.scenario),map(stored,allocate(HUNDRED,[Decimal(1)]*12))))
        self.assertEqual(self.save_rows([self.row(mode='equal',values=values)]).status_code,200)
        self.scenario.refresh_from_db()
        line = self.scenario.state['groups'][self.gid]['channels'][self.cid]
        self.assertEqual(line['distribution_mode'],'equal')
        self.assertEqual(sum(map(Decimal,line['months'].values())),100)
        self.assertEqual(self.save_rows([self.row(mode='equal')]).status_code,400)

    def test_quantity_mode_remains_available_and_must_equal_net_plan(self):
        self.scenario.state['volume_mode'] = 'annual'
        line = self.scenario.state['groups'][self.gid]['channels'][self.cid]
        line['input'],line['fact'] = '30','20'
        self.scenario.save()
        row = self.row(unit='quantity'); row['values']['2027-01'] = '10'
        self.assertEqual(self.save_rows([row]).status_code,200)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['channels'][self.cid]['allocation'],'quantity')

    def test_duplicate_unknown_and_stale_rows_are_rejected_without_source_query(self):
        with patch('base.split_distribution_history.load_basis') as source:
            self.assertEqual(self.save_rows([self.row(),self.row()]).status_code,400)
            self.assertEqual(self.save_rows([self.row(gid='unknown')]).status_code,400)
            self.assertEqual(self.save_rows([self.row()],revision=0).status_code,400)
            self.client.post(self.url,{'action':'preview_month_grid','revision':0,'group':self.gid,'channel':self.cid})
            source.assert_not_called()

    def test_approved_grid_is_readonly_and_never_previews_source(self):
        approve(self.scenario,self.actor,self.scenario.revision,False)
        self.scenario.refresh_from_db()
        with patch('base.split_distribution_history.load_basis') as source:
            response = self.client.get(self.url,{'tab':'distribution'})
            self.assertContains(response,'data-draft="0"')
            self.assertNotContains(response,'data-grid-save')
            self.assertEqual(self.save_rows([self.row()]).status_code,400)
            self.assertEqual(self.client.post(self.url,{'action':'preview_month_grid','revision':self.scenario.revision,'group':self.gid,'channel':self.cid}).status_code,400)
            source.assert_not_called()
