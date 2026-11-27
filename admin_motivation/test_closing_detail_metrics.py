from decimal import Decimal
from unittest.mock import patch, MagicMock
from contextlib import nullcontext
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from . import test_distribution_stages as fixtures
from .services import closing_detail_metrics as metrics, closing_details


class DetailMetricTests(SimpleTestCase):
    def test_global_kind_and_person_denominators_are_distinct(self):
        rows=[dict(kind='Политики',author='A',before='100',payment='annual'),dict(kind='Политики',author='B',before='50',payment='annual'),dict(kind='Директора',author='D',before='30',payment='annual'),dict(kind='Политики',author='A',before='20',payment='catchup')]
        comparison=closing_details.impact(rows,2)
        data={'global':{'plan':10000,'fact':Decimal(5000)},'kinds':{'Политики':{'plan':4000,'fact':Decimal(2000)},'Директора':{'plan':10000,'fact':Decimal(5000)}},'people':{'Политики':{'a':{'plan':1000,'fact':Decimal(500)},'b':{'plan':2000,'fact':Decimal(1000)}}}}
        value=metrics.enrich(comparison,comparison,data)
        self.assertEqual(value['before_fact_percent'],Decimal(4))
        self.assertEqual(value['kinds'][1]['before_fact_percent'],Decimal('8.5'))
        self.assertEqual(value['rows'][0]['before_fact_percent'],Decimal(20))
        self.assertEqual(value['rows'][0]['after_fact_percent'],Decimal(40))
        self.assertEqual(value['rows'][3]['fact'],500)
        self.assertEqual(value['rows'][3]['before_fact_percent'],Decimal(4))
        self.assertEqual(value['rows'][2]['metric_scope'],'База директоров подразделения')
        self.assertEqual(value['fact'],5000)  # Never sum per-person or annual/catchup facts.

    def test_usd_numerators_match_people_when_row_order_differs(self):
        rows=[dict(kind='Политики',author='A',before='100',payment='quarter'),dict(kind='Политики',author='B',before='50',payment='quarter')]
        rub=closing_details.impact([dict(row,before=str(Decimal(row['before'])*90)) for row in rows],2)
        usd=closing_details.impact(list(reversed(rows)),2)
        data={'people':{'Политики':{'a':{'fact':Decimal(500)},'b':{'fact':Decimal(1000)}}}}
        result=metrics.enrich(rub,usd,data)
        self.assertEqual(result['rows'][0]['before_fact_percent'],20)
        self.assertEqual(result['rows'][1]['after_fact_percent'],10)
        self.assertEqual(result['rows'][0]['before'],9000)

    def test_missing_person_has_no_department_fallback(self):
        result=closing_details.impact([dict(kind='Политики',author='No data',before='0',payment='quarter')])
        metrics.enrich(result,result,{'global':{'plan':100,'fact':100}})
        self.assertIsNone(result['rows'][0]['fact'])
        self.assertIsNone(result['rows'][0]['after_fact_percent'])


class DetailMetricSourceTests(TestCase):
    setUp=fixtures.ClosingWorkflowTests.setUp

    def test_inputs_are_grouped_once_and_connections_closed(self):
        cache.clear()
        connection=MagicMock();cursor=MagicMock()
        cursor.fetchall.return_value=[('Продажи','A',100,80),('Продажи','B',200,160),('Политики','P',90,60)]
        with patch('admin_motivation.services.reports_next.current_run',side_effect=lambda run:nullcontext()),patch('admin_motivation.conns.connect_database',return_value=(connection,cursor)),patch.object(metrics,'department',return_value={'closing':{'plan':300,'fact':240}}):
            result=metrics.load(self.run,4,'Closing')
            self.assertEqual(metrics.load(self.run,4,'Closing'),result)
        self.assertEqual(cursor.execute.call_count,1)
        connection.close.assert_called_once()
        self.assertEqual(result['kinds']['Продажи']['fact'],240)
        self.assertEqual(result['kinds']['Директора']['fact'],240)
        self.assertEqual(result['people']['Политики']['p']['fact'],60)
        self.assertIn('GROUP BY',cursor.execute.call_args.args[0])
        self.assertEqual(cursor.execute.call_args.args[1:],('4 Квартал','Closing',2025))

    def test_dialog_and_preview_expose_same_metric_fields(self):
        rows=[dict(branch='Closing',kind='Политики',author='Policy',payment='quarter',before='100')]
        source={'global':{'plan':2000,'fact':Decimal(1000)},'kinds':{'Политики':{'plan':1000,'fact':Decimal(500)}},'people':{'Политики':{'policy':{'plan':500,'fact':Decimal(200)}}}}
        url=reverse('motivation_closing_details',args=[self.branch.pk])
        with patch('admin_motivation.closing_details_views.baseline',return_value=(rows,self.run)),patch.object(metrics,'load',return_value=source):
            response=self.client.get(url,{'year':2025,'period':4})
            self.assertContains(response,'План подразделения, USD')
            self.assertEqual(response.context['comparison']['rows'][0]['before_fact_percent'],50)
            post={'year':2025,'period':4,'coefficient':'2','revision':'0','people-TOTAL_FORMS':'0','people-INITIAL_FORMS':'0','action':'preview'}
            preview=self.client.post(url,post)
            self.assertEqual(preview.status_code,200)
            row=preview.json()['comparison']['rows'][0]
            self.assertEqual(Decimal(row['after_fact_percent']),100)
            self.assertEqual(Decimal(row['fact']),200)
