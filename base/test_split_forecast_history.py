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

    def test_direct_calculation_saves_without_preview_and_is_atomic_on_missing_history(self):
        with patch('base.split_forecast_history.source',return_value=(self.data,['Дилеры'])):
            response=self.client.post(self.url,{'action':'calculate_save_forecast_history','revision':self.scenario.revision,**self.post,'year':'2026','only_empty':'1'})
        self.assertEqual(response.status_code,200,response.content)
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'][self.gid]['forecast_history_source']['method'],'average_daily')
        before=deepcopy(self.scenario.state)
        with patch('base.split_forecast_history.source',return_value=({},['Дилеры'])):
            response=self.client.post(self.url,{'action':'calculate_save_forecast_history','revision':self.scenario.revision,**self.post,'year':'2026','only_empty':'0'})
        self.assertEqual(response.status_code,400,response.content)
        self.scenario.refresh_from_db();self.assertEqual(self.scenario.state,before)
