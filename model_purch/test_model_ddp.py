from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import load_workbook
from base.testing import authorize_admin
from base.models import UserProjectAccess
from base.access import ANALYST
from .models import ModelDDPCalculation, ModelDDPRow, ScenarioModel, Freight
from .services.model_ddp import calculate, mutate, preview_token
from .services.model_ddp_source import records, build_sql, load_prices, PRICES, GOODS, diagnostic_text
from .model_ddp_forms import RowForm
from .goods_identity import planning_group_key

PARAMS = dict(price_per_container='1000', customs_rate='10', warehouse_delivery_cost='500',
              foreign_delivery_cost='2000', nr_customs_vat_rate='20',
              container_volume='50', duty_percent='10', supply_type='p2')
RATES = dict(USD='100', EUR='125', CNY='10', RUB='1')


class CalculationTests(SimpleTestCase):
    def batch(self, **kwargs):
        return SimpleNamespace(parameters={**PARAMS, **kwargs}, rates=RATES.copy())

    def row(self, **kwargs):
        return SimpleNamespace(**dict(dict(price=Decimal('100'), currency='USD', planning_group='G', brand='B',
                                          source_data={'volume':'1'}, overrides={}), **kwargs))

    def test_existing_formula_and_all_currencies(self):
        result = calculate(self.batch(), self.row())
        self.assertEqual(result['status'], 'ready')
        steps={step['label']:step for step in result['steps']}
        self.assertEqual(Decimal(steps['CIF']['value']),Decimal('120'))
        self.assertEqual(Decimal(steps['Таможенный платёж']['value']),Decimal('13.2'))
        self.assertIn('1000',steps['Фрахт']['formula'])
        self.assertEqual(Decimal(steps['DDP RUB']['value']),Decimal('14320'))
        # CIF=120; customs=120*.1*1.1=13.2; warehouse=10.
        self.assertEqual(Decimal(result['ddp']['USD']), Decimal('143.2'))
        self.assertEqual(Decimal(result['ddp']['EUR']), Decimal('114.56'))
        self.assertEqual(Decimal(result['ddp']['CNY']), Decimal('1432'))
        self.assertEqual(Decimal(result['ddp']['RUB']), Decimal('14320'))
        self.assertEqual(Decimal(result['components']['foreign_delivery_usd']), Decimal('40'))
        self.assertEqual(Decimal(result['components']['nr_customs_vat_usd']), Decimal('2.64'))

    def test_equivalent_currency_and_packaged_price(self):
        for currency, price in [('EUR', '80'), ('CNY', '1000'), ('RUB', '10000')]:
            with self.subTest(currency=currency):
                r = calculate(self.batch(), self.row(currency=currency, price=Decimal(price)))
                self.assertEqual(Decimal(r['ddp']['USD']), Decimal('143.2'))
        row = self.row(price=Decimal('1200'), source_data={'volume':'1','unit':'упаковка'})
        self.assertEqual(calculate(self.batch(),row)['status'], 'error')
        row.overrides = {'price_quantity':'12','price_unit_confirmed':True}
        self.assertEqual(Decimal(calculate(self.batch(),row)['ddp']['USD']), Decimal('143.2'))

    def test_missing_inputs_not_zero(self):
        for changed in [{'volume':None}, {'volume':'0'}, {'volume':'NaN'}]:
            self.assertEqual(calculate(self.batch(), self.row(source_data=changed))['status'], 'error')
        for changed in [{'container_volume':None}, {'duty_percent':None}, {'supply_type':''}, {'customs_rate':'101'}]:
            self.assertEqual(calculate(self.batch(**changed), self.row())['status'], 'error')
        self.assertEqual(calculate(self.batch(), self.row(price=None))['status'],'error')
        self.assertEqual(calculate(self.batch(), self.row(currency='JPY'))['status'],'error')

    def test_russian_and_missing_rate(self):
        b = self.batch(supply_type='russian')
        self.assertEqual(Decimal(calculate(b,self.row(source_data={}))['ddp']['USD']),100)
        b.rates={}
        result=calculate(b,self.row())
        self.assertEqual(result['status'],'partial')
        self.assertIsNone(result['ddp']['RUB'])
        self.assertEqual(calculate(b,self.row(currency='EUR'))['status'],'error')

    def test_group_and_manual_override_precedence(self):
        b=self.batch(groups={planning_group_key('G'):{'container_volume':'100','duty_percent':'0','supply_type':'direct'}})
        self.assertEqual(Decimal(calculate(b,self.row())['ddp']['USD']),126)
        self.assertEqual(Decimal(calculate(b,self.row(overrides={'supply_type':'russian'}))['ddp']['USD']),100)

    def test_model_logistics_override_does_not_change_other_models(self):
        b=self.batch()
        r=self.row(overrides={'price_per_container':'2000','warehouse_delivery_cost':'0'})
        self.assertEqual(Decimal(calculate(b,r)['ddp']['USD']),Decimal('155.4'))
        self.assertEqual(Decimal(calculate(b,self.row())['ddp']['USD']),Decimal('143.2'))

    def test_high_precision_erp_volume_remains_editable(self):
        f=RowForm({'volume':'0.123456789123','container_volume':'65','duty_percent':'10','supply_type':'direct'})
        self.assertTrue(f.is_valid(),f.errors)


