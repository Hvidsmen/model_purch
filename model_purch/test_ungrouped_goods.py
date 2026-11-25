from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from unittest.mock import patch
from . import test_goods_uniqueness as fixtures
from .models import PGGoods
from .services.goods_fill import preview
from .services.copying import copy_goods

class UngroupedGoodsTests(TestCase):
    setUp=fixtures.GoodsUniquenessTests.setUp
    fields=fixtures.GoodsUniquenessTests.fields
    workbook=fixtures.GoodsUniquenessTests.workbook
    excel_row=fixtures.GoodsUniquenessTests.excel_row

    def make(self,**changes):
        return PGGoods.objects.create(**self.fields(planning_group='',**changes))

    def test_unnamed_records_remain_distinct_and_can_be_edited(self):
        first=self.make();second=self.make()
        self.assertNotEqual(first.planning_group_key,second.planning_group_key)
        response=self.client.post(reverse('bulk_update_pggoods')+f'?scenario={self.scenario.pk}',{'updates':[{'id':first.pk,'volume':1.5,'exw_usd':12,'weight_kg':1}]},content_type='application/json')
        self.assertEqual(response.json()['updated'],1,response.content)
        first.refresh_from_db();second.refresh_from_db()
        self.assertEqual(first.exw_usd,12);self.assertEqual(second.exw_usd,2)
        self.assertIn(str(first.pk),first.display_name)

    def test_source_code_is_stable_identity_for_unnamed_import(self):
        target=self.make(source_code='ERP-1')
        row=self.excel_row('',pk=None)+[None]*10+['ERP-1']
        response=self.client.post(reverse('import_from_excel')+f'?scenario={self.scenario.pk}',{'excel_file':self.workbook([row])})
        self.assertEqual(response.status_code,302)
        target.refresh_from_db();self.assertEqual(target.planning_sales,'Imported sales')
        self.assertEqual(PGGoods.objects.filter(scenario_plan=self.scenario).count(),1)

    def test_blank_group_import_requires_local_id_or_code(self):
        target=self.make();other=self.make()
        row=self.excel_row('',pk=target.pk)
        self.client.post(reverse('import_from_excel')+f'?scenario={self.scenario.pk}',{'excel_file':self.workbook([row,self.excel_row('',pk=None)])})
        target.refresh_from_db();other.refresh_from_db()
        self.assertEqual(target.planning_sales,'Imported sales');self.assertEqual(other.planning_sales,'Sales')
        self.assertEqual(PGGoods.objects.filter(scenario_plan=self.scenario).count(),2)

    def test_group_based_fill_and_copy_skip_unnamed_records(self):
        good=self.make()
        with patch('model_purch.services.goods_fill.physical_source',return_value={}):
            result=preview(self.scenario,'physical','all',[])
        self.assertFalse(result['updates']);self.assertIn('Нет плановой группы',result['rows'][0]['notes'][0])
        target=PGGoods.objects.create(**self.fields(scenario_plan=self.other,planning_group=''))
        self.assertEqual(copy_goods(self.scenario,self.other),(0,1))
        target.refresh_from_db();self.assertEqual(target.exw_usd,2)

    def test_cross_scenario_id_and_named_id_cannot_match_empty_group(self):
        named=PGGoods.objects.create(**self.fields())
        foreign=PGGoods.objects.create(**self.fields(scenario_plan=self.other,planning_group=''))
        self.client.post(reverse('import_from_excel')+f'?scenario={self.scenario.pk}',{'excel_file':self.workbook([self.excel_row('',pk=named.pk),self.excel_row('',pk=foreign.pk)])})
        named.refresh_from_db();foreign.refresh_from_db()
        self.assertEqual(named.planning_sales,'Sales');self.assertEqual(foreign.planning_sales,'Sales')

    def test_existing_legacy_blank_identity_migrates_without_deleting_data(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from types import SimpleNamespace
        from .goods_identity import planning_group_key
        good=self.make()
        PGGoods.objects.filter(pk=good.pk).update(planning_group_key=planning_group_key(''))
        migration=import_module('model_purch.migrations.0036_ungrouped_goods')
        migration.separate_existing_unnamed(apps,SimpleNamespace(connection=connection))
        good.refresh_from_db();key=good.planning_group_key
        self.assertTrue(key.startswith('ungrouped:'))
        good.save();good.refresh_from_db();self.assertEqual(good.planning_group_key,key)
        self.assertEqual(good.exw_usd,2)

    def test_goods_list_labels_unnamed_and_source_code_can_be_saved(self):
        good=self.make()
        response=self.client.get(reverse('pggoods_list'),{'scenario':self.scenario.pk})
        self.assertContains(response,f'Без плановой группы · ID {good.pk}')
        response=self.client.post(reverse('bulk_update_pggoods')+f'?scenario={self.scenario.pk}',{'updates':[{'id':good.pk,'source_code':'ERP-SAVED'}]},content_type='application/json')
        self.assertEqual(response.json()['updated'],1,response.content)
        good.refresh_from_db();self.assertEqual(good.source_code,'ERP-SAVED')
