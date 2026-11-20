from copy import deepcopy
from datetime import date
from decimal import Decimal as D
import json
from unittest.mock import Mock,patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from . import test_split_power_grid as fixtures
from .split_forecast_history import preview,apply,period,source
from .split_preplan_forecast import resolve


class HistoricalForecastTests(TestCase):
    def setUp(self):
        fixtures.PowerGridTests.setUp(self)
        self.scenario.stock_date=date(2026,11,6)
        group=self.scenario.state['groups'][self.gid]
        group['forecast_mode']='group';group['forecast_before_total']=None
        self.scenario.save(update_fields=['stock_date','state'])
        self.key=group['ref']['source_key']
        self.data={}
        for power,quantities in zip(group['powers'].values(),[(10,8),(5,7)]):
            for kind,quantity in zip(('indoor','outdoor'),quantities):
                model=next(m for m in power['models'] if m['kind']==kind)
                self.data[(self.key,model['name'].casefold())]={'qty':D(quantity),'unknown':False}
        self.post={'targets':json.dumps([self.gid]),'year':'2025','adjustment':'10'}

    def proposal(self,**overrides):
        with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])):
            return preview(self.scenario,{**self.post,**overrides})

    def test_preview_is_read_only_and_uses_exact_dates_and_sets_per_power(self):
        before=deepcopy(self.scenario.state)
        result=self.proposal();row=result['rows'][0]
        self.assertEqual(self.scenario.state,before);self.assertEqual(D(row['proposed']),D('14.3'))
        self.assertEqual([D(p['sets']) for p in row['powers']],[D('8.8'),D('5.5')])
        self.assertEqual((result['source']['from'],result['source']['through']),('2025-11-07','2025-12-31'))
        state,count=apply(self.scenario,{'tokens':json.dumps([row['token']]),'only_empty':'1'})
        self.assertEqual(count,1);self.assertEqual(state['groups'][self.gid]['forecast_mode'],'powers')
        self.assertEqual(resolve(self.scenario,state['groups'][self.gid])['total'],D('14.3'))
        self.assertEqual(state['groups'][self.gid]['channels'],before['groups'][self.gid]['channels'])

    def test_missing_history_unknown_quantity_and_unmapped_model_are_not_zeroed(self):
        self.data={};self.assertIn('Нет истории',self.proposal()['rows'][0]['error'])
        self.data={(self.key,'unknown model'):{'qty':D(5),'unknown':False}}
        self.assertIn('Нет сопоставления',self.proposal()['rows'][0]['error'])
        self.data[(self.key,'unknown model')]['unknown']=True
        self.assertIn('неизвестное количество',self.proposal()['rows'][0]['error'])

    def test_returns_and_adjustment_and_existing_values(self):
        for entry in self.data.values():entry['qty']=D(-2)
        row=self.proposal(adjustment='0')['rows'][0]
        self.assertEqual(D(row['proposed']),0);self.assertTrue(row['warnings'])
        self.scenario.state['groups'][self.gid]['forecast_before_total']='4'
        row=self.proposal()['rows'][0]
        with self.assertRaisesMessage(ValidationError,'уже задан прогноз'):
            apply(self.scenario,{'tokens':json.dumps([row['token']]),'only_empty':'1'})
        state,_=apply(self.scenario,{'tokens':json.dumps([row['token']]),'only_empty':'0'})
        self.assertEqual(resolve(self.scenario,state['groups'][self.gid])['total'],0)
        for adjustment in ('-101','1001','NaN'):
            with self.assertRaises(ValidationError):self.proposal(adjustment=adjustment)

    def test_only_empty_protects_partial_manual_forecast(self):
        group=self.scenario.state['groups'][self.gid]
        group['forecast_mode']='powers'
        for power in group['powers'].values():power['forecast_before']=None
        next(iter(group['powers'].values()))['forecast_before']='3'
        row=self.proposal()['rows'][0]
        self.assertTrue(row['has_current']);self.assertIsNone(row['current'])
        with self.assertRaisesMessage(ValidationError,'уже задан прогноз'):
            apply(self.scenario,{'tokens':json.dumps([row['token']]),'only_empty':'1'})

    def test_tampered_stale_and_duplicate_proposals_are_rejected(self):
        token=self.proposal()['rows'][0]['token']
        with self.assertRaises(ValidationError):apply(self.scenario,{'tokens':json.dumps([token+'x'])})
        with self.assertRaises(ValidationError):apply(self.scenario,{'tokens':json.dumps([token,token])})
        self.scenario.revision+=1
        with self.assertRaises(ValidationError):apply(self.scenario,{'tokens':json.dumps([token])})

    def test_leap_dates_and_missing_interval(self):
        self.scenario.stock_date=date(2024,2,28);self.scenario.start=date(2024,3,2)
        self.assertEqual(period(self.scenario,2023)[2:],(date(2023,2,28),date(2023,3,1)))
        self.scenario.stock_date=self.scenario.start
        with self.assertRaises(ValidationError):period(self.scenario,2023)

    def test_source_binds_dates_channels_and_preserves_negative_net(self):
        connection,cursor=Mock(),Mock();cursor.fetchall.return_value=[('G','IN',-2,0),('G','IN',3,0)]
        with patch('base.split_forecast_history.connect_database',return_value=(connection,cursor)):
            data,channels=source(self.scenario,date(2025,11,7),date(2025,12,31))
        self.assertEqual(data[(self.key,'in')]['qty'],1)
        args=cursor.execute.call_args.args
        self.assertEqual(args[1:3],(date(2025,11,7),date(2026,1,1)))
        self.assertIn('d.Chanel IN',args[0]);self.assertEqual(set(args[4:]),set(channels))
        cursor.close.assert_called_once();connection.close.assert_called_once()

    def test_blank_forecasts_render_without_invalid_none_number_defaults(self):
        response=self.client.get(self.url,{'tab':'stock'})
        self.assertEqual(response.status_code,200)
        self.assertNotIn('value="None"',response.content.decode())
        self.assertContains(response,'Рассчитать прогноз по истории')

    def test_http_preview_then_apply_persists_once_and_revision_protects(self):
        revision=self.scenario.revision
        with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])):
            response=self.client.post(self.url,{**self.post,'action':'preview_forecast_history','revision':revision})
        self.assertEqual(response.status_code,200,response.content)
        token=response.json()['rows'][0]['token']
        post={'action':'apply_forecast_history','revision':revision,'tokens':json.dumps([token]),'only_empty':'1'}
        self.assertEqual(self.client.post(self.url,post).status_code,200)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.revision,revision+1)
        self.assertEqual(resolve(self.scenario,self.scenario.state['groups'][self.gid])['total'],D('14.3'))
        self.assertEqual(self.client.post(self.url,post).status_code,400)

    def test_incomplete_stock_year_uses_daily_sales_and_records_actual_period(self):
        result=self.proposal(year='2026',adjustment='0')
        self.assertEqual((result['source']['from'],result['source']['through']),('2026-01-01','2026-11-06'))
        self.assertEqual(result['source']['method'],'average_daily')
        self.assertEqual(result['source']['fact_days'],310);self.assertEqual(result['source']['target_days'],55)
        row=result['rows'][0]
        self.assertEqual([D(p['sets']) for p in row['powers']],[D('1.419355'),D('0.887097')])
        state,count=apply(self.scenario,{'tokens':json.dumps([row['token']]),'only_empty':'1'})
        self.assertEqual(state['groups'][self.gid]['forecast_history_source']['method'],'average_daily')
        self.assertEqual(state['groups'][self.gid]['forecast_before_total'],'2.306452')

    def test_incomplete_year_never_reads_future_dates_and_rejects_future_year(self):
        with patch('base.split_forecast_history.timezone.localdate',return_value=date(2026,10,1)):
            self.assertEqual(period(self.scenario,2026)[2:],(date(2026,1,1),date(2026,10,1)))
        with self.assertRaises(ValidationError):period(self.scenario,2027)

    def test_direct_calculation_saves_without_preview_and_skips_missing_history(self):
        with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])):
            response=self.client.post(self.url,{'action':'calculate_save_forecast_history','revision':self.scenario.revision,**self.post,'year':'2026','only_empty':'1'})
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['forecast_history_source']['method'],'average_daily')
        before=deepcopy(self.scenario.state)
        with patch('base.split_forecast_history.source',return_value=({},['Дилеры'])):
            response=self.client.post(self.url,{'action':'calculate_save_forecast_history','revision':self.scenario.revision,**self.post,'year':'2026','only_empty':'0'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['count'],0)
        self.assertEqual(len(response.json()['skipped']),1)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)

    def test_mixed_success_and_error_saves_only_successful_group_without_overwriting_failed_group(self):
        other=deepcopy(self.scenario.state['groups'][self.gid]);other['ref'].update(name='Missing history',source_key='missing')
        other['forecast_before_total']='17'
        self.scenario.state['groups']['other']=other;self.scenario.save(update_fields=['state'])
        revision=self.scenario.revision
        with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])):
            response=self.client.post(self.url,{'action':'calculate_save_forecast_history','revision':revision,
                **self.post,'targets':json.dumps([self.gid,'other']),'only_empty':'0'})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['count'],1)
        self.assertEqual(response.json()['skipped'][0]['gid'],'other')
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.revision,revision+1)
        self.assertEqual(self.scenario.state['groups']['other'],other)
        self.assertEqual(self.scenario.state['groups'][self.gid]['forecast_before_total'],'14.300000')

    def test_history_forecast_caps_each_power_and_keeps_unconstrained_source(self):
        group=self.scenario.state['groups'][self.gid]
        group['powers']['07'].update(stock_indoor='3',stock_outdoor='30',prior_indoor='2',prior_outdoor='0')
        before=deepcopy(self.scenario.state);row=self.proposal()['rows'][0]
        self.assertEqual([D(p['sets']) for p in row['powers']],[D(5),D('5.5')])
        self.assertEqual(D(row['proposed']),D('10.5'))
        self.assertTrue(any('ограничен' in warning for warning in row['warnings']))
        state,_=apply(self.scenario,{'tokens':json.dumps([row['token']])})
        source=state['groups'][self.gid]['forecast_history_source']
        self.assertTrue(source['stock_cap_applied']);self.assertEqual(D(source['unconstrained_forecast']),D('14.3'))
        self.assertEqual(source['stock_constraint'],'before-start-v1')
        for power in state['groups'][self.gid]['powers'].values():
            for kind in ('indoor','outdoor'):
                self.assertGreaterEqual(D(power['stock_'+kind])+D(power['prior_'+kind])-D(power['forecast_before']),0)
        self.assertEqual(self.scenario.state,before)

    def test_zero_available_and_future_incoming_do_not_inflate_forecast(self):
        for power in self.scenario.state['groups'][self.gid]['powers'].values():
            power.update(stock_indoor='0',stock_outdoor='0',prior_indoor='0',prior_outdoor='0',incoming_indoor='999',incoming_outdoor='999')
        row=self.proposal()['rows'][0]
        self.assertEqual(D(row['proposed']),0)
        self.assertTrue(all(D(p['sets'])==0 for p in row['powers']))
        self.scenario.state['groups'][self.gid]['powers']['07']['stock_indoor']=None
        row=self.proposal()['rows'][0];self.assertNotIn('token',row);self.assertIn('остатки',row['error'])

    def test_direct_average_daily_save_is_capped_without_extra_confirmation(self):
        group=self.scenario.state['groups'][self.gid]
        for power in group['powers'].values():power.update(stock_indoor='0.2',stock_outdoor='30',prior_indoor='0.3',prior_outdoor='0')
        self.scenario.save(update_fields=['state'])
        with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])):
            response=self.client.post(self.url,{'action':'calculate_save_forecast_history','revision':self.scenario.revision,**self.post,'year':'2026','adjustment':'0','only_empty':'0'})
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db()
        self.assertEqual(D(self.scenario.state['groups'][self.gid]['forecast_before_total']),1)
        self.assertTrue(all(D(power['forecast_before'])==D('.5') for power in self.scenario.state['groups'][self.gid]['powers'].values()))

    def test_capped_history_cannot_save_negative_opening_after_inventory_reduction_or_use_old_preview(self):
        from .split_workflow import persist
        from .split_stock_workspace import candidate
        from django.core import signing
        from .split_forecast_history import SALT
        row=self.proposal()['rows'][0]
        state,_=apply(self.scenario,{'tokens':json.dumps([row['token']])})
        state['groups'][self.gid]['powers']['07'].update(stock_indoor='0',prior_indoor='0')
        with self.assertRaisesMessage(ValidationError,'прогноз превышает'):
            persist(self.scenario,self.actor,self.scenario.revision,state,'Bad inventory')
        with self.assertRaisesMessage(ValidationError,'прогноз превышает'):
            candidate(self.scenario,json.dumps({self.gid:{'07:stock_indoor':'0','07:prior_indoor':'0'}}),forecasts=json.dumps([row['token']]))
        old=signing.loads(row['token'],salt=SALT);old['source'].pop('stock_constraint')
        with self.assertRaisesMessage(ValidationError,'устарел'):
            apply(self.scenario,{'tokens':json.dumps([signing.dumps(old,salt=SALT)])})

    def test_only_empty_repairs_unsafe_automatic_forecast_per_power_but_keeps_manual_and_safe_values(self):
        group=self.scenario.state['groups'][self.gid]
        group.update(forecast_mode='powers',forecast_history_source={'method':'average_daily'})
        group['powers']['07'].update(stock_indoor='81',stock_outdoor='81',prior_indoor='0',prior_outdoor='0',forecast_before='231.709677')
        group['powers']['09'].update(stock_indoor='68',stock_outdoor='67',prior_indoor='0',prior_outdoor='0',forecast_before='486.661290')
        for entry in self.data.values():entry['qty']=D(1000)
        self.scenario.save(update_fields=['state'])
        before=deepcopy(self.scenario.state)
        group['forecast_workspace_source']={'kind':'manual','label':'Вручную'}
        self.scenario.save(update_fields=['state'])
        payload={'action':'calculate_save_forecast_history','revision':self.scenario.revision,**self.post,'only_empty':'1'}
        with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])):
            response=self.client.post(self.url,payload)
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(response.json()['count'],0)
        group.pop('forecast_workspace_source');self.scenario.save(update_fields=['state'])
        with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])):
            response=self.client.post(self.url,payload)
        self.assertEqual(response.status_code,200,response.content);self.assertEqual(response.json()['count'],1)
        self.scenario.refresh_from_db();group=self.scenario.state['groups'][self.gid]
        self.assertEqual([D(p['forecast_before']) for p in group['powers'].values()],[81,67])
        self.assertEqual([D(p['stock_outdoor'])-D(p['forecast_before']) for p in group['powers'].values()],[0,0])
        saved=deepcopy(self.scenario.state)
        with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])):
            response=self.client.post(self.url,{**payload,'revision':self.scenario.revision})
        self.assertEqual(response.json()['count'],0)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,saved)

    def test_preview_selected_unsafe_automatic_forecast_can_be_applied_with_only_empty(self):
        group=self.scenario.state['groups'][self.gid]
        group.update(forecast_mode='powers',forecast_history_source={'source':'DataWH.dbo.Sales.Quantity'})
        for power in group['powers'].values():power['forecast_before']='200'
        row=self.proposal()['rows'][0]
        self.assertTrue(row['needs_stock_repair'])
        state,count=apply(self.scenario,{'tokens':json.dumps([row['token']]),'only_empty':'1'})
        self.assertEqual(count,1)
        self.assertTrue(all(D(p['forecast_before'])<=D(p['stock_indoor']) for p in state['groups'][self.gid]['powers'].values()))
