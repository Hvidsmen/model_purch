from copy import copy
from decimal import Decimal
from unittest.mock import Mock,patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from base.testing import authorize_test_case
from . import test_goods_uniqueness as fixtures
from .models import PGGoods,Freight,Purch
from .services import goods_fill as fill
from .services.pricing import apply_price

class GoodsFillTests(TestCase):
    fields=fixtures.GoodsUniquenessTests.fields
    def setUp(self):
        fixtures.GoodsUniquenessTests.setUp(self)
        self.good=PGGoods.objects.create(**self.fields(volume=1,exw_usd=100),container_volume=50,duty_rate=.1,weight_kg=2)
        self.freight=Freight.objects.create(scenario=self.scenario,price_per_container=1000,customs_rate=10,warehouse_delivery_cost=200)
        self.scenario.plan_parameters_loaded_at=timezone.now();self.scenario.save()
        PGGoods.objects.filter(pk=self.good.pk).update(plan_ddp_usd=192.7,plan_quantity=10,fact_ddp_usd=300,fact_quantity=20)
        self.good.refresh_from_db()
        self.url=reverse('goods_fill',args=[self.scenario.pk])

    def apply(self,result,source='plan'):
        return fill.apply(self.scenario.pk,{**result,'source':source})

    def test_reverse_matches_forward_with_duty_and_shipping(self):
        value=fill.reverse_exw(self.good,192.7,self.freight,None)
        self.assertAlmostEqual(value,150)
        before=self.good.exw_usd
        result=fill.preview(self.scenario,'plan','all',[])
        self.assertEqual(self.apply(result),1)
        self.good.refresh_from_db()
        self.assertAlmostEqual(self.good.exw_usd,150);self.assertAlmostEqual(self.good.ddp_usd,192.7)
        self.assertEqual((self.good.volume,self.good.weight_kg),(1,2))

    def test_russian_exw_equals_ddp_without_freight(self):
        self.assertEqual(fill.reverse_exw(self.good,100,None,'russian'),100)

    def test_fact_missing_and_visible_modes(self):
        other=PGGoods.objects.create(**self.fields(planning_group='Other',exw_usd=0))
        PGGoods.objects.filter(pk=other.pk).update(fact_ddp_usd=50,fact_quantity=1)
        result=fill.preview(self.scenario,'fact','visible',[self.good.pk])
        self.assertEqual([r['id'] for r in result['updates']],[self.good.pk])
        result=fill.preview(self.scenario,'fact','missing',[])
        self.assertNotIn(self.good.pk,[r['id'] for r in result['updates']])
        self.assertIn(other.pk,[r['id'] for r in result['updates']])
        with self.assertRaises(ValidationError):fill.preview(self.scenario,'fact','visible',[999999])

    def test_ddp_without_quantity_is_used_and_invalid_targets_skip(self):
        PGGoods.objects.filter(pk=self.good.pk).update(plan_quantity=None)
        result=fill.preview(self.scenario,'plan','all',[])
        self.assertEqual(len(result['updates']),1)
        self.assertAlmostEqual(result['updates'][0]['values']['exw_usd'],150)
        PGGoods.objects.filter(pk=self.good.pk).update(plan_quantity=1,plan_ddp_usd=1)
        result=fill.preview(self.scenario,'plan','all',[])
        self.assertFalse(result['updates']);self.assertIn('неотрицательный EXW',result['rows'][0]['notes'][0])

    def test_physical_missing_changes_only_zero_and_preserves_exw(self):
        PGGoods.objects.filter(pk=self.good.pk).update(volume=0)
        with patch.object(fill,'physical_source',return_value={self.good.planning_group_key:{'volume':3,'weight_kg':9}}):
            result=fill.preview(self.scenario,'physical','missing',[])
        self.assertEqual(result['updates'][0]['values'],{'volume':3})
        self.apply(result,'physical');self.good.refresh_from_db()
        self.assertEqual((self.good.volume,self.good.weight_kg,self.good.exw_usd),(3,2,100))

    def test_stale_inputs_and_freight_prevent_application(self):
        result=fill.preview(self.scenario,'plan','all',[])
        self.freight.price_per_container=1200;self.freight.save()
        with self.assertRaises(ValidationError):self.apply(result)
        self.good.refresh_from_db();self.assertEqual(self.good.exw_usd,100)

    def test_erp_reader_uses_available_columns_and_positive_averages(self):
        connection,cursor=Mock(),Mock()
        cursor.fetchall.side_effect=[[('Даичи_ГруппаПланирования',),('ОбъемЧислитель',),('ВесЧислитель',)],[('Daikin SENSIRA',2,3)]]
        rows=fill.physical_source(lambda *args:(connection,cursor))
        self.assertEqual(rows[self.good.planning_group_key],{'volume':2,'weight_kg':3})
        self.assertIn('AVG(CASE WHEN TRY_CONVERT',cursor.execute.call_args[0][0])
        connection.close.assert_called_once()

    def test_http_preview_confirmation_and_apply(self):
        data={'source':'plan','mode':'all','action':'preview'}
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['updates'])
        token=response.context['token']
        response=self.client.post(self.url,{**data,'action':'apply','token':token})
        self.assertContains(response,'Подтвердите замену')
        response=self.client.post(self.url,{**data,'action':'apply','token':token,'confirm':'yes'})
        self.assertEqual(response.status_code,302)
        self.good.refresh_from_db();self.assertAlmostEqual(self.good.exw_usd,150)

    def test_foreign_ids_and_modified_goods_are_rejected(self):
        other=PGGoods.objects.create(**self.fields(scenario_plan=self.other))
        with self.assertRaises(ValidationError):fill.preview(self.scenario,'plan','visible',[other.pk])
        result=fill.preview(self.scenario,'plan','all',[])
        PGGoods.objects.filter(pk=self.good.pk).update(volume=2)
        with self.assertRaises(ValidationError):self.apply(result)

    def test_missing_physical_source_retains_existing_values(self):
        with patch.object(fill,'physical_source',return_value={self.good.planning_group_key:{'volume':0,'weight_kg':None}}):
            result=fill.preview(self.scenario,'physical','all',[])
        self.assertFalse(result['updates']);self.assertEqual(len(result['rows'][0]['notes']),2)

    def test_unauthorized_user_cannot_preview_or_apply(self):
        from django.contrib.auth.models import User
        self.client.force_login(User.objects.create_user('cannot-fill'))
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.client.post(self.url,{'source':'plan','action':'apply'}).status_code,403)

    def test_fact_ddp_without_quantity_is_used_and_missing_ddp_is_clear(self):
        PGGoods.objects.filter(pk=self.good.pk).update(fact_quantity=None)
        result=fill.preview(self.scenario,'fact','all',[])
        self.assertEqual(len(result['updates']),1)
        self.assertAlmostEqual(result['rows'][0]['ddp_after'],300)
        PGGoods.objects.filter(pk=self.good.pk).update(fact_ddp_usd=None)
        result=fill.preview(self.scenario,'fact','all',[])
        self.assertFalse(result['updates'])
        self.assertIn('не загружен',result['rows'][0]['notes'][0])
