from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from . import test_split_cycle
from .split_history import load_history, presentation, HISTORY_SQL
from .split_workflow import approve


class HistoricalFactTests(TestCase):
    def setUp(self):
        test_split_cycle.SplitCycleTests.setUp(self)
        test_split_cycle.SplitCycleTests.complete(self)

    def source(self, rows):
        connection,cursor=Mock(),Mock()
        cursor.fetchall.return_value=[(*row[:5],None if row[5] is None else Decimal(str(row[5]))/2,None if row[6] is None else Decimal(str(row[6]))/2,model) for row in rows for model in ('IN','OUT')]
        return connection,cursor

    def test_kits_are_paired_per_power_channel_and_month_and_blocks_remain_in_details(self):
        group=next(iter(self.scenario.state['groups'].values()))
        group['powers']['09']=deepcopy(group['powers']['07'])
        group['powers']['09']['models']=[{**model,'name':model['name']+'09'} for model in group['powers']['09']['models']]
        rows=[('G','Дилеры',2025,1,q,20,10,model) for model,q in [('IN',10),('OUT',4),('IN09',1),('OUT09',8)]]
        rows += [('G','Дилеры',2025,2,-2,-5,-3,model) for model in ('IN','OUT')]
        connection,cursor=Mock(),Mock();cursor.fetchall.return_value=rows
        with patch('base.split_history.connect_database',return_value=(connection,cursor)):
            snapshot=load_history(self.scenario)
        self.scenario.state['history']=snapshot
        year=next(year for year in presentation(self.scenario)['groups'][0]['years'] if year['year']==2025)
        self.assertEqual(year['months'][0]['cells'][0]['qty'],5) # min(10,4) + min(1,8), not min(11,12).
        self.assertEqual(year['months'][1]['cells'][0]['qty'],-2)
        self.assertEqual(year['annual'][0]['qty'],3)
        self.assertEqual(year['annual'][0]['revenue'],70)
        self.assertEqual(year['annual'][0]['cost'],34)
        self.assertEqual(sum(Decimal(row['qty']) for row in snapshot['model_rows']),19)
        self.assertEqual(snapshot['quantity_unit'],'sets-v1')
        self.assertIn('p.ModelCode',cursor.execute.call_args.args[0])
        self.scenario.save(update_fields=['state'])
        response=self.client.get(reverse('split_scenario',args=[self.scenario.pk]),{'tab':'detail','group':next(iter(self.scenario.state['groups'])),'channel':next(iter(self.scenario.state['channels']))})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Факт по моделям · штуки блоков')
        self.assertContains(response,'IN09')
        snapshot.pop('quantity_unit')
        old=deepcopy(snapshot)
        view=presentation(self.scenario)
        self.assertIsNone(next(year for year in view['groups'][0]['years'] if year['year']==2025)['annual'][0]['qty'])
        self.assertIn('Загрузить факт',view['quantity_note'])
        self.assertEqual(snapshot,old)

    def test_unmapped_or_unknown_model_quantity_does_not_invent_kits(self):
        for model,qty in [('UNKNOWN',10),('IN',None)]:
            connection,cursor=Mock(),Mock();cursor.fetchall.return_value=[('G','Дилеры',2025,1,qty,20,10,model)]
            with patch('base.split_history.connect_database',return_value=(connection,cursor)):
                snapshot=load_history(self.scenario)
            self.assertIsNone(snapshot['rows'][0]['qty'])
            self.assertEqual(Decimal(snapshot['rows'][0]['revenue']),20)

    def test_three_previous_calendar_years_formula_and_weighted_markup(self):
        connection,cursor=self.source([('G','Дилеры',2025,1,10,200,100),('g ','дилеры',2025,2,30,300,100)])
        with patch('base.split_history.connect_database',return_value=(connection,cursor)), patch('base.split_history.timezone.localdate',return_value=date(2027,1,5)):
            snapshot=load_history(self.scenario)
        self.assertEqual(cursor.execute.call_args.args[1:3],('2024-01-01','2027-01-01'))
        self.assertIn('SUM(s.Price - s.AmountUSDIt_ITW)',HISTORY_SQL)
        self.assertNotIn('BallsOuterUSD',HISTORY_SQL)
        self.assertEqual(snapshot['first_year'],2024)
        self.scenario.state['history']=snapshot
        view=presentation(self.scenario)
        year=next(row for row in view['groups'][0]['years'] if row['year']==2025)
        self.assertEqual(year['annual'][0]['qty'],40)
        self.assertEqual(year['annual'][0]['markup'],Decimal('2.5'))
        self.assertEqual(year['months'][0]['cells'][0]['markup'],2)
        self.assertEqual(year['months'][1]['cells'][0]['markup'],3)
        cursor.close.assert_called_once(); connection.close.assert_called_once()

    def test_missing_future_and_new_composition_data_are_not_fabricated(self):
        self.assertIsNone(presentation(self.scenario)['groups'][0]['years'][0]['annual'][0]['qty'])
        connection,cursor=self.source([])
        with patch('base.split_history.connect_database',return_value=(connection,cursor)), patch('base.split_history.timezone.localdate',return_value=date(2026,11,9)):
            self.scenario.state['history']=load_history(self.scenario)
        year=presentation(self.scenario)['groups'][0]['years'][-1]
        self.assertEqual(year['months'][0]['cells'][0]['qty'],0)
        self.assertIsNone(year['months'][11]['cells'][0]['qty'])
        self.assertIsNone(year['annual'][0]['markup'])
        self.scenario.state['history']['groups']=[]
        self.assertIsNone(presentation(self.scenario)['groups'][0]['years'][0]['annual'][0]['qty'])

    def test_snapshot_load_does_not_replace_plan_and_approved_version_never_queries(self):
        url=reverse('split_scenario',args=[self.scenario.pk])
        original=deepcopy(self.scenario.state['groups'])
        connection,cursor=self.source([('G','Дилеры',2025,1,10,200,100)])
        with patch('base.split_history.connect_database',return_value=(connection,cursor)):
            response=self.client.post(url,{'action':'load_history','revision':self.scenario.revision},follow=True)
        self.assertContains(response,'data-history-toggle=')
        self.assertContains(response,'data-history-month-column=')
        self.assertNotContains(response,'Раскрыть по месяцам')
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state['groups'],original)
        self.assertTrue(self.scenario.state['history']['rows'])
        from io import BytesIO
        from openpyxl import load_workbook
        from .split_exports import workbook
        exported = workbook(self.scenario)
        book = load_workbook(BytesIO(b''.join(exported.streaming_content)), read_only=True)
        rows = list(book['Исторический факт'].values)
        january = next(row for row in rows[2:] if row[2] == '2025-01' and row[3] == 'Дилеры')
        self.assertEqual(january[4:], (10, 200, 100, 2))
        book.close()
        exported.close()
        approve(self.scenario,self.actor,self.scenario.revision,False)
        self.scenario.refresh_from_db()
        with patch('base.split_history.load_history') as source:
            self.client.post(url,{'action':'load_history','revision':self.scenario.revision})
            source.assert_not_called()

    def test_source_failure_keeps_previous_snapshot_and_does_not_expose_driver_details(self):
        url=reverse('split_scenario',args=[self.scenario.pk])
        original=deepcopy(self.scenario.state)
        with patch('base.split_history.connect_database',side_effect=RuntimeError('password=secret')),self.assertLogs('base.split_history',level='ERROR'):
            response=self.client.post(url,{'action':'load_history','revision':self.scenario.revision})
        self.assertNotContains(response,'password=secret')
        self.scenario.refresh_from_db()
        self.assertEqual(self.scenario.state,original)
