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
        cursor.fetchall.return_value=rows
        return connection,cursor

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
