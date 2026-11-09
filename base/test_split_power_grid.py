from copy import deepcopy
from decimal import Decimal
import json
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle
from .models import SalesPlanningGoods
from .split_engine import allocate, HUNDRED, stored
from .split_workflow import approve


class PowerGridTests(TestCase):
    def setUp(self):
        test_split_cycle.SplitCycleTests.setUp(self)
        test_split_cycle.SplitCycleTests.complete(self)
        self.gid = str(self.group.pk)
        self.url = reverse('split_scenario',args=[self.scenario.pk])
        self.nine = [SalesPlanningGoods.objects.create(source_key='nine'+kind,name='NINE '+kind,planning_group='G',
                     product_group='Бытовые кондиционеры',power_class='09',block_type=label)
                     for kind,label in (('indoor','Внутренний'),('outdoor','Наружный'))]
        group = self.scenario.state['groups'][self.gid]
        power = deepcopy(group['powers']['07'])
        power['models'] = [{'id':str(model.pk),'key':model.source_key,'name':model.name,'kind':kind} for model,kind in zip(self.nine,('indoor','outdoor'))]
        power.update(share='50',indoor=str(self.nine[0].pk),outdoor=str(self.nine[1].pk))
        group['powers']['07']['share'] = '50'
        group['powers']['09'] = power
        self.scenario.save()

    def row(self, **kwargs):
        return {'gid':self.gid,'mode':'manual','unit':'percent','values':{'07':'25','09':'75'},**kwargs}

    def save_rows(self, rows, revision=None):
        return self.client.post(self.url,{'action':'save_power_grid','revision':self.scenario.revision if revision is None else revision,'grid_payload':json.dumps(rows)})

    def test_columns_three_modes_and_no_detail_editor(self):
        response = self.client.get(self.url,{'tab':'distribution','section':'powers','edit':'1'})
        self.assertContains(response,'data-grid-column="07"')
        self.assertContains(response,'data-grid-column="09"')
        self.assertContains(response,'data-kind="powers"')
        self.assertContains(response,'data-mode-button=',count=3)
        self.assertNotContains(response,'Настроить')
        self.assertNotContains(response,'id="distribution-editor"')

    def test_manual_shares_preserve_all_months_and_channels(self):
        original = deepcopy(self.scenario.state['groups'][self.gid]['channels'])
        self.assertEqual(self.save_rows([self.row()]).status_code,200)
        self.scenario.refresh_from_db()
        group = self.scenario.state['groups'][self.gid]
        self.assertEqual(group['channels'],original)
        self.assertEqual(group['power_distribution_mode'],'manual')
        self.assertEqual(Decimal(group['powers']['09']['share']),75)

    def test_incorrect_sum_and_second_row_are_atomic(self):
        self.scenario.state['groups']['second'] = deepcopy(self.scenario.state['groups'][self.gid])
        self.scenario.save()
        original = deepcopy(self.scenario.state)
        response = self.save_rows([self.row(),self.row(gid='second',values={'07':'25','09':'74.999999'})])
        self.assertEqual(response.status_code,400)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original)

    def test_fact_signature_and_parameter_changes_preserve_rule_without_source_read(self):
        basis = {'first_year':2022,'last_year':2026,'stock_date':'2026-12-31','through':'2026-12-31',
                 'rule_key':'planning_group_all_channels','rule_label':'Группа планирования · все каналы'}
        with patch('base.split_power_grid.resolve_proposal',return_value=({'07':'75','09':'25'},basis)):
            response = self.client.post(self.url,{'action':'preview_power_grid','revision':self.scenario.revision,'group':self.gid})
        data = response.json()
        self.assertEqual(self.save_rows([self.row(mode='fact',values=data['values'],token=data['token'])]).status_code,200)
        self.scenario.refresh_from_db()
        params = {'07':{'factor':'2','cost':'40','indoor':str(self.models[0].pk),'outdoor':str(self.models[1].pk)}}
        with patch('base.split_power_grid.resolve_proposal') as source:
            self.assertEqual(self.save_rows([self.row(mode='fact',values=data['values'],parameters=params)]).status_code,200)
            source.assert_not_called()
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['distribution_sources']['powers']['rule_key'],'planning_group_all_channels')
        self.assertEqual(self.save_rows([self.row(mode='fact')]).status_code,400)

    def test_uniform_rounding_follows_columns_even_with_different_snapshot_order(self):
        group = self.scenario.state['groups'][self.gid]
        group['powers']['12'] = deepcopy(group['powers']['09'])
        extra = deepcopy(group)
        extra['powers'] = {key:extra['powers'][key] for key in ('12','07','09')}
        self.scenario.state['groups']['second'] = extra
        self.scenario.save()
        values = dict(zip(('07','09','12'),map(stored,allocate(HUNDRED,[Decimal(1)]*3))))
        self.assertEqual(self.save_rows([self.row(mode='equal',values=values),self.row(gid='second',mode='equal',values=values)]).status_code,200)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups']['second']['powers']['12']['share'],'33.333334')

    def test_inactive_powers_are_not_allocated_and_old_shares_are_cleared(self):
        self.scenario.state['groups'][self.gid]['powers']['09']['active'] = False
        self.scenario.save()
        self.assertEqual(self.save_rows([self.row(values={'07':'100'})]).status_code,200)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['powers']['09']['share'],'0')

    def test_invalid_models_parameters_and_power_keys_do_not_save(self):
        original = deepcopy(self.scenario.state)
        for params in ({'factor':'0'}, {'factor':'1','cost':'101'}, {'factor':'1','cost':'40','indoor':str(self.nine[0].pk)}):
            with self.subTest(params=params):
                self.assertEqual(self.save_rows([self.row(parameters={'07':params})]).status_code,400)
        self.assertEqual(self.save_rows([self.row(values={'07':'100','unknown':'0'})]).status_code,400)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original)

    def test_approved_and_stale_grid_never_read_source_or_write(self):
        with patch('base.split_power_grid.resolve_proposal') as source:
            self.assertEqual(self.save_rows([self.row()],revision=0).status_code,400)
            approve(self.scenario,self.actor,self.scenario.revision,False)
            self.scenario.refresh_from_db()
            self.assertEqual(self.save_rows([self.row()]).status_code,400)
            response = self.client.get(self.url,{'tab':'distribution','section':'powers'})
            self.assertContains(response,'data-draft="0"')
            self.assertNotContains(response,'data-grid-save')
            self.assertEqual(self.client.post(self.url,{'action':'preview_power_grid','revision':self.scenario.revision,'group':self.gid}).status_code,400)
            source.assert_not_called()
