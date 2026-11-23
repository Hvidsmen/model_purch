from datetime import date
import json
from django.test import TestCase
from django.urls import reverse
from base.testing import authorize_admin
from .models import ModelDDPCalculation,ModelDDPRow,GoodsGroup
from .services.model_ddp import mutate
from .test_model_ddp import PARAMS,RATES


class WorkflowTests(TestCase):
    def setUp(self):
        self.user=authorize_admin(self.client)
        GoodsGroup.objects.create(name='Goods',container_volume=50,duty_rate=.1)
        self.batch=ModelDDPCalculation.objects.create(name='Workflow',as_of=date(2026,11,23),created_by=self.user,parameters=PARAMS,rates=RATES)
        self.row=ModelDDPRow.objects.create(calculation=self.batch,model_ref='A',model_name='Model A',price=100,currency='USD',
            source_data={'volume':'1','classification':{'group_goods':'Goods','supply_type':'p2','brand':'A'}})
        self.other=ModelDDPRow.objects.create(calculation=self.batch,model_ref='B',model_name='Model B',price=100,currency='USD',
            source_data={'volume':'1','classification':{'group_goods':'Goods','supply_type':'p2','brand':'B'}})
        self.url=reverse('model_ddp_detail',args=[self.batch.pk])

    def data(self,**extra):
        return {'revision':self.batch.revision,'action':'workflow_calculate',
                **{key:value for key,value in PARAMS.items() if key not in ('container_volume','duty_percent','supply_type')},
                'usd':'100','eur':'125','cny':'10',**extra}

    def test_loaded_prices_default_to_review_and_empty_calculation_to_load(self):
        response=self.client.get(self.url)
        self.assertEqual(response.context['workflow_step'],'review')
        self.assertContains(response,'Перейти к расчёту')
        self.batch.rows.all().delete()
        self.assertEqual(self.client.get(self.url).context['workflow_step'],'load')

    def test_save_model_parameters_does_not_recalculate(self):
        mutate(self.batch.pk,1,'calculate')
        self.batch.refresh_from_db();self.row.refresh_from_db()
        previous=self.row.result
        self.client.post(self.url+'?step=review',{'action':'rows_save','revision':2,'rows':json.dumps([self.row.pk]),f'r{self.row.pk}-volume':'2'})
        self.row.refresh_from_db();self.batch.refresh_from_db()
        self.assertEqual(self.row.overrides['volume'],'2')
        self.assertEqual(self.row.result['ddp'],previous['ddp'])
        self.assertTrue(self.row.result['stale'])
        self.assertTrue(self.batch.needs_recalculation)

    def test_single_calculation_saves_global_and_row_inputs_then_recalculates_all(self):
        response=self.client.post(self.url+'?step=calculate&brand=A',self.data(
            rows=json.dumps([self.row.pk]),price_per_container='2000',**{f'r{self.row.pk}-volume':'2',f'r{self.row.pk}-price_per_container':'1000'}))
        self.assertEqual(response.status_code,302)
        self.row.refresh_from_db();self.other.refresh_from_db();self.batch.refresh_from_db()
        self.assertEqual(self.row.overrides['volume'],'2')
        self.assertNotIn('price_per_container',self.row.overrides)
        self.assertEqual(self.batch.parameters['price_per_container'],'2000')
        self.assertEqual(self.row.result['status'],'ready')
        self.assertEqual(self.other.result['status'],'ready')
        self.assertEqual(self.other.overrides,{})
        self.assertFalse(self.batch.needs_recalculation)
        self.assertEqual(self.row.price,100)

    def test_invalid_model_prevents_global_save_and_keeps_bound_values(self):
        response=self.client.post(self.url+'?step=calculate',self.data(
            rows=json.dumps([self.row.pk]),price_per_container='2000',**{f'r{self.row.pk}-volume':'-1'}))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Model A')
        self.batch.refresh_from_db();self.row.refresh_from_db()
        self.assertEqual(self.batch.revision,1)
        self.assertEqual(self.batch.parameters['price_per_container'],'1000')
        self.assertEqual(self.row.overrides,{})
        self.assertEqual(response.context['page'][0].edit_form['volume'].value(),'-1')
        self.assertEqual(response.context['rejected_edits'],'both')

    def test_directory_change_marks_results_stale_without_get_writes(self):
        mutate(self.batch.pk,1,'calculate')
        self.assertFalse(self.client.get(self.url).context['needs_recalculation'])
        GoodsGroup.objects.update(container_volume=60)
        response=self.client.get(self.url)
        self.assertTrue(response.context['needs_recalculation'])
        self.assertContains(response,'Требуется пересчёт')
        self.batch.refresh_from_db();self.assertEqual(self.batch.revision,2)

    def test_price_review_filters_and_navigation_keep_scope(self):
        self.row.price=0;self.row.save()
        response=self.client.get(self.url,{'step':'review','price_check':'zero','brand':'A'})
        self.assertEqual(response.context['page'].paginator.count,1)
        self.assertIn('price_check=zero',response.context['step_urls']['calculate'])
        self.assertIn('brand=A',response.context['step_urls']['calculate'])
        self.assertNotContains(response,'Сохранить параметры и рассчитать')
        self.assertNotContains(response,'Рассчитать все модели')

    def test_free_navigation_does_not_save_or_calculate(self):
        for step in ('load','calculate','review'):
            response=self.client.get(self.url,{'step':step})
            self.assertEqual(response.context['workflow_step'],step)
        self.batch.refresh_from_db();self.assertEqual(self.batch.revision,1)
        self.assertEqual(self.batch.rows.filter(result={}).count(),2)