class SourceTests(SimpleTestCase):
    def raw(self, **kwargs):
        data=dict(ref='m',name='Model',group='G',brand='B',volume='1',weight='2',
                  kind_ref='k',kind_name='PURCH USD',currency='840',price='100',
                  at=datetime(2026,11,20),unit='')
        data.update(kwargs)
        return tuple(data.values())

    def test_currency_missing_price_duplicates_and_characteristics(self):
        self.assertEqual(records([self.raw()])[0]['currency'],'USD')
        self.assertEqual(len(records([self.raw(),self.raw()])),1)
        result=records([self.raw(),self.raw(price='101')])[0]
        self.assertIsNone(result['price'])
        self.assertIn('Несколько',result['source_data']['error'])
        self.assertIsNone(records([self.raw(price=None)])[0]['price'])
        self.assertIn('характеристики',records([self.raw()+('variant',)])[0]['source_data']['error'])

    def test_binary_erp_references_are_hex_not_utf16(self):
        from .services.model_ddp_source import KINDS, CURRENCY_TABLES
        schema={
            GOODS:{'Ссылка':'binary','Наименование':'nvarchar','Даичи_ГруппаПланирования':'nvarchar','Марка':'varbinary'},
            PRICES:{'Номенклатура':'binary','ВидЦены':'binary','Валюта':'binary','Период':'datetime','Цена':'numeric','Единица':'binary','Характеристика':'binary'},
            KINDS[0]:{'Ссылка':'binary','Наименование':'nvarchar'},
            CURRENCY_TABLES[0]:{'Ссылка':'binary','Код':'varchar'}}
        sql=build_sql(schema)
        self.assertIn('CONVERT(varchar(max),p.[Номенклатура],2)',sql)
        self.assertIn('CONVERT(varchar(max),n.[Ссылка],2)',sql)
        self.assertIn('CONVERT(varchar(max),p.[Характеристика],2)',sql)
        self.assertIn('CONVERT(varchar(max),p.[Единица],2)',sql)
        self.assertIn('CONVERT(varchar(max),n.[Марка],2)',sql)
        self.assertNotIn('CAST(p.[ВидЦены] AS nvarchar',sql)
        self.assertIn('CAST(k.[Наименование] AS nvarchar(500))',sql)
        raw=self.raw()+('00000000000000000000000000000000',)
        self.assertNotIn('error',records([raw])[0]['source_data'])

    def test_diagnostics_redact_secrets(self):
        message=diagnostic_text(RuntimeError("PWD={secret;pass};UID=alice;SERVER=private;DATABASE=db; Login failed for user 'alice'"))
        for secret in ('secret','pass}','alice','private','db;'):
            self.assertNotIn(secret,message)
        self.assertIn('[скрыто]',message)

    def test_connection_failure_has_stage_id_state_and_safe_message(self):
        with patch('model_purch.services.model_ddp_source.connect_database',side_effect=RuntimeError('08001','PWD=secret;SERVER=private;network unreachable')):
            with self.assertLogs('model_purch.services.model_ddp_source',level='ERROR') as logs:
                with self.assertRaises(ValidationError) as caught:load_prices(date(2026,11,22))
        message=str(caught.exception)
        self.assertIn('подключение к DataWH',message)
        self.assertIn('SQLSTATE: 08001',message)
        self.assertIn('Код диагностики:',message)
        self.assertNotIn('secret',message)
        self.assertNotIn('private',message)
        self.assertNotIn('secret',' '.join(logs.output))

    def test_missing_metadata_explains_permissions_and_closes_resources(self):
        from unittest.mock import MagicMock
        connection,cursor=MagicMock(),MagicMock()
        cursor.fetchall.return_value=[]
        with patch('model_purch.services.model_ddp_source.connect_database',return_value=(connection,cursor)):
            with self.assertLogs('model_purch.services.model_ddp_source',level='ERROR'):
                with self.assertRaises(ValidationError) as caught:load_prices(date(2026,11,22))
        self.assertIn('подготовка запроса PURCH',str(caught.exception))
        self.assertIn('нет доступа',str(caught.exception))
        cursor.close.assert_called_once()
        connection.close.assert_called_once()

    def test_schema_required_and_parameterized_cutoff(self):
        with self.assertRaises(ValidationError):build_sql({})
        schema={GOODS:{'Ссылка':'varchar','Наименование':'varchar'},PRICES:{
            'Номенклатура':'varchar','ВидЦены':'varchar','Валюта':'varchar','Период':'datetime','Цена':'numeric'}}
        sql=build_sql(schema)
        self.assertIn('DENSE_RANK()',sql)
        self.assertIn(' < ? ',sql)
        self.assertIn(' LIKE ?',sql)
        self.assertNotIn('PURCH%',sql)
        connection=SimpleNamespace(timeout=0,close=lambda:None)
        from unittest.mock import MagicMock
        cursor=MagicMock()
        cursor.fetchall.side_effect=[
            [(table,col,kind) for table,cols in schema.items() for col,kind in cols.items()],
            [self.raw()+('',)]]
        with patch('model_purch.services.model_ddp_source.connect_database',return_value=(connection,cursor)):
            self.assertEqual(len(load_prices(date(2026,11,22))),1)
        self.assertEqual(cursor.execute.call_args.args[1:],(date(2026,11,23),'PURCH%'))
        cursor.close.assert_called_once()


