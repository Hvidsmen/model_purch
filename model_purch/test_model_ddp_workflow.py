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

    def test_prices_share_one_model_row_even_when_currency_filtered(self):
        second=ModelDDPRow.objects.create(calculation=self.batch,model_ref='A',model_name='Model A',price_type='PURCH_EUR',price_type_ref='eur',price=90,currency='EUR',source_data=self.row.source_data)
        response=self.client.get(self.url+'?currency=EUR')
        page=response.context['page']
        self.assertEqual(page.paginator.count,1)
        self.assertEqual({r.pk for r in page[0].price_variants},{self.row.pk,second.pk})
        self.assertEqual(response.content.decode().count('class="ddp-model-identity"'),1)
        self.assertContains(response,'PURCH_EUR')
        self.assertContains(response,f'name="r{page[0].pk}-volume"',count=1)

    def test_model_edit_updates_every_price_without_changing_prices(self):
        second=ModelDDPRow.objects.create(calculation=self.batch,model_ref='A',model_name='Model A',price_type_ref='eur',price=90,currency='EUR',source_data=self.row.source_data)
        mutate(self.batch.pk,1,'rows_save',rows={self.row.pk:{'volume':'2'}})
        self.row.refresh_from_db();second.refresh_from_db();self.other.refresh_from_db()
        self.assertEqual(self.row.overrides,second.overrides)
        self.assertEqual(second.overrides['volume'],'2')
        self.assertEqual(second.price,90)
        self.assertEqual(self.other.overrides,{})

    def test_excel_groups_model_columns_and_preserves_each_price(self):
        from io import BytesIO
        from openpyxl import load_workbook
        ModelDDPRow.objects.create(calculation=self.batch,model_ref='A',model_name='Model A',price_type_ref='eur',price=90,currency='EUR',source_data=self.row.source_data)
        response=self.client.get(reverse('model_ddp_export',args=[self.batch.pk]))
        sheet=load_workbook(BytesIO(response.content))['Помодельный DDP']
        self.assertIn('A2:A3',{str(r) for r in sheet.merged_cells.ranges})
        self.assertEqual({sheet.cell(r,6).value for r in (2,3)},{'USD','EUR'})

    def workbook(self,query):
        from io import BytesIO
        from openpyxl import load_workbook
        response=self.client.get(reverse('model_ddp_export',args=[self.batch.pk])+query)
        self.assertEqual(response.status_code,200)
        return load_workbook(BytesIO(response.content))

    def test_excel_keeps_filters_in_download_link_and_both_sheets(self):
        for row in (self.row,self.other):
            row.result={'status':'ready','steps':[{'label':'Step','formula':'1','value':'1','currency':'USD'}]}
            row.save()
        query='?brand=A&step=calculate&page=2'
        response=self.client.get(self.url+query)
        self.assertContains(response,reverse('model_ddp_export',args=[self.batch.pk])+'?brand=A&amp;step=calculate')
        book=self.workbook(query)
        self.assertEqual(book['Помодельный DDP'].max_row,2)
        self.assertEqual(book['Помодельный DDP']['A2'].value,'Model A')
        self.assertEqual(book['Этапы расчёта'].max_row,2)
        self.assertEqual(book['Этапы расчёта']['A2'].value,'Model A')
        self.assertIn(('Фильтр: brand','A'),list(book['Параметры'].values))

    def test_excel_empty_filters_do_not_fall_back_to_entire_calculation(self):
        book=self.workbook('?brand=NoSuchBrand')
        self.assertEqual(book['Помодельный DDP'].max_row,1)
        self.assertEqual(book['Этапы расчёта'].max_row,1)

    def test_excel_currency_filter_selects_model_and_keeps_all_its_prices(self):
        ModelDDPRow.objects.create(calculation=self.batch,model_ref='A',model_name='Model A',price_type_ref='eur',price=90,currency='EUR',source_data=self.row.source_data)
        book=self.workbook('?currency=EUR&page=99')
        sheet=book['Помодельный DDP']
        self.assertEqual(sheet.max_row,3)
        self.assertEqual({sheet.cell(r,6).value for r in (2,3)},{'USD','EUR'})
        self.assertEqual(sheet['A2'].value,'Model A')

    def test_zero_prices_hidden_only_when_same_model_has_nonzero_price(self):
        zero=ModelDDPRow.objects.create(calculation=self.batch,model_ref='A',model_name='Model A',price_type_ref='eur',price_type='ZERO_EUR',price=0,currency='EUR',source_data=self.row.source_data,
            result={'status':'error','steps':[{'label':'Hidden zero','formula':'0','value':'0','currency':'EUR'}]})
        self.other.price=0;self.other.save()
        response=self.client.get(self.url)
        groups={row.model_ref:row for row in response.context['page']}
        self.assertEqual([r.pk for r in groups['A'].price_variants],[self.row.pk])
        self.assertEqual([r.pk for r in groups['B'].price_variants],[self.other.pk])
        self.assertEqual(response.context['stats']['zero'],1)
        self.assertNotContains(response,'ZERO_EUR')
        book=self.workbook('')
        self.assertEqual(book['Помодельный DDP'].max_row,3)
        self.assertNotIn('Hidden zero',[row[3] for row in book['Этапы расчёта'].values])
        zero.refresh_from_db();self.assertEqual(zero.price,0)
        filtered=self.client.get(self.url+'?price_check=zero')
        self.assertEqual([row.model_ref for row in filtered.context['page']],['B'])
        sheet=self.workbook('?price_check=zero')['Помодельный DDP']
        self.assertEqual(sheet.max_row,2)
        self.assertEqual(sheet['A2'].value,'Model B')

    def test_prices_in_another_calculation_do_not_hide_zero(self):
        other_batch=ModelDDPCalculation.objects.create(name='Other',as_of=self.batch.as_of,created_by=self.user)
        ModelDDPRow.objects.create(calculation=other_batch,model_ref='B',price=5,currency='USD')
        self.other.price=0;self.other.save()
        response=self.client.get(self.url+'?price_check=zero')
        self.assertEqual([row.model_ref for row in response.context['page']],['B'])