class SnapshotTests(TestCase):
    def setUp(self):
        self.user=authorize_admin(self.client)
        self.batch=ModelDDPCalculation.objects.create(name='DDP',as_of=date(2026,11,22),created_by=self.user,parameters=PARAMS,rates=RATES)
        self.row=ModelDDPRow.objects.create(calculation=self.batch,model_ref='m',model_name='Model',brand='B',
                      price_type_ref='k',price_type='PURCH USD',currency='USD',price=100,source_data={'volume':'1'})
        self.url=reverse('model_ddp_detail',args=[self.batch.pk])

    def test_calculate_persists_and_stale_write_rejected(self):
        mutate(self.batch.pk,1,'calculate')
        self.row.refresh_from_db()
        self.assertEqual(self.row.result['status'],'ready')
        with self.assertRaises(ValidationError):mutate(self.batch.pk,1,'calculate')
        self.assertEqual(self.client.get(self.url).status_code,200)

    def test_reload_preserves_overrides_and_invalidates_result(self):
        self.row.overrides={'volume':'2'}
        self.row.save()
        record=records([('m','Model','G','B','1','2','k','PURCH USD','USD','200',datetime(2026,11,22),'')])[0]
        mutate(self.batch.pk,1,'load',rows=[record])
        row=self.batch.rows.get()
        self.assertEqual(row.overrides,{'volume':'2'})
        self.assertEqual(row.result,{})
        self.assertEqual(row.price,200)

    def test_copy_is_reviewed_snapshot_not_live_reference(self):
        source=ScenarioModel.objects.create(name='Source',date_start_plan=date(2027,1,1),date_end_plan=date(2027,12,31))
        freight=Freight.objects.create(scenario=source,price_per_container=1000,customs_rate=10)
        data,token=preview_token(self.batch,source,self.user)
        freight.price_per_container=9000
        freight.save()
        mutate(self.batch.pk,1,'copy',user=self.user,token=token)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.parameters['price_per_container'],'1000.00')
        self.assertEqual(self.batch.source_scenario,source)
        with self.assertRaises(ValidationError):mutate(self.batch.pk,2,'copy',user=self.user,token=token)

    def test_model_metadata_and_costs_are_saved_independently(self):
        mutate(self.batch.pk,1,'rows',rows={self.row.pk:{
            'purch':'Model supplier','planning_sales':'RAC','weight_kg':'12',
            'stock_cnt_day':'90','percent_stock_end':'25','price_per_container':'2000'}})
        self.row.refresh_from_db()
        self.assertEqual(self.row.overrides['purch'],'Model supplier')
        self.assertEqual(self.row.overrides['stock_cnt_day'],'90')
        self.assertEqual(Decimal(self.row.result['ddp']['USD']),Decimal('165.4'))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.parameters['price_per_container'],'1000')

    def test_failed_load_does_not_change_saved_rows(self):
        with patch('model_purch.views_model_ddp.load_prices',side_effect=ValidationError('Unavailable')):
            response=self.client.post(self.url,{'action':'load','revision':1})
        self.assertContains(response,'Unavailable')
        self.assertEqual(self.batch.rows.count(),1)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.revision,1)

    def test_other_batch_row_is_rejected(self):
        b=ModelDDPCalculation.objects.create(name='Other',as_of=self.batch.as_of,created_by=self.user)
        r=ModelDDPRow.objects.create(calculation=b,model_ref='x',model_name='X')
        response=self.client.post(self.url,{'action':'rows','revision':1,'rows':'['+str(r.pk)+']'})
        self.assertContains(response,'не принадлежит')
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.revision,1)

    def test_export_formula_escape_and_filters(self):
        mutate(self.batch.pk,1,'calculate')
        self.row.model_name='=1+1'
        self.row.save(update_fields=['model_name'])
        response=self.client.get(reverse('model_ddp_export',args=[self.batch.pk]))
        self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(response.content))
        self.assertEqual(book['Помодельный DDP']['A2'].value,"'=1+1")
        self.assertGreater(book['Этапы расчёта'].max_row,5)
        self.assertEqual(self.client.get(self.url,{'status':'ready','brand':'B'}).context['page'].paginator.count,1)
        self.assertEqual(self.client.get(self.url,{'status':'error'}).context['page'].paginator.count,0)

    def test_analyst_read_only_and_no_role_denied(self):
        user=User.objects.create_user('viewer')
        UserProjectAccess.objects.create(user=user,project='purchases',role=ANALYST)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.client.get(reverse('model_ddp_export',args=[self.batch.pk])).status_code,200)
        self.assertEqual(self.client.post(self.url,{'action':'calculate','revision':1}).status_code,403)
        outsider=User.objects.create_user('outsider')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.url).status_code,403)
